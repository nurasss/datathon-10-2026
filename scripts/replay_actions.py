#!/usr/bin/env python3
"""Replay cached detections/states for targeted action development, NO inference."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for extra in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / extra))
from vision.config import PipelineConfig
from vision.types import Detection, FrameObservation, ZoneSpec
from vision.tracking import track_people
from vision.triggers import build_triggers, workplace_model, at_workplace
from vision.scene_state import SceneState, DeskItem
from vision.rule_engine import evaluate_rules
from vision.oracle import load_annotations
from vision.evaluation import score_actions, action_summary, type_comparison
from rules.registry import RuleRegistry


def replay(source, cfg):
    data = json.loads(source.read_text())
    w, h = data["quality"].get("frame_size", (464, 848))
    frames = data["quality"]["trace"]
    observations = []
    for idx, f in enumerate(frames):
        raw = f.get("tracking_detections", f["detections"])
        detections = [Detection(d["box"][0]*w, d["box"][1]*h, d["box"][2]*w, d["box"][3]*h, d["score"])
                      for d in raw if d["cls"] == "person"]
        if "tracking_detections" not in f:
            detections.extend(Detection(p["bbox"][0]*w, p["bbox"][1]*h, p["bbox"][2]*w, p["bbox"][3]*h, p["score"])
                              for p in f["persons"] if p["score"] < cfg.detector.min_score)
        observations.append(FrameObservation(f["t"], idx, detections))
    zones = [ZoneSpec(**z) for z in data["quality"]["zones"] if z["name"] != "door"]
    x1, y1, x2, y2 = cfg.zones.door_rect
    door = ZoneSpec("door", x1*w, y1*h, x2*w, y2*h)
    tracks, assignments = track_people(observations, zones[0], door, w, h, cfg.triggers)
    triggers = build_triggers(observations, tracks, assignments, zones[0], w, h, cfg=cfg.triggers, door=door)
    model = workplace_model(tracks, zones[0], w, h, cfg.triggers)
    presence = {}
    for obs in observations:
        cashier = [d for tr, d in assignments.get(obs.frame_index, []) if tr.role == "cashier"]
        presence[obs.t] = {"cashier_present": bool(cashier), "cashier_seated": any(at_workplace(d, model, h, cfg.triggers, seated=True) for d in cashier)}
        safe_box = data["quality"].get("scene_context", {}).get("safe_box")
        margin = cfg.triggers.safe_supervision_margin_frac
        presence[obs.t]["cashier_near_safe"] = (any(d.x2/w >= safe_box[0]-margin and d.x1/w <= safe_box[2]+margin and
            d.y2/h >= safe_box[1]-margin and d.y1/h <= safe_box[3]+margin for d in cashier) if safe_box else None)
    scenes = []
    for frame in frames:
        if frame.get("scene") is not None:
            s = dict(frame["scene"])
            s["desk_items"] = [DeskItem(**d) for d in s["desk_items"]]
            scenes.append(SceneState(**s))
    events = evaluate_rules(triggers, scenes, presence, data["duration_sec"], RuleRegistry.load(), cfg.rules)
    return {"video": data["video"], "source": str(source), "method": "cached-state replay, not an end-to-end evaluation",
            "low_score_detections_complete": all("tracking_detections" in f for f in frames),
            "workplace_model": model, "zones": [z.as_dict() for z in zones], "triggers": [t.as_dict() for t in triggers],
            "tracks": [{"id": tr.track_id, "role": tr.role, "aliases": tr.source_track_ids,
                        "path": [{"t": t, "box": [d.x1/w, d.y1/h, d.x2/w, d.y2/h], "score": d.score,
                                  "at_workplace": at_workplace(d, model, h, cfg.triggers),
                                  "seated": at_workplace(d, model, h, cfg.triggers, seated=True)} for t, _, d in tr.history]}
                       for tr in tracks],
            "events": [{"code": e.event_code, "t": e.start_sec, "meta": e.meta} for e in events]}, events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "artifacts/task4/baseline/trace")
    parser.add_argument("--only", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/inference.default.json")
    args = parser.parse_args()
    cfg = PipelineConfig.load(args.config)
    annotations = load_annotations(ROOT / "data/annotations/events.csv")
    args.out.mkdir(parents=True, exist_ok=True)
    actions, outputs = [], []
    for video in args.only.split(","):
        output, events = replay(args.source / f"{video}.json", cfg)
        output.update(type_comparison(events, annotations.get(video, [])))
        action = score_actions(video, output["triggers"], annotations.get(video, []))
        if action:
            actions.append(action)
        outputs.append(output)
        (args.out / f"{video}.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
        print(video, [(t["kind"], round(t["t"], 2)) for t in output["triggers"]], [(e.event_code, round(e.start_sec, 2)) for e in events], flush=True)
    (args.out / "summary.json").write_text(json.dumps({"config": cfg.to_dict(), "actions": action_summary(actions),
        "outputs": [{k: row[k] for k in ("video", "events", "type_ok")} for row in outputs]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
