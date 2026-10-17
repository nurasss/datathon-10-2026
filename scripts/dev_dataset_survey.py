#!/usr/bin/env python
"""Development helper: probe the local dataset and build frame contact sheets.

Usage (inside the project venv)::

    python scripts/dev_dataset_survey.py --labels ../../data_raw/open/open_labels.xlsx \
        --videos ../../data_raw/open/videos --out /tmp/fv_survey

The dataset itself is never copied into the repository: only derived,
non-revealing statistics are printed.  This script is meant for local model
development and is not part of the service runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "vision"))

from vision.videoprobe import probe, VideoProbeError  # noqa: E402


def parse_labels(path: Path | None) -> dict[str, dict]:
    if path is None or not path.is_file():
        return {}
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    labels: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        name = str(row[0]).strip()
        ts_raw = row[1]
        verdict_raw = str(row[2] or "").strip().lower()
        seconds = None
        if ts_raw:
            text = str(ts_raw).lower().replace("секунда", "").replace("сек", "").strip()
            if "минут" in str(ts_raw).lower():
                parts = str(ts_raw).lower().replace("минута", "").replace("минут", "").split("секунда")
                try:
                    minutes = float("".join(ch for ch in parts[0] if ch.isdigit() or ch == " "))
                except ValueError:
                    minutes = 0.0
                secs = 0.0
                if len(parts) > 1:
                    digits = "".join(ch for ch in parts[1] if ch.isdigit())
                    secs = float(digits) if digits else 0.0
                seconds = minutes * 60 + secs
            else:
                digits = "".join(ch for ch in text if ch.isdigit())
                seconds = float(digits) if digits else None
        labels[name] = {
            "violation": verdict_raw.startswith("наруш"),
            "timecode_sec": seconds,
        }
    return labels


def contact_sheet(video: Path, times: list[float], cols: int = 3, cell_w: int = 640) -> np.ndarray:
    cap = cv2.VideoCapture(str(video))
    frames = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            frame = np.zeros((360, 640, 3), np.uint8)
            cv2.putText(frame, "n/a", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 255), 3)
        h, w = frame.shape[:2]
        scale = cell_w / w
        frame = cv2.resize(frame, (cell_w, max(1, int(h * scale))))
        cv2.putText(frame, f"t={t:.1f}s", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        frames.append(frame)
    cap.release()
    cells = [f for f in frames]
    height = max(f.shape[0] for f in cells)
    cells = [cv2.copyMakeBorder(f, 0, height - f.shape[0], 0, 0, cv2.BORDER_CONSTANT) for f in cells]
    rows = []
    for i in range(0, len(cells), cols):
        row = cells[i:i + cols]
        while len(row) < cols:
            row.append(np.zeros_like(cells[0]))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("/tmp/fv_survey"))
    ap.add_argument("--name", action="append", default=None, help="only these clip ids")
    ap.add_argument("--sheets", action="store_true", help="build contact sheets")
    args = ap.parse_args()

    labels = parse_labels(args.labels)
    args.out.mkdir(parents=True, exist_ok=True)
    inventory = []
    names = sorted(args.name) if args.name else sorted(p.stem for p in args.videos.glob("*.mov"))
    for name in names:
        path = args.videos / f"{name}.mov"
        if not path.is_file():
            matches = list(args.videos.glob(f"{name}.*"))
            if not matches:
                continue
            path = matches[0]
        try:
            meta = probe(path)
        except VideoProbeError as exc:
            print(f"{name}: PROBE FAILED ({exc})")
            continue
        label = labels.get(name, {})
        inventory.append({
            "name": name,
            "file": path.name,
            "duration": round(meta.duration_sec, 2),
            "fps": round(meta.fps, 3),
            "size": f"{meta.width}x{meta.height}",
            "codec": meta.codec,
            "audio": meta.has_audio,
            "violation": label.get("violation"),
            "timecode": label.get("timecode_sec"),
            "file_mb": round(path.stat().st_size / 1e6, 2),
        })
        if args.sheets:
            t_ev = label.get("timecode_sec")
            times = [0.0, max(0.0, (t_ev or meta.duration_sec / 2) - 2.0), t_ev or meta.duration_sec / 2,
                     min(meta.duration_sec - 0.1, (t_ev or 0) + 2.0), min(meta.duration_sec - 0.1, meta.duration_sec - 0.5),
                     max(0.0, (t_ev or 0) + 6.0)]
            sheet = contact_sheet(path, times)
            cv2.imwrite(str(args.out / f"{name}.jpg"), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    print(json.dumps(inventory, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
