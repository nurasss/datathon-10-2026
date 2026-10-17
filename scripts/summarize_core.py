#!/usr/bin/env python3
"""Derive report numbers from saved artifacts only; never launch inference."""
import argparse
import csv
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=Path, required=True)
    parser.add_argument("--load", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    args = parser.parse_args()
    rows = list(csv.DictReader((args.eval_dir / "results.csv").open()))
    load = read(args.load)
    metrics = read(args.eval_dir / "metrics.json")
    oracle = read(args.eval_dir / "oracle/metrics.json")
    benchmark = read(args.benchmark)
    clean = [r for r in rows if r["label"] == "no_violation"]
    ev03 = {}
    for name in ("X-18", "X-38", "X-4"):
        trace = read(args.eval_dir / "trace" / f"{name}.json")
        predicted = [e["timecode_sec"] for e in trace["events"] if e["code"] == "OUTSIDER"]
        # Ground truth comes from the frozen annotation CSV, not hardcoded times.
        with (args.eval_dir / "oracle/events_input.csv").open(encoding="utf-8-sig") as f:
            truth = [r for r in csv.DictReader(f) if r["video"].replace("Х", "X").startswith(name)
                     and r["type_code"] in {"UNAUTHORIZED_ENTRY", "OUTSIDER"}]
        if len(truth) != 1:
            raise ValueError(f"ambiguous annotation for {name}")
        label, onset = float(truth[0]["label_sec"]), float(truth[0]["start_sec"])
        ev03[name] = {"predicted_onsets": predicted, "label_sec": label, "annotation_start_sec": onset,
                      "delta_from_label": [t-label for t in predicted], "delta_from_start": [t-onset for t in predicted]}
    durations = {r["video"]: float(r["runtime_sec"]) for r in rows}
    bsummary = benchmark["summary"]
    raw_jobs = load.get("raw_jobs", [])
    intervals = []
    for j in raw_jobs:
        if j.get("started_at") and j.get("finished_at"):
            intervals.extend([(j["started_at"], 1), (j["finished_at"], -1)])
    active, maximum = 0, 0
    for _, delta in sorted(intervals):
        active += delta
        maximum = max(maximum, active)
    summary = {
        "sources": {"eval_dir": str(args.eval_dir), "load": str(args.load), "benchmark": str(args.benchmark),
                    "pytest": "artifacts/tests/pytest_final.txt"},
        "rows": len(rows), "summary": metrics["summary"], "baseline": metrics.get("baseline_comparison"),
        "oracle_engine": oracle["summary"], "oracle_scene_only": oracle["scene_only_observed_actions_summary"],
        "oracle_unsupported": oracle["unsupported_rows"], "ev03": ev03,
        "clean_videos": len(clean), "clean_fp": [r["video"] for r in clean if r["outcome"] == "FP"],
        "clean_ev03_fp": [r["video"] for r in clean if "OUTSIDER" in r["pred_codes"].split(";")],
        "runtime_sec": durations, "over_60_sec": {name: t for name, t in durations.items() if t > 60},
        "maximum_runtime_sec": max(durations.values()), "benchmark": bsummary,
        "benchmark_videos": benchmark["videos"], "frames_per_video": benchmark["frames_per_video"],
        "person_count_lower_than_baseline": {model: [r["video"] for r in benchmark["per_video"]
            if r["counts"][model] < r["counts"]["cv2_416_baseline"]] for model in ("ort_320", "ort_416")},
        "thread_trials_mean_wall_sec": {str(n): sum(t["wall_sec"] for t in benchmark["thread_trials"] if t["threads_per_session"] == n)/
            sum(t["threads_per_session"] == n for t in benchmark["thread_trials"]) for n in (1, 2)},
        "load": {"wall_sec": load["total_wall_sec"], "max_observed_processing_jobs": maximum,
            "results": [{"clip": r["clip"], "status": r["status"], "elapsed_sec": r.get("elapsed_sec"),
                         "verdict": r.get("verdict"), "error": r.get("error")} for r in load["results"]]},
        "docs_status": load.get("docs_status", {}),
    }
    out = ROOT / "artifacts/report"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (out / "git-log.txt").write_text(subprocess.check_output(["git", "log", "--oneline", "-10"], cwd=ROOT, text=True))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
