#!/usr/bin/env python3
"""Offline task-5 trigger/scene audit. Never runs neural inference."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for package in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / package))
from vision.config import PipelineConfig
from vision.oracle import load_annotations
from vision.rule_engine import evaluate_rules
from vision.scene_state import SceneState, DeskItem
from vision.triggers import Trigger
from rules.registry import RuleRegistry

EXPECTED = {**{f"X-{n}": "cashier_leave" for n in (11,13,15,22,24,26,28,12,23,25,27,29)},
            **{f"X-{n}": "cashier_enter" for n in (32,33,31)},
            **{f"X-{n}": "outsider" for n in (17,34)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "artifacts/task5/baseline")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/task5/audit_baseline")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "trace").mkdir(exist_ok=True)
    manifest = json.loads((args.source / "run.json").read_text())
    cfg = PipelineConfig.from_dict(manifest.get("effective_config", manifest["config"]))
    annotations = load_annotations(args.source / "events_input.csv")
    rows = []
    for file in sorted((args.source / "trace").glob("*.json")):
        data = json.loads(file.read_text())
        frames = data["quality"].get("trace", [])
        scenes, presence = [], {}
        for frame in frames:
            presence[round(frame["t"],4)] = {k: frame.get(k) for k in (
                "cashier_present", "cashier_seated", "cashier_near_safe", "cashier_at_workplace")}
            if frame.get("scene"):
                scene = dict(frame["scene"])
                scene["desk_items"] = [DeskItem(**i) for i in scene["desk_items"]]
                scenes.append(SceneState(**scene))
        actions = [Trigger(**t) for t in data["quality"].get("triggers", [])]
        roles = {p["track_id"]: p["role"] for f in frames for p in f["persons"]}
        decisions = []
        evaluate_rules(actions, scenes, presence, data["duration_sec"], RuleRegistry.load(), cfg.rules,
                       decisions=decisions, track_roles=roles)
        data["rule_decisions"] = decisions
        data["triggers"] = [{"kind": t.kind, "start": t.t, "end": t.end, "track_id": t.track_id,
                             "role": roles.get(t.track_id, "unknown")} for t in actions]
        data["audit_mode"] = "saved states and actions; no neural inference; original verdict preserved"
        (args.out / "trace" / file.name).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        if data["video"] not in EXPECTED:
            continue
        name = data["video"]
        gt = annotations.get(name, [])
        normal = all(a["type_code"] == "NONE" for a in gt)
        expected = None if normal else min(float(a["start_sec"]) for a in gt)
        found = [t.t for t in actions if t.kind == EXPECTED[name]]
        nearest = min(found, key=lambda t: abs(t-expected)) if found and expected is not None else None
        rows.append({"video": name, "expected_trigger": EXPECTED[name], "expected_sec": expected,
            "reference": "NONE has no action time" if normal else "events.csv start_sec",
            "found_sec": ";".join(str(t) for t in found), "nearest_sec": nearest,
            "delta_sec": round(nearest-expected, 3) if nearest is not None else None,
            "verdict": data["verdict"], "reasons": ";".join(sorted({d["rule"]+":"+d["reason"] for d in decisions})),
            "source": str(file)})
    rows.sort(key=lambda r: int(r["video"].split("-")[1]))
    with (args.out / "triggers.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out / "triggers.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
