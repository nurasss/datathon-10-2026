#!/usr/bin/env python3
"""Deep inspection of each of the 21 violation videos and key clean videos.

For each video, prints:
1. Video duration, label timestamp.
2. What changes at label_ts: cashier position, hand movements, desk changes, client presence.
3. What is on the table before vs during vs after.
4. What happens with the safe / door / cash drawer.
"""

from __future__ import annotations

import json
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VIDEOS_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
LABELS_FILE = Path("/Users/nuras/Desktop/datathon/data_raw/open/open_labels.xlsx")

sys_path = Path("/Users/nuras/Desktop/datathon/fv-scene/scripts")
import sys
sys.path.insert(0, str(sys_path))
from evaluate import load_dataset, VideoEntry


def inspect_video(entry: VideoEntry):
    cap = cv2.VideoCapture(str(entry.path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    duration = entry.duration_sec
    labels = entry.label_timestamps

    # Let's inspect at labels ± 5s
    times = sorted(list({max(0.0, min(duration, round(t, 1))) for l in labels for t in np.arange(max(0, l - 5), min(duration, l + 5.1), 0.5)}))
    if not times:
        # For clean videos, sample at key intervals
        times = [0.0, duration * 0.25, duration * 0.5, duration * 0.75, duration]

    frames = {}
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ret, frame = cap.read()
        if ret and frame is not None:
            frames[t] = frame

    cap.release()
    return frames


def main():
    entries = load_dataset(VIDEOS_DIR, LABELS_FILE)
    viols = [e for e in entries if e.label_verdict == "violation"]
    norms = [e for e in entries if e.label_verdict == "no_violation"]

    print(f"Total: {len(entries)} (Violations: {len(viols)}, Norms: {len(norms)})")
    for v in viols:
        print(f"VIOLATION: {v.display_name} | Dur: {v.duration_sec:.1f}s | Labels: {v.label_timestamps}")

    for n in norms:
        print(f"NORM: {n.display_name} | Dur: {n.duration_sec:.1f}s")


if __name__ == "__main__":
    main()
