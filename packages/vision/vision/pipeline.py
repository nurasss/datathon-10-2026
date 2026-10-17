"""Coarse scan -> candidate windows -> dense scene/tracking/rules -> evidence."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from rules.registry import RuleRegistry
from . import scene_state
from .config import PipelineConfig
from .detector import NanoDetConfig, NanoDetPersonDetector, PersonDetector
from .evidence import encode_frame
from .rule_engine import evaluate_rules
from .tracking import track_people
from .triggers import at_workplace, build_triggers, workplace_model
from .types import AnalysisResult, FrameObservation, VideoMeta, ZoneSpec
from .videoprobe import FrameReader, VideoProbeError, probe
from .zones import infer_zones

ProgressCallback = Callable[[str, float], None]
DESK_CLASSES = {"cup", "bottle", "cell phone", "handbag", "backpack", "book"}


class AnalysisError(RuntimeError):
    def __init__(self, message: str, error_code: str = "ANALYSIS_ERROR"):
        super().__init__(message)
        self.error_code = error_code


@dataclass
class AnalysisOptions:
    job_id: str = ""
    keep_trace: bool = False


def choose_analysis_fps(cfg: PipelineConfig, meta: VideoMeta) -> float:
    return min(meta.fps, max(1.5, min(2.0, cfg.sampling.coarse_fps)))


def candidate_windows(observations, post, duration, cfg, frame_height=None):
    # Always inspect the initial desk state; later windows are action-driven.
    marks = [0.0]
    previous = None
    signatures = []
    for obs in observations:
        people = [d for d in obs.detections if d.score >= 0.3 and (frame_height is None or d.height >= frame_height*.16)]
        signatures.append((len(people), sum(post.contains(*d.center) for d in people)))
        signature = tuple(int(np.median([s[k] for s in signatures[-3:]])) for k in (0, 1))
        if previous is not None:
            prev_obs, prev_signature = previous
            changed = signature != prev_signature
            if not changed and people and prev_obs.detections:
                # Unmatched or moving person, including standing up / door entry.
                changed = any(max((d.iou(p) for p in prev_obs.detections), default=0) < 0.35 for d in people)
            if changed or obs.motion > cfg.motion_threshold:
                marks.append(obs.t)
        previous = obs, signature
    windows = []
    for t in marks:
        start, end = max(0.0, t-cfg.window_padding_sec), min(duration, t+cfg.window_padding_sec)
        if windows and start <= windows[-1][1] + 1/cfg.dense_fps:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return windows


def normalized_detection(d, width, height):
    return {"cls": d.label, "score": float(d.score),
            "box": [float(d.x1/width), float(d.y1/height), float(d.x2/width), float(d.y2/height)]}


class VideoAnalyzer:
    def __init__(self, cfg: PipelineConfig | None = None, detector: PersonDetector | None = None,
                 registry: RuleRegistry | None = None, project_root: str | Path | None = None):
        self.cfg = cfg or PipelineConfig.load()
        self.registry = registry or RuleRegistry.load()
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        cv2.setNumThreads(1)
        if detector is None:
            path = Path(self.cfg.detector.model_path)
            if not path.is_absolute():
                path = self.project_root / path
            dc = self.cfg.detector
            detector = NanoDetPersonDetector(path, NanoDetConfig(input_size=dc.input_size,
                prob_threshold=dc.min_score, nms_threshold=dc.nms_threshold,
                intra_op_num_threads=dc.intra_op_num_threads))
        self.detector = detector
        self.coarse_detector = detector
        dc = self.cfg.detector
        if isinstance(detector, NanoDetPersonDetector) and dc.coarse_input_size != dc.input_size:
            path = Path(dc.coarse_model_path)
            if not path.is_absolute():
                path = self.project_root / path
            self.coarse_detector = NanoDetPersonDetector(path, NanoDetConfig(input_size=dc.coarse_input_size,
                prob_threshold=dc.min_score, nms_threshold=dc.nms_threshold, intra_op_num_threads=dc.intra_op_num_threads))

    def analyze(self, video_path, options: AnalysisOptions | None = None, progress=None):
        options = options or AnalysisOptions()
        path = Path(video_path)
        if not path.is_file():
            raise AnalysisError("файл не найден", "FILE_NOT_FOUND")
        report = progress or (lambda phase, pct: None)
        timings = {}
        started = time.perf_counter()
        try:
            meta = probe(path)
        except VideoProbeError as exc:
            raise AnalysisError(str(exc), "PROBE_FAILED") from exc
        timings["probe"] = time.perf_counter()-started
        fps = choose_analysis_fps(self.cfg, meta)
        coarse, all_detections = {}, {}
        inference_sec = 0.0
        calls = 0
        frame_size = None

        def detect(t, frame, phase):
            nonlocal inference_sec, calls, frame_size
            h, w = frame.shape[:2]
            frame_size = (w, h)
            t0 = time.perf_counter()
            active_detector = self.coarse_detector if phase == "coarse" else self.detector
            if hasattr(active_detector, "detect_all"):
                dets = active_detector.detect_all(frame, self.cfg.detector.track_low_score)
            else:
                dets = active_detector.detect(frame, self.cfg.detector.min_score)
            inference_sec += time.perf_counter()-t0
            calls += 1
            key = round(t, 4)
            all_detections[key] = (dets, phase)
            people = [d for d in dets if d.label == "person"]
            return FrameObservation(key, 0, people)

        report("coarse", 0.05)
        t0 = time.perf_counter()
        previous = None
        # Decode the compressed source once. The second logical pass reuses a
        # bounded JPEG frame cache, but runs detection/scene analysis ONLY inside
        # windows. Overflow discards the cache and falls back to window decoding.
        frame_cache = {}
        cache_bytes = 0
        cache_enabled = self.cfg.sampling.cache_max_mb > 0
        next_coarse = 0.0
        with FrameReader(path, meta) as reader:
            dense_fps = self.cfg.sampling.dense_fps
            for t, frame in reader.iter_sampled(dense_fps):
                if cache_enabled:
                    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    if not ok:
                        raise AnalysisError("не удалось сохранить кадр прохода", "FRAME_CACHE_ERROR")
                    frame_cache[round(t, 4)] = data
                    cache_bytes += data.nbytes
                    if cache_bytes > self.cfg.sampling.cache_max_mb * 1024**2:
                        frame_cache.clear()
                        cache_enabled = False
                if t < next_coarse - 0.5/dense_fps - 1e-6:
                    continue
                next_coarse += 1/fps
                obs = detect(t, frame, "coarse")
                gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (96, 96))
                if previous is not None:
                    obs.motion = float(np.mean(np.abs(gray.astype(np.int16)-previous)))
                previous = gray.astype(np.int16)
                coarse[obs.t] = obs
                if len(coarse) % 12 == 0:
                    report("coarse", 0.05+0.35*t/meta.duration_sec)
        if not coarse:
            raise AnalysisError("не удалось декодировать ни одного кадра", "DECODE_FAILED")
        timings["coarse"] = time.perf_counter()-t0
        w, h = frame_size
        for i, obs in enumerate(coarse.values()):
            obs.frame_index = i
        # Zones are inferred once from the uniform scan, never from event labels.
        zones = infer_zones(list(coarse.values()), w, h, self.cfg.zones, self.cfg.post_zone_override)
        post = zones[0]
        dx1, dy1, dx2, dy2 = self.cfg.zones.door_rect
        door = ZoneSpec("door", w*dx1, h*dy1, w*dx2, h*dy2, 0.3, "geometry_prior")
        zones.append(door)
        coarse_tracks, coarse_assignments = track_people(list(coarse.values()), post, door, w, h, self.cfg.triggers)
        window_observations = [FrameObservation(o.t, o.frame_index,
            [d for _, d in coarse_assignments.get(o.frame_index, [])], motion=o.motion) for o in coarse.values()]
        windows = candidate_windows(window_observations, post, meta.duration_sec, self.cfg.sampling, h)
        coarse_actions = build_triggers(list(coarse.values()), coarse_tracks, coarse_assignments, post, w, h,
                                       cfg=self.cfg.triggers, door=door)
        padding = self.cfg.sampling.window_padding_sec
        for action in coarse_actions:
            if action.kind in {"cashier_leave", "cashier_enter", "person_enter"}:
                windows.append([max(0, action.t-padding), min(meta.duration_sec, action.t+padding)])
                confirm = action.facts.get("confirmed_at")
                if confirm is not None:
                    windows.append([max(0, confirm-padding), min(meta.duration_sec, confirm+padding)])
        merged_windows = []
        for a, b in sorted(windows):
            if merged_windows and a <= merged_windows[-1][1]:
                merged_windows[-1][1] = max(merged_windows[-1][1], b)
            else:
                merged_windows.append([a, b])
        windows = merged_windows
        observations = dict(coarse)
        scene_states = []
        dense_keys = set()
        batch_frames, batch_times, batch_detections = [], [], []
        context = {"zones": [z.as_dict() for z in zones], "bbox_format": "normalized_xyxy"}

        def scene_batch():
            if not batch_frames:
                return
            if "ref_frame" not in context:
                # The scene team evaluates against one clip reference. Resetting
                # it every batch makes an open door become the new 'closed' view.
                from .scene.desk import DeskItemDetector
                from .scene.door import DoorStateDetector
                from .scene.safe import SafeStateDetector
                reference = batch_frames[0]
                context["ref_frame"] = reference
                full_dets = [normalized_detection(d, w, h) for ds, _ in all_detections.values()
                             for d in ds if d.score >= self.cfg.detector.min_score]
                context["desk_box"] = DeskItemDetector().localize_desk(reference, full_dets)
                context["door_box"] = DoorStateDetector().localize_door(reference)
                context["safe_box"] = SafeStateDetector().localize_safe(reference, context["desk_box"])
                laptops = [d["box"] for d in full_dets if d["cls"] == "laptop"]
                if laptops:
                    laptop = tuple(float(v) for v in np.median(laptops, axis=0))
                    context["stationary_boxes"] = [laptop]
                    # The foreground meeting table is often the highest-score
                    # COCO table. The cashier's surface is anchored by equipment,
                    # not by that unrelated foreground rectangle or a person box.
                    zc = self.cfg.zones
                    context["desk_box"] = (max(0, laptop[0]-zc.desk_laptop_side_margin),
                        max(0, laptop[1]-zc.desk_laptop_top_margin),
                        min(1, laptop[2]+zc.desk_laptop_side_margin),
                        min(1, laptop[3]+zc.desk_laptop_bottom_margin))
            states = scene_state.analyze_scene(batch_frames, batch_times, batch_detections, context=context)
            if len(states) != len(batch_times):
                raise AnalysisError("scene_state вернул неверное число состояний", "SCENE_CONTRACT_ERROR")
            if any(abs(s.t-t) > 1e-3 for s, t in zip(states, batch_times)):
                raise AnalysisError("scene_state вернул неверные таймкоды", "SCENE_CONTRACT_ERROR")
            scene_states.extend(states)
            batch_frames.clear()
            batch_times.clear()
            batch_detections.clear()

        t0 = time.perf_counter()
        def window_frames(reader, start, end):
            if cache_enabled:
                for key, data in frame_cache.items():
                    if start-1e-4 <= key <= end+1e-4:
                        yield key, cv2.imdecode(data, cv2.IMREAD_COLOR)
            else:
                yield from reader.iter_window(start, end, self.cfg.sampling.dense_fps)
        with FrameReader(path, meta) as reader:
            for start, end in windows:
                for t, frame in window_frames(reader, start, end):
                    key = round(t, 4)
                    if key in dense_keys:
                        continue
                    dense_keys.add(key)
                    if key not in observations or self.coarse_detector is not self.detector:
                        observations[key] = detect(key, frame, "dense")
                    else:
                        dets, _ = all_detections[key]
                        all_detections[key] = dets, "dense_reused"
                    dets, _ = all_detections[key]
                    batch_frames.append(frame)
                    batch_times.append(key)
                    # ALL COCO classes, not a person-only filtered list.
                    batch_detections.append([normalized_detection(d, w, h) for d in dets if d.score >= self.cfg.detector.min_score])
                    if len(batch_frames) >= 16:
                        scene_batch()
                report("dense", 0.4+0.35*end/meta.duration_sec)
        scene_batch()
        timings["dense_scene"] = time.perf_counter()-t0
        frame_cache.clear()
        # Replace the coarse sample inside a window with the dense grid. This
        # prevents near-duplicate timestamps biasing tracking and temporal holds.
        observations = sorted([obs for key, obs in observations.items() if key in dense_keys or
            not any(a <= key <= b for a, b in windows)], key=lambda o: o.t)
        for i, obs in enumerate(observations):
            obs.frame_index = i
        t0 = time.perf_counter()
        tracks, assignments = track_people(observations, post, door, w, h, self.cfg.triggers)
        triggers = build_triggers(observations, tracks, assignments, post, w, h, cfg=self.cfg.triggers, door=door)
        trace, presence = [], {}
        scenes_at = {round(s.t, 4): s for s in scene_states}
        seat = workplace_model(tracks, post, w, h, self.cfg.triggers)
        for obs in observations:
            people = assignments.get(obs.frame_index, [])
            cashier = [d for tr, d in people if tr.role == "cashier"]
            p = {"cashier_present": bool(cashier), "cashier_seated": any(at_workplace(d, seat, h, self.cfg.triggers, seated=True) for d in cashier),
                 "cashier_at_workplace": any(at_workplace(d, seat, h, self.cfg.triggers) for d in cashier)}
            safe_box = context.get("safe_box")
            p["cashier_near_safe"] = None
            if safe_box is not None:
                margin = self.cfg.triggers.safe_supervision_margin_frac
                p["cashier_near_safe"] = any(
                    d.x2/w >= safe_box[0]-margin and d.x1/w <= safe_box[2]+margin and
                    d.y2/h >= safe_box[1]-margin and d.y1/h <= safe_box[3]+margin for d in cashier)
            presence[obs.t] = p
            if options.keep_trace:
                dets, phase = all_detections[obs.t]
                norm = [normalized_detection(d, w, h) for d in dets if d.score >= self.cfg.detector.min_score]
                trace.append({"t": obs.t, "pass": phase, **p,
                    "persons": [{"track_id": tr.track_id, "bbox": normalized_detection(d, w, h)["box"],
                                 "role": tr.role, "score": d.score} for tr, d in people],
                     "detections": norm,
                     "tracking_detections": [normalized_detection(d, w, h) for d in dets if d.label == "person"],
                    "desk_objects": [d for d in norm if d["cls"] in DESK_CLASSES and post.contains((d["box"][0]+d["box"][2])*w/2, (d["box"][1]+d["box"][3])*h/2)],
                    "scene": asdict(scenes_at[obs.t]) if obs.t in scenes_at else None})
        events = evaluate_rules(triggers, scene_states, presence, meta.duration_sec, self.registry, self.cfg.rules)[:self.cfg.max_events]
        timings["tracking_rules"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        if events:
            with FrameReader(path, meta) as reader:
                self._attach_evidence(events, reader)
        timings["evidence"] = time.perf_counter()-t0
        timings["detector"] = inference_sec
        timings["total"] = time.perf_counter()-started
        high_people = sum(sum(d.score >= self.cfg.detector.min_score for d in obs.detections) for obs in observations)
        quality = {"zone_source": post.source, "zone_confidence": post.confidence, "zones": [z.as_dict() for z in zones],
            "persons": high_people, "frames_with_person": sum(bool(o.detections) for o in observations)/len(observations),
            "windows": windows, "coarse_frames": len(coarse), "dense_frames": len(dense_keys), "inference_calls": calls,
            "detector_ms_per_frame": inference_sec*1000/max(calls, 1), "triggers": [tr.as_dict() for tr in triggers],
            "scene_backend": scene_state.__file__, "scene_samples": len(scene_states), "bbox_format": "normalized_xyxy",
            "door_zone_note": "uncalibrated geometry prior; not a detected door", "scene_stub": all(s.screen_state is None and not s.desk_items and s.safe_open is None and s.door_open is None and s.cashier_hand_counting is None for s in scene_states)}
        quality["coarse_detector"] = self.coarse_detector.name
        quality["dense_detector"] = self.detector.name
        quality["frame_cache"] = {"used": cache_enabled, "bytes": cache_bytes if cache_enabled else 0, "max_mb": self.cfg.sampling.cache_max_mb, "jpeg_quality": 95}
        quality["room_count_prior"] = {"min_person_height_frac": self.cfg.triggers.outsider_min_height_frac,
            "door_crossing": "configured_staff_door_prior_plus_body_and_direction_not_authorization",
            "opposite_client": "seated_history_and_no_new_door_birth"}
        quality["cashier_seated_source"] = "person_box_height_proxy_not_pose_model"
        quality["frame_size"] = [w, h]
        quality["workplace_model"] = seat
        quality["cashier_track_aliases"] = {str(tr.track_id): tr.source_track_ids for tr in tracks if tr.role == "cashier"}
        quality["scene_context"] = {k: v for k, v in context.items() if k != "ref_frame"}
        quality["scene_reference"] = "first_dense_frame_of_clip_not_batch"
        if options.keep_trace:
            quality["trace"] = trace
        warnings = ["door_zone_uncalibrated"]
        if quality["scene_stub"]:
            warnings.append("scene_unknown: scene-dependent rules cannot fire")
        if post.source == "fallback":
            warnings.append("zone_fallback")
        if high_people == 0:
            warnings.append("no_person_detected")
        return AnalysisResult(meta=meta, events=events, warnings=warnings, zones=zones,
            frames_analyzed=len(observations), persons_detected=high_people, analysis_fps=fps,
            detector=self.detector.name, timings=timings, quality=quality)

    def _attach_evidence(self, events, reader):
        timestamps = sorted({t for event in events for t in event.evidence_times[:self.cfg.evidence.max_frames_per_event]})
        originals = reader.read_frames_at(timestamps)
        for event in events:
            frames = []
            for t in event.evidence_times[:self.cfg.evidence.max_frames_per_event]:
                frame = originals.get(t)
                if frame is not None:
                    frames.append(encode_frame(frame, t, max_width=self.cfg.evidence.max_width, quality=self.cfg.evidence.jpeg_quality))
            event.meta["evidence"] = frames


def analyze_file(video_path, cfg=None, registry=None, progress=None):
    return VideoAnalyzer(cfg=cfg, registry=registry).analyze(video_path, progress=progress)
