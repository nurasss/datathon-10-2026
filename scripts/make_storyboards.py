#!/usr/bin/env python3
"""Generate 2 fps storyboards for all 40 unique videos in artifacts/annotate/<video>.jpg.

Each storyboard is a high-contrast grid:
- 2 frames per second (sampled every 0.5s)
- Frame width = 232 px (height proportional)
- Header with video name, duration, label verdict, and label timestamps
- Each thumbnail stamped with timestamp in seconds (and red highlight if near label timestamp)
"""

from __future__ import annotations

import math
import os
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
OUT_DIR = ROOT / "artifacts" / "annotate"

THUMB_W = 232
FPS_SAMPLE = 2.0
COLS = 6  # 6 columns grid -> 6 * 232 = 1392 px width


def make_storyboard(entry: VideoEntry, out_path: Path) -> None:
    cap = cv2.VideoCapture(str(entry.path))
    if not cap.isOpened():
        print(f"Error opening {entry.path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = entry.duration_sec

    # Sample timestamps every 0.5s
    times = [i * 0.5 for i in range(int(math.ceil(duration * FPS_SAMPLE)) + 1) if i * 0.5 <= duration + 0.1]
    if not times:
        times = [0.0]

    thumbs = []
    thumb_h = None

    for t in times:
        frame_idx = int(round(t * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(frame_idx, max(0, total_frames - 1)))
        ret, frame = cap.read()
        if not ret or frame is None:
            # Fallback black frame if read fails
            if thumb_h is None:
                thumb_h = int(THUMB_W * 9 / 16)
            thumb = np.zeros((thumb_h, THUMB_W, 3), dtype=np.uint8)
        else:
            h, w = frame.shape[:2]
            thumb_h = int(round(THUMB_W * (h / w)))
            thumb = cv2.resize(frame, (THUMB_W, thumb_h), interpolation=cv2.INTER_AREA)

        # Check if t is near any label timestamp (within ±1.0s)
        is_label = any(abs(t - l_ts) <= 1.0 for l_ts in entry.label_timestamps)

        # Draw red border if near label
        if is_label and entry.label_verdict == "violation":
            cv2.rectangle(thumb, (0, 0), (THUMB_W - 1, thumb_h - 1), (0, 0, 255), 3)

        # Time banner at bottom-left
        label_text = f"{t:4.1f}s"
        bg_color = (0, 0, 180) if (is_label and entry.label_verdict == "violation") else (20, 20, 20)
        cv2.rectangle(thumb, (4, thumb_h - 22), (64, thumb_h - 4), bg_color, -1)
        cv2.putText(
            thumb,
            label_text,
            (8, thumb_h - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

        thumbs.append(thumb)

    cap.release()

    if not thumbs:
        return

    # Layout into grid
    num_thumbs = len(thumbs)
    cols = COLS
    rows = int(math.ceil(num_thumbs / cols))

    header_h = 50
    grid_w = cols * THUMB_W
    grid_h = header_h + rows * thumb_h

    canvas = np.zeros((grid_h, grid_w, 3), dtype=np.uint8)
    canvas[:] = (30, 30, 30)

    # Header banner
    header_color = (40, 40, 140) if entry.label_verdict == "violation" else (40, 100, 40)
    cv2.rectangle(canvas, (0, 0), (grid_w, header_h), header_color, -1)

    labels_str = "; ".join(f"{ts:.1f}s" for ts in entry.label_timestamps) if entry.label_timestamps else "None"
    header_text = (
        f"{entry.display_name} | {entry.label_verdict.upper()} (labels: {labels_str}) "
        f"| Dur: {entry.duration_sec:.1f}s | {num_thumbs} frames @ 2fps"
    )
    cv2.putText(canvas, header_text, (15, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

    for idx, thumb in enumerate(thumbs):
        r = idx // cols
        c = idx % cols
        y1 = header_h + r * thumb_h
        y2 = y1 + thumb_h
        x1 = c * THUMB_W
        x2 = x1 + THUMB_W
        canvas[y1:y2, x1:x2] = thumb

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = load_dataset(VIDEOS_DIR, LABELS_FILE)
    print(f"Loaded {len(entries)} unique videos.")
    for i, entry in enumerate(entries, 1):
        out_name = f"{entry.primary_name}.jpg"
        out_path = OUT_DIR / out_name
        print(f"[{i:02d}/40] Generating storyboard for {entry.display_name} -> {out_path.name}")
        make_storyboard(entry, out_path)
    print("Done generating all storyboards.")


if __name__ == "__main__":
    main()
