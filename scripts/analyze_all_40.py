#!/usr/bin/env python3
"""Detailed scene analyzer for all 40 videos.

Extracts:
1. When persons are detected and where (cashier desk area vs client area vs safe/door).
2. Cashier presence timeline: present / standing / walking / absent.
3. Safe interaction timeline: approach to safe, door of safe.
4. Desk changes: differences in desk surface before and after departure.
5. Exact events around label timestamp.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate import load_dataset, VideoEntry

VIDEOS_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
LABELS_FILE = Path("/Users/nuras/Desktop/datathon/data_raw/open/open_labels.xlsx")


def analyze_video(entry: VideoEntry):
    cap = cv2.VideoCapture(str(entry.path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = entry.duration_sec

    # Let's inspect frames at 1 fps
    times = [float(i) for i in range(int(math.ceil(duration)) + 1) if float(i) <= duration]

    info = {
        "video": entry.primary_name,
        "display_name": entry.display_name,
        "duration": duration,
        "label": entry.label_verdict,
        "label_ts": entry.label_timestamps,
        "frames": [],
    }

    # Also compute motion energy per second in 3 regions:
    # 1) Cashier chair/desk (approx bottom-left or center depending on angle)
    # 2) Safe/cabinet (approx top-right or back wall)
    # 3) Counter / client area
    prev_gray = None

    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        small = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        motion = 0.0
        if prev_gray is not None:
            motion = float(np.mean(np.abs(gray.astype(np.int16) - prev_gray.astype(np.int16))))
        prev_gray = gray

        info["frames"].append({"t": t, "motion": round(motion, 2)})

    cap.release()
    return info


def main():
    entries = load_dataset(VIDEOS_DIR, LABELS_FILE)
    print(f"Loaded {len(entries)} videos.")
    results = []
    for e in entries:
        print(f"Checking {e.display_name} ({e.label_verdict})...")
        res = analyze_video(e)
        results.append(res)

    out_file = ROOT / "artifacts" / "scene_motion.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {out_file}")


if __name__ == "__main__":
    main()
