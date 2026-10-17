#!/usr/bin/env python3
"""Stage-by-stage profiling for ForteVision video analysis.

Measures:
- Per-stage latency:
    1. Probe / metadata
    2. Video decoding (extracting sampled frames)
    3. Person detector (NanoDet inference)
    4. Tracking & desk state analysis (zones + build_states + desk watcher)
    5. Rule reasoning (EV01 + EV02 evaluation & merging)
    6. Evidence attachment (seeking & encoding evidence frames)
- Total analyzed frames
- Detector latency per frame (ms/frame)
- Peak RSS RAM usage (MB)
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / extra))

import cv2
import numpy as np

from rules.registry import RuleRegistry
from vision.config import PipelineConfig
from vision.detector import MotionProxyDetector, NanoDetPersonDetector
from vision.events import build_states, detect_ev01, detect_ev02, merge_events
from vision.pipeline import (
    VideoAnalyzer,
    choose_analysis_fps,
    _zone_presence_share,
    _suppress_contained_events,
)
from vision.videoprobe import FrameReader, probe
from vision.zones import infer_zones


def get_peak_ram_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return round(rss / (1024 * 1024), 2)
    return round(rss / 1024, 2)


def profile_video(video_path: Path, config_path: Path) -> dict:
    cfg = PipelineConfig.load(config_path)
    registry = RuleRegistry.load()
    analyzer = VideoAnalyzer(cfg=cfg, registry=registry, project_root=ROOT)

    t_start = time.perf_counter()

    # 1. Probe
    t0 = time.perf_counter()
    meta = probe(video_path)
    probe_time = time.perf_counter() - t0

    fps = choose_analysis_fps(cfg, meta)

    # 2. Frame decoding + Detector + Desk/Tracking preprocessing
    # We instrument pass 1 to cleanly separate decoding time and detector time
    reader = FrameReader(video_path, meta, decode_max_width=cfg.sampling.decode_max_width)
    motion = MotionProxyDetector(cfg.detector.motion_min_area_ratio) if cfg.detector.motion_fallback else None

    decode_time = 0.0
    detector_time = 0.0
    tracking_prep_time = 0.0

    observations = []
    desk_frames = []
    desk_masks = []
    motion_frames = 0
    prev_gray = None

    t_iter = time.perf_counter()
    gen = reader.iter_sampled(fps)
    i = 0
    while True:
        t_dec_start = time.perf_counter()
        try:
            item = next(gen)
        except StopIteration:
            break
        t, frame = item
        decode_time += time.perf_counter() - t_dec_start

        if analyzer._frame_size is None:
            analyzer._frame_size = (int(frame.shape[1]), int(frame.shape[0]))

        # Detector
        t_det_start = time.perf_counter()
        dets = analyzer.detector.detect(frame, min_score=cfg.detector.min_score)
        detector_time += time.perf_counter() - t_det_start

        # Tracking prep (motion + desk)
        t_track_start = time.perf_counter()
        if motion is not None and not dets:
            extra = motion.detect_blobs(frame, dets)
            if extra:
                dets = extra
                motion_frames += 1
        gray_small = analyzer.desk.downscale(frame)
        motion_value = 0.0
        if prev_gray is not None:
            motion_value = float(np.mean(np.abs(gray_small.astype(np.int16) - prev_gray.astype(np.int16))))
        prev_gray = gray_small
        from vision.types import FrameObservation
        observations.append(FrameObservation(t=t, frame_index=i, detections=dets, motion=motion_value))
        desk_frames.append(gray_small)
        scale = analyzer.desk.cfg.small_width / max(int(frame.shape[1]), 1)
        desk_masks.append(analyzer.desk.person_masks(dets, gray_small.shape[:2], scale))
        tracking_prep_time += time.perf_counter() - t_track_start
        i += 1

    reader.close()

    # 3. Zones & Tracking (build_states)
    t_track_start = time.perf_counter()
    decoded_w, decoded_h = analyzer._frame_size or (meta.width, meta.height)
    zones = infer_zones(observations, decoded_w, decoded_h, cfg.zones, override=cfg.post_zone_override)
    post_zone = next(z for z in zones if z.name == "post")
    counter_zone = next(z for z in zones if z.name == "counter")
    zone_share = _zone_presence_share(observations, post_zone)
    if (
        cfg.post_zone_override is None
        and post_zone.source == "auto"
        and zone_share < cfg.zones.min_zone_presence_share
    ):
        fallback = infer_zones(observations, decoded_w, decoded_h, cfg.zones, override=cfg.zones.fallback_post_rect)
        zones = fallback
        post_zone = next(z for z in zones if z.name == "post")
        counter_zone = next(z for z in zones if z.name == "counter")
    dt = 1.0 / max(fps, 0.1)
    states = build_states(observations, post_zone, counter_zone, dt)
    states_by_index = {s.frame_index: s for s in states}
    observations_by_index = {o.frame_index: o for o in observations}
    tracking_time = (time.perf_counter() - t_track_start) + tracking_prep_time

    # 4. Rules & reasoning
    t_rules_start = time.perf_counter()
    quality = {
        "zone_confidence": post_zone.confidence,
        "zone_source": post_zone.source,
        "persons": int(sum(len(o.detections) for o in observations)),
        "frames_with_person": float(sum(1 for o in observations if o.detections) / max(1, len(observations))),
        "motion_fallback_frames": motion_frames,
        "zones": [z.as_dict() for z in zones],
    }
    analyzer._desk_cache = (desk_frames, desk_masks, post_zone)
    events = analyzer._detect_events(observations, states, states_by_index, observations_by_index, quality, fps)
    analyzer._desk_cache = None
    events = merge_events(events, cfg.merge_gap_sec)
    if cfg.prefer_absence_event:
        events = _suppress_contained_events(events)
    events = events[: cfg.max_events]
    rules_time = time.perf_counter() - t_rules_start

    # 5. Evidence attachment
    t_ev_start = time.perf_counter()
    reader2 = FrameReader(video_path, meta)
    try:
        analyzer._attach_evidence(events, reader2)
    finally:
        reader2.close()
    evidence_time = time.perf_counter() - t_ev_start

    total_time = time.perf_counter() - t_start
    frames_analyzed = len(observations)
    detector_ms_per_frame = (detector_time / max(1, frames_analyzed)) * 1000.0
    peak_ram_mb = get_peak_ram_mb()

    return {
        "video": video_path.name,
        "duration_sec": round(meta.duration_sec, 2),
        "analysis_fps": round(fps, 2),
        "frames_analyzed": frames_analyzed,
        "stages_sec": {
            "probe": round(probe_time, 3),
            "decode": round(decode_time, 3),
            "detector": round(detector_time, 3),
            "tracking": round(tracking_time, 3),
            "rules": round(rules_time, 3),
            "evidence": round(evidence_time, 3),
        },
        "stages_percent": {
            "probe": round(probe_time / total_time * 100, 1),
            "decode": round(decode_time / total_time * 100, 1),
            "detector": round(detector_time / total_time * 100, 1),
            "tracking": round(tracking_time / total_time * 100, 1),
            "rules": round(rules_time / total_time * 100, 1),
            "evidence": round(evidence_time / total_time * 100, 1),
        },
        "detector_ms_per_frame": round(detector_ms_per_frame, 2),
        "total_runtime_sec": round(total_time, 2),
        "peak_ram_mb": peak_ram_mb,
        "verdict": "violation" if events else "no_violation",
        "events_count": len(events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile ForteVision pipeline stages.")
    parser.add_argument("--video", type=Path, default=ROOT.parent / "data_raw" / "open" / "videos" / "Х-14.mov")
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "inference.default.json")
    args = parser.parse_args()

    print(f"Profiling video: {args.video} ...")
    res = profile_video(args.video, args.config)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
