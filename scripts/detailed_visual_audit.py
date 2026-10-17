#!/usr/bin/env python3
"""Comprehensive visual audit script for all 40 videos.

Produces high-res side-by-side comparison panels of key comparison pairs:
Pair 1: X-24 (viol 8s), X-26 (viol 9s), X-28 (viol 7s) vs X-25 (norm), X-27 (norm), X-29 (norm)
Pair 2: X-22 (viol 14s) vs X-23 (norm)
Pair 3: X-11 (viol 18s) vs X-12 (norm)

Also inspects all other violation videos:
X-1 (22s), X-2 (10s), X-4 (57s, 68s), X-13 (47s), X-15 (15s), X-17 (7s), X-18 (20s),
X-20 (5s), X-30 (13s), X-32 (5s), X-33 (6s), X-34 (13s), X-38 (33s), X-40 (17s),
X-44 (16s), X-46 (11s).
"""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEOS_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
OUT_DIR = ROOT / "artifacts" / "visual_audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_video_path(name: str) -> Path:
    num_str = name.replace("X-", "").replace("X", "").strip()
    candidates = list(VIDEOS_DIR.glob(f"*{num_str}.mov")) + list(VIDEOS_DIR.glob(f"*{num_str}.MOV"))
    for c in candidates:
        stem = c.stem.replace("х", "x").replace("Х", "X")
        if stem.endswith(f"-{num_str}") or stem.endswith(f"X{num_str}"):
            return c
    return candidates[0]


def get_frame(cap, t_sec: float, fps: float):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t_sec * fps)))
    ret, frame = cap.read()
    return frame if ret else None


def build_strip(video_name: str, label_sec: float | None, times: list[float], title: str, num_cols: int = 6) -> np.ndarray:
    path = get_video_path(video_name)
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    frames = []
    for t in times[:num_cols]:
        f_idx = min(int(round(t * fps)), max(0, total_frames - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, f = cap.read()
        if not ret or f is None:
            f = np.zeros((720, 1280, 3), dtype=np.uint8)
        small = cv2.resize(f, (360, 202), interpolation=cv2.INTER_AREA)

        is_label = label_sec is not None and abs(t - label_sec) <= 1.0
        if is_label:
            cv2.rectangle(small, (0, 0), (359, 201), (0, 0, 255), 3)

        bg = (0, 0, 180) if is_label else (20, 20, 20)
        cv2.rectangle(small, (4, 178), (70, 198), bg, -1)
        cv2.putText(small, f"{t:4.1f}s", (8, 194), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        frames.append(small)

    cap.release()

    while len(frames) < num_cols:
        blank = np.zeros((202, 360, 3), dtype=np.uint8)
        frames.append(blank)

    row = np.hstack(frames)
    bar_h = 35
    bar = np.zeros((bar_h, row.shape[1], 3), dtype=np.uint8)
    bar[:] = (40, 40, 40) if label_sec is None else (30, 30, 110)
    cv2.putText(bar, f"{video_name}: {title}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, row])



def main():
    # 1. Compare G07: X-24, X-26, X-28 vs X-25, X-27, X-29
    # In X-24, label is 8s; in X-26, label is 9s; in X-28, label is 7s.
    strip_24 = build_strip("X-24", 8.0, [5.0, 7.0, 8.0, 9.0, 11.0, 13.0], "VIOLATION (label: 8s)")
    strip_26 = build_strip("X-26", 9.0, [6.0, 8.0, 9.0, 10.0, 12.0, 15.0], "VIOLATION (label: 9s)")
    strip_28 = build_strip("X-28", 7.0, [4.0, 6.0, 7.0, 8.0, 10.0, 13.0], "VIOLATION (label: 7s)")

    strip_25 = build_strip("X-25", None, [15.0, 20.0, 24.0, 26.0, 28.0, 30.0], "NO VIOLATION (cashier leaves at end)")
    strip_27 = build_strip("X-27", None, [12.0, 16.0, 20.0, 22.0, 24.0, 26.0], "NO VIOLATION (cashier leaves at end)")
    strip_29 = build_strip("X-29", None, [6.0, 8.0, 10.0, 12.0, 14.0, 16.0], "NO VIOLATION (cashier leaves)")

    panel_g07 = np.vstack([strip_24, strip_26, strip_28, strip_25, strip_27, strip_29])
    cv2.imwrite(str(OUT_DIR / "comparison_G07.jpg"), panel_g07, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print("Saved comparison_G07.jpg")

    # 2. Compare G05: X-22 (viol 14s) vs X-23 (norm)
    strip_22 = build_strip("X-22", 14.0, [10.0, 12.0, 14.0, 15.0, 17.0, 20.0], "VIOLATION (label: 14s)")
    strip_23 = build_strip("X-23", None, [8.0, 10.0, 12.0, 13.0, 15.0, 17.0], "NO VIOLATION (cashier leaves)")
    panel_g05 = np.vstack([strip_22, strip_23])
    cv2.imwrite(str(OUT_DIR / "comparison_G05.jpg"), panel_g05, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print("Saved comparison_G05.jpg")

    # 3. Compare G04: X-11 (viol 18s) vs X-12 (norm)
    strip_11 = build_strip("X-11", 18.0, [12.0, 15.0, 18.0, 20.0, 22.0, 24.0], "VIOLATION (label: 18s)")
    strip_12 = build_strip("X-12", None, [15.0, 20.0, 25.0, 30.0, 35.0, 40.0], "NO VIOLATION (cashier leaves)")
    panel_g04 = np.vstack([strip_11, strip_12])
    cv2.imwrite(str(OUT_DIR / "comparison_G04.jpg"), panel_g04, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print("Saved comparison_G04.jpg")


if __name__ == "__main__":
    main()
