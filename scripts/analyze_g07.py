#!/usr/bin/env python3
"""Detailed pixel and bounding box analysis of G07 videos.
G07: X-24, X-25, X-26, X-27, X-28, X-29.
"""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path

VIDEOS_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
CROPS_DIR = Path("/Users/nuras/Desktop/datathon/fv-scene/artifacts/crops")


def get_video_path(num: int) -> Path:
    candidates = list(VIDEOS_DIR.glob(f"*{num}.mov")) + list(VIDEOS_DIR.glob(f"*{num}.MOV"))
    for c in candidates:
        stem = c.stem.replace("х", "x").replace("Х", "X")
        if stem.endswith(f"-{num}") or stem.endswith(f"X{num}"):
            return c
    return candidates[0]


def analyze_g07_video(num: int, label_sec: float | None):
    p = get_video_path(num)
    cap = cv2.VideoCapture(str(p))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = total / fps

    print(f"\n================ Video X-{num} (dur={dur:.1f}s, label={label_sec}) ================")

    # Sample every 1s
    for s in range(0, int(dur) + 1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(s * fps)))
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        h, w = frame.shape[:2]

        # Let's check 4 key regions in G07:
        # Region 1: Cashier chair (middle-left: y: 0.35-0.70, x: 0.20-0.45)
        # Region 2: Counter / window (middle-center: y: 0.25-0.60, x: 0.45-0.75)
        # Region 3: Door (right side: y: 0.15-0.85, x: 0.75-0.98)
        # Region 4: Safe / cabinet (in G07, where is the safe? is it on the left or back wall?)
        # Let's measure brightness / color / motion
        chair = frame[int(0.35 * h):int(0.70 * h), int(0.20 * w):int(0.45 * w)]
        counter = frame[int(0.25 * h):int(0.60 * h), int(0.45 * w):int(0.75 * w)]
        door = frame[int(0.15 * h):int(0.85 * h), int(0.75 * w):int(0.98 * w)]

        chair_mean = np.mean(chair, axis=(0, 1))
        counter_mean = np.mean(counter, axis=(0, 1))
        door_mean = np.mean(door, axis=(0, 1))

        # Check if cashier is in chair
        # When chair is empty vs occupied:
        # Let's print summary at each second
        # (we'll also save visual crops for seconds around label_sec)
    cap.release()


def main():
    for num, lbl in [(24, 8.0), (25, None), (26, 9.0), (27, None), (28, 7.0), (29, None)]:
        analyze_g07_video(num, lbl)


if __name__ == "__main__":
    main()
