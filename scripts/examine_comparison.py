#!/usr/bin/env python3
"""Detailed comparative examination of pairs:
Violations: X-22, X-24, X-26, X-28
Norms: X-23, X-25, X-27, X-29, X-12

Also inspects:
- X-1, X-2, X-3, X-4, X-5
- X-13, X-15, X-16, X-17, X-18, X-19, X-20, X-21
- X-30, X-31, X-32, X-33, X-34, X-35, X-37, X-38, X-39, X-40, X-43, X-44, X-45, X-46
"""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path

VIDEOS_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
OUT_DIR = Path("/Users/nuras/Desktop/datathon/fv-scene/artifacts/crops")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_video_path(name: str) -> Path | None:
    p = list(VIDEOS_DIR.glob(f"*{name.replace('X-', '').replace('X', '')}.mov"))
    if not p:
        candidates = [f for f in VIDEOS_DIR.glob("*.mov") if name.lower().replace("-", "") in f.stem.lower().replace("-", "").replace("х", "x")]
        if candidates:
            p = candidates
    return p[0] if p else None


def crop_regions(video_name: str, timestamps: list[float], tag: str):
    path = get_video_path(video_name)
    if not path:
        print(f"Missing {video_name}")
        return
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    for t in timestamps:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        h, w = frame.shape[:2]
        # Save crops of:
        # 1. Desk area (approx x: 0.15-0.75, y: 0.35-0.90)
        desk_crop = frame[int(0.35 * h):int(0.95 * h), int(0.10 * w):int(0.85 * w)]
        # 2. Safe area (in G07, safe is in background / right or left)
        # Let's save a 800px scaled full frame with time stamp
        small = cv2.resize(frame, (960, 540))
        cv2.putText(small, f"{video_name} ({tag}) t={t:.1f}s", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        cv2.imwrite(str(OUT_DIR / f"{video_name}_{t:04.1f}s_full.jpg"), small)
        cv2.imwrite(str(OUT_DIR / f"{video_name}_{t:04.1f}s_desk.jpg"), desk_crop)
    cap.release()


def main():
    # 1. G07 Violations: X-24 (label 8s), X-26 (label 9s), X-28 (label 7s)
    crop_regions("X-24", [6.0, 7.0, 8.0, 9.0, 10.0, 12.0], "VIOL_label8s")
    crop_regions("X-26", [7.0, 8.0, 9.0, 10.0, 12.0, 15.0], "VIOL_label9s")
    crop_regions("X-28", [5.0, 6.0, 7.0, 8.0, 9.0, 12.0], "VIOL_label7s")

    # 2. G07 Norms: X-25, X-27, X-29
    crop_regions("X-25", [15.0, 20.0, 25.0, 27.0, 30.0], "NORM_leaves")
    crop_regions("X-27", [15.0, 18.0, 20.0, 22.0, 25.0], "NORM_leaves")
    crop_regions("X-29", [8.0, 10.0, 12.0, 14.0, 16.0], "NORM_leaves")

    # 3. G05: X-22 (violation 14s) vs X-23 (norm)
    crop_regions("X-22", [10.0, 12.0, 14.0, 16.0, 18.0], "VIOL_label14s")
    crop_regions("X-23", [10.0, 12.0, 13.0, 15.0, 17.0], "NORM_leaves")

    # 4. G04: X-11 (violation 18s) vs X-12 (norm)
    crop_regions("X-11", [15.0, 18.0, 20.0, 22.0], "VIOL_label18s")
    crop_regions("X-12", [18.0, 22.0, 28.0, 32.0, 38.0], "NORM_leaves")


if __name__ == "__main__":
    main()
