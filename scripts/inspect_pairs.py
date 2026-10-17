#!/usr/bin/env python3
"""Detailed scene frame extractor for comparative analysis of violation vs no-violation pairs."""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path

VIDEOS_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
OUT_DIR = Path("/Users/nuras/Desktop/datathon/fv-scene/artifacts/inspect")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_clip_frames(video_name: str, times: list[float], prefix: str):
    # Find file
    p = list(VIDEOS_DIR.glob(f"*{video_name.replace('X-', '').replace('X', '')}.mov"))
    if not p:
        candidates = [f for f in VIDEOS_DIR.glob("*.mov") if video_name.lower().replace("-", "") in f.stem.lower().replace("-", "").replace("х", "x")]
        if candidates:
            p = candidates
    if not p:
        print(f"Could not find video for {video_name}")
        return
    path = p[0]
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ret, frame = cap.read()
        if ret and frame is not None:
            # save full frame and crops
            out_file = OUT_DIR / f"{prefix}_{video_name}_{t:05.1f}s.jpg"
            # Stamp time
            vis = frame.copy()
            cv2.putText(vis, f"{video_name} @ {t:.1f}s", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
            cv2.imwrite(str(out_file), vis, [cv2.IMWRITE_JPEG_QUALITY, 90])
    cap.release()


def main():
    # Group G07: X-24, X-26, X-28 (violations) vs X-25, X-27, X-29 (clean)
    print("Extracting G07 keyframes...")
    extract_clip_frames("X-24", [6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0], "G07_VIOL")
    extract_clip_frames("X-26", [7.0, 8.0, 9.0, 10.0, 11.0, 14.0, 18.0], "G07_VIOL")
    extract_clip_frames("X-28", [5.0, 6.0, 7.0, 8.0, 9.0, 12.0, 15.0], "G07_VIOL")

    extract_clip_frames("X-25", [5.0, 10.0, 15.0, 20.0, 25.0, 27.0, 30.0], "G07_NORM")
    extract_clip_frames("X-27", [5.0, 10.0, 15.0, 20.0, 22.0, 25.0], "G07_NORM")
    extract_clip_frames("X-29", [5.0, 8.0, 10.0, 12.0, 14.0, 16.0], "G07_NORM")

    # Group G05: X-22 (violation 14s) vs X-23 (clean)
    print("Extracting G05 keyframes...")
    extract_clip_frames("X-22", [10.0, 12.0, 14.0, 16.0, 18.0, 20.0], "G05_VIOL")
    extract_clip_frames("X-23", [8.0, 10.0, 12.0, 13.0, 15.0, 17.0], "G05_NORM")

    # Group G04: X-11 (violation 18s) vs X-12 (clean), X-14 (clean)
    print("Extracting G04 keyframes...")
    extract_clip_frames("X-11", [12.0, 15.0, 18.0, 20.0, 22.0, 24.0], "G04_VIOL")
    extract_clip_frames("X-12", [15.0, 20.0, 25.0, 30.0, 35.0, 40.0], "G04_NORM")
    print("Done extracting keyframes.")


if __name__ == "__main__":
    main()
