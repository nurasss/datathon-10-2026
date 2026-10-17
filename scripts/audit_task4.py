#!/usr/bin/env python3
"""Verify saved task-4 evidence, source freeze and environment without inference."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
import onnxruntime

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=Path, required=True)
    args = parser.parse_args()
    directory = args.eval_dir
    manifest = json.loads((directory / "run.json").read_text())
    rows = list(csv.DictReader((directory / "results.csv").open()))
    changed = [name for name, sha in manifest["source_sha256"].items() if digest(ROOT / name) != sha]
    traces = []
    failures = []
    images = 0
    events = 0
    no_pre_recording_frame = []
    for file in sorted((directory / "trace").glob("*.json")):
        trace = json.loads(file.read_text())
        traces.append(trace["video"])
        if trace["error"]:
            failures.append(f"analysis error: {trace['video']}")
        for event in trace["events"]:
            events += 1
            frames = event["evidence_frames"]
            times = [f["timestamp_sec"] for f in frames]
            if len(frames) != 3 or times != event["evidence_times"] or times[1] != event["start_sec"]:
                failures.append(f"wrong evidence/onset: {trace['video']} {event['code']}")
            if not 0 <= event["start_sec"] <= event["end_sec"] <= trace["duration_sec"]+.05:
                failures.append(f"event bounds: {trace['video']} {event['code']}")
            if event["timecode_sec"] != event["start_sec"]:
                failures.append(f"midpoint timestamp: {trace['video']} {event['code']}")
            trigger = event["meta"].get("trigger")
            if trigger is not None and trigger["t"] != event["start_sec"]:
                failures.append(f"trigger timestamp differs: {trace['video']} {event['code']}")
            if event["start_sec"] == 0:
                no_pre_recording_frame.append({"video": trace["video"], "code": event["code"]})
            for frame in frames:
                path = directory / frame["path"]
                image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
                images += 1
                if digest(path) != frame["sha256"] or image is None:
                    failures.append(f"invalid JPEG: {path}")
                elif [image.shape[1], image.shape[0]] != [frame["width"], frame["height"]]:
                    failures.append(f"wrong JPEG dimensions: {path}")
                if not 0 <= frame["timestamp_sec"] <= trace["duration_sec"]:
                    failures.append(f"JPEG timestamp bounds: {path}")
    baseline = ROOT / "artifacts/task4/baseline"
    baseline_hashes = {name: digest(baseline / name) for name in ("metrics.json", "results.csv", "run.json")}
    scene_path = "packages/vision/vision/scene_state.py"
    integration_head = json.loads((ROOT / "artifacts/task4/runtime.json").read_text())["integration_head"]
    integrated_scene = subprocess.check_output(["git", "show", f"{integration_head}:{scene_path}"], cwd=ROOT)
    scene_equal = hashlib.sha256(integrated_scene).hexdigest() == digest(ROOT / scene_path)
    marker = json.loads((ROOT / "artifacts/task4/full_run.json").read_text())
    required = {"type_ok", "type_hit", "label_codes"}
    if not rows or not required.issubset(rows[0]):
        failures.append("missing typed CSV columns")
    if changed:
        failures.append("inference source changed after final evaluate started")
    if len(rows) != 40 or len(rows) != len(manifest["dataset"]) or set(traces) != {x["video"] for x in manifest["dataset"]}:
        failures.append("CSV/trace/dataset mismatch")
    if not scene_equal:
        failures.append("scene_state.py differs from integrated scene")
    if marker["invocations"] != 1 or marker.get("returncode") != 0:
        failures.append("full-run invocation/completion marker invalid")
    result = {"evaluation": str(directory), "csv_rows": len(rows), "traces": len(traces),
              "events": events, "jpeg_images_checked": images, "failures": failures,
              "source_changed": changed, "scene_state_equal_to_integ": scene_equal,
              "source_git_head": manifest["git_head"], "full_run_marker": marker,
              "baseline_hashes": baseline_hashes, "annotation_sha256": digest(directory / "events_input.csv"),
              "onsets_at_recording_start_cannot_have_earlier_frame": no_pre_recording_frame,
              "python": sys.executable, "onnxruntime_version": onnxruntime.__version__,
              "onnxruntime_file": onnxruntime.__file__, "uses_runtime_overlay": ".runtime" in onnxruntime.__file__}
    out = ROOT / "artifacts/task4/report"
    out.mkdir(parents=True, exist_ok=True)
    (out / "integrity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
