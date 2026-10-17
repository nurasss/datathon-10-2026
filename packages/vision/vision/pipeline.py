"""End-to-end local analysis pipeline (video -> verdict + evidence events)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from rules.registry import RuleReference, RuleRegistry

from .config import PipelineConfig
from .deskobjects import DeskWatcher
from .detector import MotionProxyDetector, NanoDetPersonDetector, PersonDetector
from .evidence import EvidenceFrame, encode_frame
from .events import build_states, detect_ev01, detect_ev02, merge_events
from .types import AnalysisResult, EventCandidate, FrameObservation, VideoMeta
from .videoprobe import FrameReader, VideoProbeError, probe
from .zones import infer_zones

ProgressCallback = Callable[[str, float], None]


class AnalysisError(RuntimeError):
    """Raised when the clip cannot be analysed; mapped to ``failed`` by the API."""

    def __init__(self, message: str, error_code: str = "ANALYSIS_ERROR"):
        super().__init__(message)
        self.error_code = error_code


@dataclass
class AnalysisOptions:
    job_id: str = ""
    keep_trace: bool = False


def choose_analysis_fps(cfg: PipelineConfig, meta: VideoMeta) -> float:
    duration = max(0.5, meta.duration_sec)
    fps = cfg.sampling.target_frames / duration
    fps = max(cfg.sampling.min_fps, min(cfg.sampling.max_fps, fps))
    if duration <= cfg.sampling.short_clip_duration:
        fps = max(fps, cfg.sampling.short_clip_fps)
    return float(fps)


class VideoAnalyzer:
    """Runs the whole local pipeline for one file.

    The analyzer is intentionally stateless per call so that it can be executed
    inside a worker process without shared mutable state.
    """

    def __init__(
        self,
        cfg: PipelineConfig | None = None,
        detector: PersonDetector | None = None,
        registry: RuleRegistry | None = None,
        project_root: str | Path | None = None,
    ):
        self.cfg = cfg or PipelineConfig.load()
        self.registry = registry or RuleRegistry.load()
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self.detector = detector or self._build_detector()
        self.desk = DeskWatcher(self.cfg.desk)
        #: size of the *decoded* frames (the analysis pass may downscale)
        self._frame_size: tuple[int, int] | None = None

    # -- construction helpers --------------------------------------------------------

    def _build_detector(self) -> PersonDetector:
        model_path = Path(self.cfg.detector.model_path)
        if not model_path.is_absolute():
            model_path = self.project_root / model_path
        return NanoDetPersonDetector(model_path)

    # -- main entry point ------------------------------------------------------------

    def analyze(
        self,
        video_path: str | Path,
        options: AnalysisOptions | None = None,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        options = options or AnalysisOptions()
        path = Path(video_path)
        if not path.is_file():
            raise AnalysisError("файл не найден", "FILE_NOT_FOUND")

        def report(phase: str, pct: float) -> None:
            if progress is not None:
                progress(phase, pct)

        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        report("probe", 0.02)
        try:
            meta = probe(path)
        except VideoProbeError as exc:
            raise AnalysisError(str(exc), "PROBE_FAILED") from exc
        if not meta.is_decodable:
            raise AnalysisError("видео не декодируется", "UNSUPPORTED_VIDEO")
        timings["probe"] = time.perf_counter() - t0

        fps = choose_analysis_fps(self.cfg, meta)
        report("decode", 0.05)

        t1 = time.perf_counter()
        observations, desk_frames, desk_masks, motion_frames = self._pass1(path, meta, fps, report)
        timings["pass1"] = time.perf_counter() - t1
        if not observations:
            raise AnalysisError("не удалось декодировать ни одного кадра", "DECODE_FAILED")

        report("zones", 0.62)
        decoded_w, decoded_h = self._frame_size or (meta.width, meta.height)
        zones = infer_zones(observations, decoded_w, decoded_h, self.cfg.zones,
                            override=self.cfg.post_zone_override)
        post_zone = next(z for z in zones if z.name == "post")
        counter_zone = next(z for z in zones if z.name == "counter")
        # Safety net: if the auto-derived workplace is almost never occupied, the
        # anchor was probably a client rather than the cashier — fall back to the
        # configured default rectangle and report it.
        zone_share = _zone_presence_share(observations, post_zone)
        if (
            self.cfg.post_zone_override is None
            and post_zone.source == "auto"
            and zone_share < self.cfg.zones.min_zone_presence_share
        ):
            fallback = infer_zones(observations, decoded_w, decoded_h, self.cfg.zones,
                                   override=self.cfg.zones.fallback_post_rect)
            zones = fallback
            post_zone = next(z for z in zones if z.name == "post")
            counter_zone = next(z for z in zones if z.name == "counter")
            post_zone.confidence = round(min(post_zone.confidence, 0.25), 3)
            zone_share = _zone_presence_share(observations, post_zone)
        dt = 1.0 / max(fps, 0.1)
        states = build_states(observations, post_zone, counter_zone, dt)
        states_by_index = {s.frame_index: s for s in states}
        observations_by_index = {o.frame_index: o for o in observations}
        quality = {
            "zone_confidence": post_zone.confidence,
            "zone_source": post_zone.source,
            "persons": int(sum(len(o.detections) for o in observations)),
            "frames_with_person": float(
                sum(1 for o in observations if o.detections) / max(1, len(observations))
            ),
            "motion_fallback_frames": motion_frames,
            "zones": [z.as_dict() for z in zones],
        }

        report("reasoning", 0.72)
        self._desk_cache = (desk_frames, desk_masks, post_zone)
        t2 = time.perf_counter()
        events = self._detect_events(observations, states, states_by_index, observations_by_index, quality, fps)
        timings["reasoning"] = time.perf_counter() - t2
        self._desk_cache = None
        events = merge_events(events, self.cfg.merge_gap_sec)
        if self.cfg.prefer_absence_event:
            events = _suppress_contained_events(events)
        events = events[: self.cfg.max_events]
        if options.keep_trace:
            desk_signal = getattr(self, "_desk_signal", {}) or {}
            quality["trace"] = [
                {
                    "t": round(s.t, 2),
                    "post": s.post_occupied,
                    "client": s.client_present,
                    "n_post": s.persons_in_post,
                    "n_counter": s.persons_near_counter,
                    "n_total": s.persons_total,
                    "n_other": s.persons_other,
                    "motion": round(observations_by_index[s.frame_index].motion, 2),
                    "fg": round(desk_signal.get(s.frame_index, {}).get("fg_ratio", 0.0), 4),
                    "blob": round(desk_signal.get(s.frame_index, {}).get("blob_area", 0.0), 5),
                    "post_box_h": round(desk_signal.get(s.frame_index, {}).get("post_box_h", 0.0), 3),
                }
                for s in states
            ]

        report("evidence", 0.85)
        t3 = time.perf_counter()
        reader = FrameReader(path, meta)
        try:
            self._attach_evidence(events, reader)
        finally:
            reader.close()
        timings["evidence"] = time.perf_counter() - t3

        result = AnalysisResult(
            meta=meta,
            events=events,
            warnings=self._warnings(quality, meta),
            zones=zones,
            frames_analyzed=len(observations),
            persons_detected=quality["persons"],
            analysis_fps=fps,
            detector=getattr(self.detector, "name", "unknown"),
            timings=timings,
            quality=quality,
        )
        report("finalize", 0.98)
        return result

    # -- passes ----------------------------------------------------------------------

    def _pass1(
        self,
        path: Path,
        meta: VideoMeta,
        fps: float,
        report: ProgressCallback,
    ) -> tuple[list[FrameObservation], list[np.ndarray], list[np.ndarray], int]:
        observations: list[FrameObservation] = []
        desk_frames: list[np.ndarray] = []
        desk_masks: list[np.ndarray] = []
        motion = MotionProxyDetector(self.cfg.detector.motion_min_area_ratio) if self.cfg.detector.motion_fallback else None
        motion_frames = 0
        prev_gray: np.ndarray | None = None
        reader = FrameReader(path, meta, decode_max_width=self.cfg.sampling.decode_max_width)
        try:
            total = max(1, int(meta.duration_sec * fps))
            for i, (t, frame) in enumerate(reader.iter_sampled(fps)):
                if self._frame_size is None:
                    self._frame_size = (int(frame.shape[1]), int(frame.shape[0]))
                dets = self.detector.detect(frame, min_score=self.cfg.detector.min_score)
                if motion is not None and not dets:
                    extra = motion.detect_blobs(frame, dets)
                    if extra:
                        dets = extra
                        motion_frames += 1
                gray_small = self.desk.downscale(frame)
                motion_value = 0.0
                if prev_gray is not None:
                    motion_value = float(np.mean(np.abs(gray_small.astype(np.int16) - prev_gray.astype(np.int16))))
                prev_gray = gray_small
                observations.append(
                    FrameObservation(t=t, frame_index=i, detections=dets, motion=motion_value)
                )
                desk_frames.append(gray_small)
                scale = self.desk.cfg.small_width / max(int(frame.shape[1]), 1)
                desk_masks.append(self.desk.person_masks(dets, gray_small.shape[:2], scale))
                if i % 10 == 0:
                    report("decode", 0.05 + 0.55 * min(1.0, i / total))
        finally:
            reader.close()
        return observations, desk_frames, desk_masks, motion_frames

    def _detect_events(
        self,
        observations: list[FrameObservation],
        states,
        states_by_index,
        observations_by_index,
        quality: dict,
        fps: float,
    ) -> list[EventCandidate]:
        dt = 1.0 / max(fps, 0.1)
        events: list[EventCandidate] = []

        raw_ev01 = detect_ev01(states, self.cfg.ev01, dt, quality)
        rule_ev01 = self.registry.for_event("EV01")
        if rule_ev01 is not None:
            for raw in raw_ev01:
                events.append(self._make_ev01(raw, rule_ev01, observations_by_index))

        if self.cfg.ev02.enabled:
            events.extend(self._ev02_events(states_by_index, observations_by_index, quality, dt))
        return events

    def _ev02_events(self, states_by_index, observations_by_index, quality, dt) -> list[EventCandidate]:
        # The desk watcher needs the downscaled frames; they are attached to the
        # observations during pass 1 through ``_desk_cache`` (see ``analyze``).
        cache = getattr(self, "_desk_cache", None)
        if not cache:
            return []
        frames, masks, post_zone = cache
        background, valid = self.desk.build_background(frames, masks)
        if background is None or valid is None:
            return []
        scale = self.desk.cfg.small_width / max(1, (self._frame_size or (self.desk.cfg.small_width, 1))[0])
        per_frame_blobs = {}
        desk_signal: dict[int, dict] = {}
        for idx, gray in enumerate(frames):
            per_frame_blobs[idx] = self.desk.detect_blobs(
                gray, background, valid, masks[idx], post_zone, idx, scale
            )
            desk_signal[idx] = {
                "fg_ratio": self.desk.foreground_ratio(gray, background, valid, masks[idx], post_zone, scale),
                "blob_area": float(sum(b.area for b in per_frame_blobs[idx])),
                "post_box_h": self._post_box_height(observations_by_index.get(idx), post_zone),
            }
        self._desk_signal = desk_signal
        blobs = self.desk.track_blobs(per_frame_blobs, list(observations_by_index.values()))
        raw = detect_ev02(blobs, observations_by_index, states_by_index, self.cfg.ev02, dt)
        rule = self.registry.for_event("EV02")
        if rule is None:
            return []
        return [self._make_ev02(item, rule) for item in raw]

    @staticmethod
    def _post_box_height(obs, post_zone) -> float:
        """Max person-box height (share of the zone height) inside the workplace.

        A seated cashier produces a short box; standing up makes it taller —
        a cheap "is the cashier actually at the post" proxy for traces.
        """

        if obs is None or not obs.detections:
            return 0.0
        zone_h = max(1.0, post_zone.y2 - post_zone.y1)
        heights = [
            det.height / zone_h
            for det in obs.detections
            if post_zone.contains(*det.center)
        ]
        return max(heights, default=0.0)

    # -- event materialisation -------------------------------------------------------

    def _make_ev01(self, raw: dict, rule: RuleReference, observations_by_index) -> EventCandidate:
        start = float(raw["start"])
        peak = float(raw["peak"])
        end = float(raw["end"])
        duration = float(raw["absence_duration"])
        client_ref = float(raw["client_ref"])
        if raw["variant"] == "service_in_progress":
            observation = (
                f"Рабочее место кассового работника остаётся не занятым {duration:.1f} с "
                f"(с {start:.1f} с по {end:.1f} с), при этом клиент находится у кассы "
                f"(клиент виден у кассы с {client_ref:.1f} с)."
            )
            hypothesis = (
                "Видео показывает оставление рабочего места при незавершённом, по видимым признакам, "
                "обслуживании клиента. Это может указывать на несоблюдение требования: "
                f"{rule.full_citation()} По записи не устанавливается, завершено ли обслуживание "
                "документально, кто именно является кассовым работником и была ли служебная "
                "необходимость отойти. Результат — подсказка для проверки человеком."
            )
        else:
            observation = (
                f"Клиент подходит к кассе в {peak:.1f} с, при этом рабочее место кассового работника "
                f"не занято: отсутствие длится {duration:.1f} с (с {start:.1f} с по {end:.1f} с)."
            )
            hypothesis = (
                "Видео показывает, что клиент оказался у кассы при незанятом рабочем месте. "
                "Это может указывать на несоблюдение требования: "
                f"{rule.full_citation()} По записи не устанавливается, продолжалось ли обслуживание "
                "и по какой причине рабочее место не занято. Результат — подсказка для проверки человеком."
            )
        times = _dedupe_times([max(0.0, peak - 0.8), peak, min(end, peak + 1.6)], 0.3)
        return EventCandidate(
            event_code="EV01",
            event_label=rule.event_label,
            start_sec=start,
            peak_sec=peak,
            end_sec=end,
            rule_id=rule.rule_id,
            observation=observation,
            hypothesis=hypothesis,
            evidence_times=times,
            confidence=float(raw["confidence"]),
            meta={"variant": raw["variant"], "client_ref": client_ref},
        )

    def _make_ev02(self, raw: dict, rule: RuleReference) -> EventCandidate:
        start = float(raw["start"])
        unattended = float(raw["peak"])
        end = float(raw["end"])
        observation = (
            f"На открытой поверхности рабочего места остаётся статичный предмет "
            f"(с {start:.1f} с, к моменту {unattended:.1f} с рабочее место не занято, "
            f"предмет виден до {end:.1f} с)."
        )
        hypothesis = (
            "Видео показывает, что после ухода с рабочего места на открытой поверхности остаётся предмет; "
            "ранее принятые наличные деньги должны храниться в устройствах, закрывающихся на ключ: "
            f"{rule.full_citation()} По записи не устанавливается, что это именно наличные деньги или "
            "ценности и что предмет не помещён в закрывающееся устройство за пределами кадра. "
            "Результат — подсказка для проверки человеком."
        )
        times = _dedupe_times([start, unattended, end], 0.3)
        return EventCandidate(
            event_code="EV02",
            event_label=rule.event_label,
            start_sec=start,
            peak_sec=unattended,
            end_sec=end,
            rule_id=rule.rule_id,
            observation=observation,
            hypothesis=hypothesis,
            evidence_times=times,
            confidence=float(raw["confidence"]),
            meta={"persist_sec": raw["persist_sec"], "bbox": raw["bbox"]},
        )

    # -- evidence --------------------------------------------------------------------

    def _attach_evidence(self, events: list[EventCandidate], reader: FrameReader) -> None:
        for event in events:
            frames: list[EvidenceFrame] = []
            for t in event.evidence_times[: self.cfg.evidence.max_frames_per_event]:
                frame = reader.read_frame_at(t)
                if frame is None:
                    continue
                try:
                    frames.append(
                        encode_frame(
                            frame,
                            t,
                            max_width=self.cfg.evidence.max_width,
                            quality=self.cfg.evidence.jpeg_quality,
                        )
                    )
                except RuntimeError:
                    continue
            event.meta["evidence"] = frames

    # -- diagnostics -----------------------------------------------------------------

    def _warnings(self, quality: dict, meta: VideoMeta) -> list[str]:
        warnings: list[str] = []
        if quality.get("zone_source") == "fallback":
            warnings.append("zone_fallback: зона рабочего места взята по умолчанию")
        if quality.get("zone_confidence", 0) < 0.3:
            warnings.append("low_zone_confidence")
        if quality.get("persons", 0) == 0:
            warnings.append("no_person_detected")
        if meta.variable_frame_rate:
            warnings.append("variable_frame_rate")
        return warnings


def _zone_presence_share(observations: list[FrameObservation], zone) -> float:
    """Share of frames in which a tracked person centre lies inside ``zone``."""

    if not observations:
        return 0.0
    seen = 0
    for obs in observations:
        for det in obs.detections:
            if det.source == "motion_blob":
                continue
            cx, cy = det.center
            if zone.contains(cx, cy):
                seen += 1
                break
    return seen / len(observations)


def _dedupe_times(times: list[float], min_gap: float) -> list[float]:
    out: list[float] = []
    for t in times:
        if all(abs(t - o) >= min_gap for o in out):
            out.append(round(float(t), 2))
    return out


def _suppress_contained_events(events: list[EventCandidate]) -> list[EventCandidate]:
    """Drop an EV02 event that describes the same episode as an EV01 event.

    Leaving the workplace and having an object on the surface at the same moment
    is one visible episode; reporting both would double-count it.
    """

    ev01 = [e for e in events if e.event_code == "EV01"]
    result: list[EventCandidate] = []
    for event in events:
        if event.event_code != "EV02":
            result.append(event)
            continue
        contained = any(
            other.start_sec - 1.0 <= event.peak_sec <= other.end_sec + 1.0 for other in ev01
        )
        if not contained:
            result.append(event)
    return result


def analyze_file(
    video_path: str | Path,
    cfg: PipelineConfig | None = None,
    registry: RuleRegistry | None = None,
    progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """Convenience helper used by tests and scripts."""

    analyzer = VideoAnalyzer(cfg=cfg, registry=registry)
    return analyzer.analyze(video_path, progress=progress)
