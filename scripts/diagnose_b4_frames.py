#!/usr/bin/env python3
"""Item 1: Screen state diagnostics on pipeline windows for Task B4."""
import sys
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "vision"))
sys.path.insert(0, str(ROOT / "packages" / "rules"))

from scripts.eval_scene import FastCocoDetector, get_video_file, probe, FrameReader
from vision.scene_state import analyze_scene
from vision.scene.screen import ScreenClassifier
from vision.scene.desk import DeskItemDetector

det = FastCocoDetector(str(ROOT / "packages" / "vision" / "models" / "nanodet_416.onnx"))
screen_clf = ScreenClassifier()
desk_det = DeskItemDetector()

def diagnose_screens():
    clips = [
        ("X-11", 16.0, 24.0, "document"),
        ("X-28", 5.0, 12.0, "document"),
        ("X-22", 12.0, 18.0, "lockscreen"),
        ("X-23", 8.0, 16.0, "lockscreen"),
        ("X-29", 6.0, 14.0, "lockscreen"),
        ("X-25", 24.0, 31.0, "lockscreen"),
    ]

    print("=" * 105)
    print("ITEM 1: SCREEN STATE PER-FRAME EVALUATION ON REAL PIPELINE WINDOWS")
    print("=" * 105)

    all_passed = True
    for name, t_start, t_end, target in clips:
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

        print(f"\n### {name} ({t_start:.1f}..{t_end:.1f}s) | Target: {target}")
        print("-" * 105)
        print(f"{'t (s)':<6} | {'Screen State':<12} | {'Target':<10} | {'Status':<6} | {'color_diff':<10} | {'val_unif':<8} | {'mean_val':<8} | {'white_pct':<9} | {'Person Status / Why'}")
        print("-" * 105)

        for t, f, fd, s in zip(times, frames, frame_dets, states):
            h, w = f.shape[:2]
            # Find current laptop crop features
            laptop_candidates = [
                d["box"] for d in fd
                if d.get("cls") in {"laptop", "tv"} and d.get("score", 0) >= 0.20
            ]
            desk_laptop = None
            for lc in laptop_candidates:
                lcx = (lc[0] + lc[2]) / 2.0
                lcy = (lc[1] + lc[3]) / 2.0
                if 0.35 <= lcx <= 0.75 and 0.28 <= lcy <= 0.60:
                    desk_laptop = lc
                    break

            persons = [d for d in fd if d.get("cls") == "person" and d.get("score", 0) >= 0.30]
            person_desc = "cashier away"
            if persons:
                pb = persons[0]["box"]
                pcx = (pb[0] + pb[2]) / 2.0
                if pb[3] < desk_box[1]:
                    person_desc = "walking out / standing"
                elif pcx < desk_box[0] or pcx > desk_box[2]:
                    person_desc = "person at periphery"
                else:
                    person_desc = "standing at desk"

            # Compute features on screen region
            # (stationary laptop or detected)
            box = desk_laptop
            if box is None:
                # search in init_dets
                for d in init_dets:
                    if d.get("cls") in {"laptop", "tv"}:
                        bcx = (d["box"][0] + d["box"][2]) / 2.0
                        bcy = (d["box"][1] + d["box"][3]) / 2.0
                        if 0.35 <= bcx <= 0.75 and 0.28 <= bcy <= 0.60:
                            box = d["box"]
                            break

            if box:
                x1 = max(0, min(w - 1, int(round(box[0] * w))))
                y1 = max(0, min(h - 1, int(round(box[1] * h))))
                x2 = max(0, min(w, int(round(box[2] * w))))
                y2 = max(0, min(h, int(round(box[3] * h))))
                screen_h = int((y2 - y1) * 0.65)
                margin_x = int((x2 - x1) * 0.06)
                crop = f[y1:y1 + screen_h, x1 + margin_x:x2 - margin_x]
                if crop.size > 0:
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                    b_ch = crop[:, :, 0].astype(float)
                    g_ch = crop[:, :, 1].astype(float)
                    r_ch = crop[:, :, 2].astype(float)
                    cdiff = float(np.mean(np.abs(r_ch - g_ch)) + np.mean(np.abs(r_ch - b_ch)) + np.mean(np.abs(g_ch - b_ch)))
                    sh = crop.shape[0]
                    vunif = abs(float(np.mean(gray[:sh // 2, :])) - float(np.mean(gray[sh // 2:, :])))
                    mval = float(np.mean(gray))
                    v_ch = hsv[:, :, 2]
                    s_ch = hsv[:, :, 1]
                    w_mask = (v_ch > 165) & (s_ch < 60)
                    wratio = float(np.count_nonzero(w_mask)) / float(max(1, gray.size))
                else:
                    cdiff, vunif, mval, wratio = 0.0, 0.0, 0.0, 0.0
            else:
                cdiff, vunif, mval, wratio = 0.0, 0.0, 0.0, 0.0

            ok = (s.screen_state == target)
            status = "PASS" if ok else "FAIL"
            if not ok:
                all_passed = False

            if s.screen_state == "document":
                why = f"{person_desc}; active spreadsheet (cdiff={cdiff:.1f}>=27, mval={mval:.1f}>=98, vunif={vunif:.1f}<=25)"
            elif s.screen_state == "lockscreen":
                why = f"{person_desc}; wallpaper/lockscreen (cdiff={cdiff:.1f}<27 or vunif={vunif:.1f}>25)"
            else:
                why = f"{person_desc}; screen {s.screen_state}"

            print(f"{t:<6.1f} | {s.screen_state or 'None':<12} | {target:<10} | {status:<6} | {cdiff:<10.1f} | {vunif:<8.1f} | {mval:<8.1f} | {wratio * 100:<8.1f}% | {why}")

    print("\n" + "=" * 105)
    print(f"ITEM 1 OVERALL: {'ALL PASSED (100% accurate)' if all_passed else 'FAILURES DETECTED'}")
    print("=" * 105)

if __name__ == "__main__":
    diagnose_screens()
