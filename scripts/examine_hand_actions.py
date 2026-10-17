#!/usr/bin/env python3
"""Extract high-res action crops around label timestamps for all 21 violation videos."""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate import load_dataset, VideoEntry

VIDEOS_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
LABELS_FILE = Path("/Users/nuras/Desktop/datathon/data_raw/open/open_labels.xlsx")
OUT_DIR = ROOT / "artifacts" / "action_crops"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    entries = load_dataset(VIDEOS_DIR, LABELS_FILE)
    viols = [e for e in entries if e.label_verdict == "violation"]

    for e in viols:
        cap = cv2.VideoCapture(str(e.path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

        for lbl in e.label_timestamps:
            # Sample 5 frames: lbl-2, lbl-1, lbl, lbl+1, lbl+2
            sample_t = [max(0.0, min(e.duration_sec, lbl + delta)) for delta in [-2.0, -1.0, 0.0, 1.0, 2.0]]
            frames = []
            for t in sample_t:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                # Draw label on frame
                vis = frame.copy()
                cv2.putText(vis, f"{e.primary_name} t={t:.1f}s (lbl={lbl:.1f}s)", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
                # Crop desk and cashier area: y: 0.15 to 0.95, x: 0.10 to 0.90
                crop = vis[int(0.15 * h):int(0.95 * h), int(0.10 * w):int(0.90 * w)]
                crop_small = cv2.resize(crop, (400, int(400 * crop.shape[0] / crop.shape[1])))
                frames.append(crop_small)

            if frames:
                row = np.hstack(frames)
                out_file = OUT_DIR / f"{e.primary_name}_lbl_{int(lbl)}s.jpg"
                cv2.imwrite(str(out_file), row, [cv2.IMWRITE_JPEG_QUALITY, 90])
                print(f"Saved {out_file.name}")

        cap.release()


if __name__ == "__main__":
    main()
