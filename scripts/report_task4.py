#!/usr/bin/env python3
"""Summarize final task-4 artifacts and the saved integration baseline; no inference."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for extra in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / extra))
from vision.oracle import load_annotations, code_for_annotation
from vision.evaluation import score_actions, action_summary


def load(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, default=ROOT / "artifacts/task4/baseline")
    args = parser.parse_args()
    rows = list(csv.DictReader((args.eval_dir / "results.csv").open()))
    baseline_rows = list(csv.DictReader((args.baseline_dir / "results.csv").open()))
    annotations = load_annotations(args.eval_dir / "events_input.csv")
    b_actions = []
    b_type_ok = b_type_hit = b_tp = 0
    for row in baseline_rows:
        name = row["video"].split(" ")[0]
        expected = {code_for_annotation(a) for a in annotations.get(name, [])}-{None}
        predicted = set(filter(None, row["pred_codes"].split(";")))
        if row["outcome"] == "TP":
            b_tp += 1
            b_type_ok += expected == predicted
            b_type_hit += bool(expected & predicted)
        trace = load(args.baseline_dir / "trace" / f"{name}.json")
        action = score_actions(name, trace["quality"]["triggers"], annotations.get(name, []))
        if action:
            action["normal_no_violation"] = action["normal"] and row["pred_verdict"] == "no_violation"
            b_actions.append(action)
    metrics = load(args.eval_dir / "metrics.json")
    policies = load(args.eval_dir / "run.json")["effective_config"]["rules"]["types"]
    baseline = load(args.baseline_dir / "metrics.json")
    case_details = {}
    for name in ("X-16", "X-19", "X-18", "X-38", "X-4"):
        trace = load(args.eval_dir / "trace" / f"{name}.json")
        case_details[name] = {"predicted": [{"code": e["code"], "t": e["start_sec"]} for e in trace["events"]],
            "annotation": [{k: a[k] for k in ("type_code", "start_sec", "label_sec")} for a in annotations.get(name, [])]}
    summary = {"sources": {"evaluation": str(args.eval_dir), "baseline": str(args.baseline_dir)},
        "rows": len(rows), "metrics": metrics["summary"], "baseline_metrics": baseline["summary"],
        "baseline_type_correct_tp": b_type_ok, "baseline_type_correct_fraction_among_tp": b_type_ok/b_tp if b_tp else None,
        "baseline_type_hit_fraction_among_tp": b_type_hit/b_tp if b_tp else None,
        "baseline_triggers": action_summary(b_actions), "triggers": metrics["trigger_accuracy"],
        "runtime_sec": {r["video"]: float(r["runtime_sec"]) for r in rows},
        "max_runtime_sec": max(float(r["runtime_sec"]) for r in rows),
        "false_positives": [r["video"] for r in rows if r["outcome"] == "FP"],
        "false_negatives": [r["video"] for r in rows if r["outcome"] == "FN"], "outsider_cases": case_details,
        "delta": {k: metrics["summary"][k]-baseline["summary"][k] for k in ("tp", "fp", "fn", "tn")}}
    summary["by_type"] = metrics["by_type"]
    summary["normal_controls"] = []
    summary["stages_sec"] = {}
    scene_gates = []
    for row in rows:
        name = row["video"].split(" ")[0]
        path = args.eval_dir / "trace" / f"{name}.json"
        trace = load(path)
        summary["stages_sec"][name] = trace["timings"]
        if name in {"X-12", "X-23", "X-25", "X-27", "X-29", "X-31"}:
            summary["normal_controls"].append({"video": name, "outcome": row["outcome"],
                "pred_codes": row["pred_codes"], "actions": [{"kind": t["kind"], "t": t["t"]}
                for t in trace["quality"]["triggers"] if t["kind"].startswith("cashier_")]})
        for annotation in annotations.get(name, []):
            start, end = float(annotation["start_sec"]), float(annotation["end_sec"])
            scenes = [f["scene"] for f in trace["quality"]["trace"] if f.get("scene") and start <= f["t"] <= end]
            if not scenes:
                continue
            scene_gates.append({"video": name, "type": annotation["type_code"], "interval": [start, end],
                "source": str(path), "samples": len(scenes),
                "screen_states": dict(Counter(s["screen_state"] or "unknown" for s in scenes)),
                "max_door_open": max((s["door_open"] for s in scenes if s["door_open"] is not None), default=None),
                "door_threshold": policies["DOOR_OPEN"]["threshold"],
                "door_frames_above_threshold": sum(s["door_open"] is not None and s["door_open"] >= policies["DOOR_OPEN"]["threshold"] for s in scenes),
                "max_safe_open": max((s["safe_open"] for s in scenes if s["safe_open"] is not None), default=None),
                "desk_classes": dict(Counter(i["cls"] for s in scenes for i in s["desk_items"]))})
    oracle = args.eval_dir / "oracle/metrics.json"
    if oracle.exists():
        summary["oracle"] = load(oracle)
    out = ROOT / "artifacts/task4/report"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (out / "scene_gate_audit.json").write_text(json.dumps(scene_gates, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
