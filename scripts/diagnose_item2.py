#!/usr/bin/env python3
"""Item 2: Signals evaluation for door_open and desk_items cash/cash_bag on Task B4."""
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "vision"))
sys.path.insert(0, str(ROOT / "packages" / "rules"))

from scripts.eval_scene import FastCocoDetector, get_video_file, probe, FrameReader
from vision.scene_state import analyze_scene
from vision.scene.desk import DeskItemDetector

det = FastCocoDetector(str(ROOT / "packages" / "vision" / "models" / "nanodet_416.onnx"))
desk_det = DeskItemDetector()

def diagnose_item2():
    print("=" * 105)
    print("ITEM 2A: DOOR_OPEN EVALUATION ON CASHIER ENTRANCE WINDOWS")
    print("=" * 105)

    door_clips = [
        ("X-32", 3.0, 15.0, "violation (door open)"),
        ("X-33", 4.0, 14.0, "violation (door open)"),
        ("X-31", 0.0, 12.0, "norm (door closed)"),
    ]

    for name, t_start, t_end, target in door_clips:
        vf = get_video_file(name)
        meta = probe(vf)
        with FrameReader(vf, meta) as r:
            ref = r.read_frame_at(0.5)
            times = list(np.arange(t_start, t_end + 0.01, 1.0))
            frames = [r.read_frame_at(t) for t in times]

        init_dets = det.detect(ref, min_score=0.20)
        desk_box = desk_det.localize_desk(ref, init_dets)
        frame_dets = [det.detect(f, min_score=0.20) for f in frames]
        ctx = {"ref_frame": ref, "desk_box": desk_box}
        states = analyze_scene(frames, times, frame_dets, context=ctx)

        print(f"\n### {name} ({t_start:.1f}..{t_end:.1f}s) | Expected: {target}")
        print("-" * 105)
        print(f"{'t (s)':<6} | {'door_open':<10} | {'Threshold':<10} | {'Signal Status':<15} | {'Physical State / Description'}")
        print("-" * 105)

        for t, s in zip(times, states):
            score = s.door_open
            is_open = score is not None and score >= 0.80
            if "violation" in target:
                signal_ok = "PASS (positive)" if is_open else ("TRANSIT" if t < 5.0 else "FAIL (silent)")
            else:
                signal_ok = "PASS (negative)" if not is_open else "FAIL (false pos)"

            if score is not None and score >= 0.80:
                desc = f"Door left wide open / sustained opening gap (score={score:.3f})"
            elif score is not None:
                desc = f"Door closed / normal frame boundary (score={score:.3f})"
            else:
                desc = "Door unlocalized"

            print(f"{t:<6.1f} | {score if score is not None else 'None':<10} | {'>= 0.80':<10} | {signal_ok:<15} | {desc}")

    print("\n" + "=" * 105)
    print("ITEM 2B: DESK_ITEMS (cash / cash_bag) EVALUATION ON LEAVE WINDOWS")
    print("=" * 105)

    desk_clips = [
        ("X-15", 12.0, 20.0, "violation (cash_bag)"),
        ("X-26", 7.0, 15.0, "violation (cash)"),
        ("X-4", 66.0, 72.0, "violation (cash)"),
        ("X-23", 8.0, 16.0, "norm (clean desk)"),
        ("X-25", 24.0, 31.0, "norm (clean desk)"),
        ("X-27", 5.0, 12.0, "norm (clean desk)"),
    ]

    for name, t_start, t_end, target in desk_clips:
        vf = get_video_file(name)
        meta = probe(vf)
        with FrameReader(vf, meta) as r:
            ref = r.read_frame_at(0.5)
            times = list(np.arange(t_start, t_end + 0.01, 1.0))
            frames = [r.read_frame_at(t) for t in times]

        init_dets = det.detect(ref, min_score=0.20)
        desk_box = desk_det.localize_desk(ref, init_dets)
        frame_dets = [det.detect(f, min_score=0.20) for f in frames]
        ctx = {"ref_frame": ref, "desk_box": desk_box, "initial_detections": init_dets}
        states = analyze_scene(frames, times, frame_dets, context=ctx)

        print(f"\n### {name} ({t_start:.1f}..{t_end:.1f}s) | Expected: {target}")
        print("-" * 105)
        print(f"{'t (s)':<6} | {'Detected Items':<26} | {'Target Class':<14} | {'Signal Status':<15} | {'Observation Details'}")
        print("-" * 105)

        for t, s in zip(times, states):
            val_items = [i for i in s.desk_items if i.cls in ("cash", "cash_bag")]
            items_str = ", ".join(f"{i.cls}({i.score:.2f})" for i in val_items) if val_items else "none"
            
            if "violation" in target:
                target_cls = "cash_bag" if "cash_bag" in target else "cash"
                has_target = any(i.cls == target_cls for i in val_items)
                signal_ok = "PASS (positive)" if has_target else ("TRANSIT" if t < 15.0 and name == "X-15" else "FAIL")
                desc = f"Valuable detected on desk: {items_str}" if val_items else "Cashier leaving / preparing"
            else:
                target_cls = "none"
                signal_ok = "PASS (negative)" if not val_items else "FAIL (false pos)"
                desc = "Desk clean, no valuables reported" if not val_items else f"False positive items: {items_str}"

            print(f"{t:<6.1f} | {items_str:<26} | {target_cls:<14} | {signal_ok:<15} | {desc}")

if __name__ == "__main__":
    diagnose_item2()
