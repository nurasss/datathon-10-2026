#!/usr/bin/env python3
"""Paired, deterministic COCO/person audit, NOT a full pipeline evaluation.

Identical evenly spaced frames from every deduplicated clip. Archives the
baseline implementation, every frame's detections/timing and concurrency trials.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import types

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for extra in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / extra))
from evaluate import load_dataset
from vision.detector import NanoDetPersonDetector, NanoDetConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames-per-video", type=int, default=8)
    parser.add_argument("--baseline-ref", default="1b5c2a9")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--reuse-audit", type=Path, help="Reuse archived paired frames/timings for baseline and ORT320, measure only the sanitized ORT416 export")
    args = parser.parse_args()
    out = args.out_dir or ROOT / "artifacts/benchmark" / f"{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(1)
    source = subprocess.check_output(["git", "show", f"{args.baseline_ref}:packages/vision/vision/detector.py"], cwd=ROOT).decode()
    (out / "baseline_detector.py").write_text(source)
    module = types.ModuleType("vision._baseline_detector")
    sys.modules[module.__name__] = module
    exec(compile(source, str(out / "baseline_detector.py"), "exec"), module.__dict__)
    model = ROOT / "packages/vision/models/nanodet.onnx"
    detectors = {
        "cv2_416_baseline": module.NanoDetPersonDetector(model),
        "ort_320": NanoDetPersonDetector(model.with_name("nanodet_320.onnx"), NanoDetConfig(input_size=320)),
        "ort_416": NanoDetPersonDetector(model.with_name("nanodet_416.onnx")),
    }
    entries = load_dataset(ROOT.parent / "data_raw/open/videos", ROOT.parent / "data_raw/open/open_labels.xlsx")
    raw, rows, thread_frames = [], [], []
    reused = json.loads((args.reuse_audit / "frames.json").read_text()) if args.reuse_audit else []
    # Warmup excluded from measurement and recorded as methodology.
    warm = np.zeros((848, 464, 3), dtype=np.uint8)
    for detector in detectors.values():
        detector.detect(warm, 0.3)
    for entry in entries:
        cap = cv2.VideoCapture(str(entry.path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
        counts = dict.fromkeys(detectors, 0)
        for t in np.linspace(0, entry.duration_sec-0.2, args.frames_per_video):
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t)*1000)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"decode failed: {entry.primary_name}@{t}")
            if len(thread_frames) < 12:
                thread_frames.append(frame)
            for name, detector in detectors.items():
                archived = next((r for r in reused if r["video"] == entry.primary_name and r["model"] == name and abs(r["t"]-float(t)) < 1e-5), None)
                if args.reuse_audit and name != "ort_416":
                    if archived is None:
                        raise RuntimeError("missing paired archive frame")
                    raw.append(dict(archived, reused_from=str(args.reuse_audit / "frames.json")))
                    counts[name] += archived["person_count"]
                    continue
                start = time.perf_counter()
                dets = detector.detect(frame, 0.3)
                ms = (time.perf_counter()-start)*1000
                counts[name] += len(dets)
                raw.append({"video": entry.primary_name, "sha256": entry.sha256, "t": float(t),
                            "model": name, "ms": ms, "person_count": len(dets),
                            "persons": [{"box": d.box, "score": d.score} for d in dets]})
        cap.release()
        rows.append({"video": entry.primary_name, "counts": counts})
        print(entry.primary_name, counts, flush=True)
        (out / "frames.json").write_text(json.dumps(raw, ensure_ascii=False))
    thread_trials = []
    for threads in (1, 2):
        pair = [NanoDetPersonDetector(model.with_name("nanodet_416.onnx"), NanoDetConfig(intra_op_num_threads=threads)) for _ in range(2)]
        for d in pair:
            d.detect(warm, 0.3)
        def task(detector):
            measurements = []
            for frame in thread_frames:
                start = time.perf_counter()
                detector.detect(frame, 0.3)
                measurements.append((time.perf_counter()-start)*1000)
            return measurements
        for repeat in range(3):
            start = time.perf_counter()
            with ThreadPoolExecutor(2) as pool:
                measurements = list(pool.map(task, pair))
            thread_trials.append({"threads_per_session": threads, "concurrent_sessions": 2, "repeat": repeat,
                                  "wall_sec": time.perf_counter()-start, "per_call_ms": measurements})
    summary = {}
    for name in detectors:
        samples = [r for r in raw if r["model"] == name]
        summary[name] = {"mean_ms": float(np.mean([r["ms"] for r in samples])),
                         "median_ms": float(np.median([r["ms"] for r in samples])), "frames": len(samples),
                         "person_count": sum(r["person_count"] for r in samples)}
    payload = {"method": "paired evenly spaced frames, one warmup excluded, threshold .3; NOT exhaustive recall",
               "reused_audit": str(args.reuse_audit) if args.reuse_audit else None,
               "frames_per_video": args.frames_per_video, "videos": len(entries), "baseline_ref": args.baseline_ref,
               "platform": platform.platform(), "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
               "summary": summary, "per_video": rows, "thread_trials": thread_trials}
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({"artifact": str(out / "summary.json"), "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
