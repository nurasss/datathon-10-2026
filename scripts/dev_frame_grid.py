#!/usr/bin/env python
"""Development helper: dump a dense grid of frames for one clip (visual QA)."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--cell", type=int, default=300)
    ap.add_argument("--out", type=Path, default=Path("/tmp/fv_zoom.jpg"))
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames_total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    duration = frames_total / fps if fps else 0.0
    end = min(args.end if args.end is not None else duration, duration)
    times = np.arange(args.start, end + 1e-6, args.step)
    cells = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        scale = args.cell / w
        frame = cv2.resize(frame, (args.cell, max(1, int(h * scale))))
        cv2.putText(frame, f"{t:.1f}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cells.append(frame)
    cap.release()
    if not cells:
        raise SystemExit("no frames decoded")
    height = max(c.shape[0] for c in cells)
    cells = [cv2.copyMakeBorder(c, 0, height - c.shape[0], 0, 0, cv2.BORDER_CONSTANT) for c in cells]
    rows = []
    for i in range(0, len(cells), args.cols):
        row = cells[i:i + args.cols]
        while len(row) < args.cols:
            row.append(np.zeros_like(cells[0]))
        rows.append(np.hstack(row))
    grid = np.vstack(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), grid, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    print(f"{args.out} ({grid.shape[1]}x{grid.shape[0]}), frames: {len(cells)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
