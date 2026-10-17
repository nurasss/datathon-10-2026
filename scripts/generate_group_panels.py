#!/usr/bin/env python3
"""Generate group-by-group comparison panels for all 11 angle groups.
Each panel shows all videos in the group with synchronized time markers,
clear indication of violation vs norm, and highlights at label timestamps.
"""

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
OUT_DIR = ROOT / "artifacts" / "group_panels"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def build_video_strip(entry: VideoEntry, sample_times: list[float], thumb_w: int = 320, thumb_h: int = 180) -> np.ndarray:
    cap = cv2.VideoCapture(str(entry.path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    frames = []
    for t in sample_times:
        if t > entry.duration_sec:
            # blank frame beyond duration
            f = np.zeros((thumb_h, thumb_w, 3), dtype=np.uint8)
            cv2.putText(f, "END", (thumb_w // 2 - 30, thumb_h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 100, 100), 2)
        else:
            f_idx = min(int(round(t * fps)), max(0, total - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                f = np.zeros((thumb_h, thumb_w, 3), dtype=np.uint8)
            else:
                f = cv2.resize(frame, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)

        # Check if near label
        is_label = any(abs(t - l_ts) <= 1.0 for l_ts in entry.label_timestamps)
        if is_label and entry.label_verdict == "violation":
            cv2.rectangle(f, (0, 0), (thumb_w - 1, thumb_h - 1), (0, 0, 255), 3)

        bg = (0, 0, 180) if (is_label and entry.label_verdict == "violation") else (20, 20, 20)
        cv2.rectangle(f, (4, thumb_h - 22), (64, thumb_h - 4), bg, -1)
        cv2.putText(f, f"{t:4.1f}s", (8, thumb_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        frames.append(f)

    cap.release()

    row = np.hstack(frames)
    bar_h = 32
    bar = np.zeros((bar_h, row.shape[1], 3), dtype=np.uint8)
    bar[:] = (30, 30, 120) if entry.label_verdict == "violation" else (30, 90, 30)

    lbl_str = "; ".join(f"{ts:.1f}s" for ts in entry.label_timestamps) if entry.label_timestamps else "None"
    text = f"{entry.display_name} | {entry.label_verdict.upper()} (label: {lbl_str}) | Dur: {entry.duration_sec:.1f}s"
    cv2.putText(bar, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, row])


def main():
    entries = load_dataset(VIDEOS_DIR, LABELS_FILE)
    # Group map
    from evaluate import cluster_angle_groups
    cluster_angle_groups(entries, ROOT / "artifacts" / "clusters")




    by_group: dict[str, list[VideoEntry]] = {}
    for e in entries:
        by_group.setdefault(e.angle_group, []).append(e)

    for g_id, g_entries in sorted(by_group.items()):
        max_dur = max(e.duration_sec for e in g_entries)
        # Choose 6 to 8 representative timestamps
        # If max_dur <= 30: step 4s
        # If max_dur > 30: step 10s or custom
        step = max(2.0, round(max_dur / 7.0, 1))
        sample_times = [round(i * step, 1) for i in range(8)]

        strips = [build_video_strip(e, sample_times) for e in g_entries]
        panel = np.vstack(strips)
        out_path = OUT_DIR / f"group_{g_id}.jpg"
        cv2.imwrite(str(out_path), panel, [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(f"Generated {out_path.name} with {len(g_entries)} videos.")


if __name__ == "__main__":
    main()
