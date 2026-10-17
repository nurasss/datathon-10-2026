#!/usr/bin/env python3
"""Dataset evaluation script for ForteVision pipeline.

Executes the vision/rules pipeline directly (in-process, without HTTP) over the
annotated dataset, produces comprehensive performance and error metrics, angle
clustering, and diagnostic contact sheets.

Usage::

    # Full evaluation over 40 deduplicated videos
    python scripts/evaluate.py

    # Targeted run for specific videos
    python scripts/evaluate.py --only X-14,X-16,X-20
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for extra in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / extra))

from rules.registry import RuleRegistry  # noqa: E402
from vision.config import PipelineConfig  # noqa: E402
from vision.pipeline import AnalysisError, AnalysisOptions, VideoAnalyzer  # noqa: E402
from vision.videoprobe import probe, FrameReader  # noqa: E402

CYR_X = "\u0425"  # Cyrillic capital HA


def normalize_name(name: str) -> str:
    """Normalize clip name to ASCII 'X'."""
    return name.replace(CYR_X, "X").replace("х", "x").strip()


def parse_timecode(raw_ts: Any) -> float | None:
    """Parse timecodes like '22 секунда', '1минута 8 секунда', '5 секунда' -> seconds."""
    if raw_ts is None:
        return None
    raw_str = str(raw_ts).strip().lower()
    if not raw_str:
        return None
    digits = "".join(ch if ch.isdigit() else " " for ch in raw_str).split()
    nums = [int(d) for d in digits]
    if not nums:
        return None
    if "мин" in raw_str:
        m = nums[0]
        sec = nums[1] if len(nums) > 1 else 0
        return float(m * 60 + sec)
    return float(nums[0])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class VideoEntry:
    primary_name: str
    aliases: list[str]
    display_name: str
    path: Path
    sha256: str
    duration_sec: float
    fps: float
    width: int
    height: int
    label_verdict: str  # 'violation' or 'no_violation'
    label_timestamps: list[float]
    angle_group: str = ""


def load_dataset(videos_dir: Path, labels_file: Path) -> list[VideoEntry]:
    """Parse labels xlsx and deduplicate videos by SHA256 into exactly 40 unique items."""
    import openpyxl

    wb = openpyxl.load_workbook(labels_file, data_only=True)
    ws = wb.worksheets[0]
    raw_labels: dict[str, list[dict]] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        norm_name = normalize_name(str(row[0]))
        ts = parse_timecode(row[1])
        verdict_str = str(row[2] or "").strip().lower()
        is_violation = "наруш" in verdict_str and "без" not in verdict_str
        raw_labels.setdefault(norm_name, []).append({
            "is_violation": is_violation,
            "ts": ts,
        })

    # Gather video files
    video_files = sorted(videos_dir.glob("*.mov")) + sorted(videos_dir.glob("*.MOV"))
    # Map by sha256
    by_sha: dict[str, list[tuple[str, Path]]] = {}
    for vf in video_files:
        norm_name = normalize_name(vf.stem)
        digest = sha256_file(vf)
        by_sha.setdefault(digest, []).append((norm_name, vf))

    entries: list[VideoEntry] = []
    for digest, file_list in by_sha.items():
        # Pick primary name by numerical index if possible
        def sort_key(item: tuple[str, Path]) -> int:
            try:
                return int(item[0].split("-")[1])
            except (IndexError, ValueError):
                return 9999

        sorted_files = sorted(file_list, key=sort_key)
        primary_name, primary_path = sorted_files[0]
        aliases = [name for name, _ in sorted_files]

        # Gather labels for all aliases
        all_labels: list[dict] = []
        for alias in aliases:
            all_labels.extend(raw_labels.get(alias, []))

        # Check violation
        has_violation = any(l["is_violation"] for l in all_labels)
        timestamps = sorted([l["ts"] for l in all_labels if l.get("ts") is not None])

        meta = probe(primary_path)
        display_name = f"{primary_name} ({', '.join(aliases[1:])})" if len(aliases) > 1 else primary_name

        entries.append(
            VideoEntry(
                primary_name=primary_name,
                aliases=aliases,
                display_name=display_name,
                path=primary_path,
                sha256=digest,
                duration_sec=round(meta.duration_sec, 2),
                fps=round(meta.fps, 2),
                width=meta.width,
                height=meta.height,
                label_verdict="violation" if has_violation else "no_violation",
                label_timestamps=timestamps,
            )
        )

    # Sort entries by primary video number
    def entry_sort_key(e: VideoEntry) -> int:
        try:
            return int(e.primary_name.split("-")[1])
        except (IndexError, ValueError):
            return 9999

    entries.sort(key=entry_sort_key)
    return entries


def cluster_angle_groups(entries: list[VideoEntry], clusters_out_dir: Path, threshold: float = 0.88) -> None:
    """Cluster videos by first frame grayscale correlation and save thumbnails for visual audit."""
    first_frames: dict[str, np.ndarray] = {}
    first_frames_color: dict[str, np.ndarray] = {}

    for entry in entries:
        cap = cv2.VideoCapture(str(entry.path))
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            frame = np.zeros((848, 464, 3), dtype=np.uint8)
        first_frames_color[entry.primary_name] = frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90))
        first_frames[entry.primary_name] = small

    n = len(entries)
    sim_matrix = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        im1 = first_frames[entries[i].primary_name].astype(np.float32)
        im1 = (im1 - im1.mean()) / (im1.std() + 1e-6)
        for j in range(i, n):
            im2 = first_frames[entries[j].primary_name].astype(np.float32)
            im2 = (im2 - im2.mean()) / (im2.std() + 1e-6)
            s = float(np.mean(im1 * im2))
            sim_matrix[i, j] = s
            sim_matrix[j, i] = s

    # Agglomerative clustering with average linkage
    clusters = [[i] for i in range(n)]
    while True:
        best_sim = -1.0
        best_pair = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                s = float(np.mean([sim_matrix[a, b] for a in clusters[i] for b in clusters[j]]))
                if s > best_sim:
                    best_sim = s
                    best_pair = (i, j)
        if best_sim < threshold or best_pair is None:
            break
        i, j = best_pair
        clusters[i] = clusters[i] + clusters[j]
        clusters.pop(j)

    # Sort clusters by minimum video ID
    def cluster_min_key(c: list[int]) -> int:
        def vid_idx(idx: int) -> int:
            try:
                return int(entries[idx].primary_name.split("-")[1])
            except (IndexError, ValueError):
                return 9999
        return min(vid_idx(x) for x in c)

    clusters.sort(key=cluster_min_key)

    clusters_out_dir.mkdir(parents=True, exist_ok=True)
    cluster_manifest: list[dict] = []

    for c_idx, cluster in enumerate(clusters, 1):
        group_id = f"G{c_idx:02d}"
        cluster_sorted = sorted(cluster, key=lambda idx: int(entries[idx].primary_name.split("-")[1]))
        members = [entries[idx].primary_name for idx in cluster_sorted]
        for idx in cluster_sorted:
            entries[idx].angle_group = group_id

        # Generate contact sheet image for cluster
        thumb_w, thumb_h = 232, 424
        cols = min(6, len(cluster_sorted))
        rows = (len(cluster_sorted) + cols - 1) // cols
        sheet = np.zeros((rows * thumb_h, cols * thumb_w, 3), dtype=np.uint8)

        for pos, idx in enumerate(cluster_sorted):
            r = pos // cols
            c = pos % cols
            img = first_frames_color[entries[idx].primary_name]
            resized = cv2.resize(img, (thumb_w, thumb_h))
            # Put label banner
            cv2.rectangle(resized, (0, 0), (thumb_w, 35), (0, 0, 0), -1)
            cv2.putText(
                resized,
                entries[idx].primary_name,
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            sheet[r * thumb_h : (r + 1) * thumb_h, c * thumb_w : (c + 1) * thumb_w] = resized

        cluster_img_path = clusters_out_dir / f"cluster_{group_id}.jpg"
        cv2.imwrite(str(cluster_img_path), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        cluster_manifest.append({
            "group": group_id,
            "count": len(members),
            "videos": members,
            "sheet": cluster_img_path.name,
        })

    (clusters_out_dir / "clusters.json").write_text(
        json.dumps(cluster_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_frame_at(cap: cv2.VideoCapture, time_sec: float) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_sec * 1000.0))
    ok, frame = cap.read()
    if ok and frame is not None:
        return frame
    return None


def generate_contact_sheet(
    entry: VideoEntry,
    analysis_res: Any,
    outcome: str,
    output_path: Path,
) -> None:
    """Generate diagnostic sheet for one video.

    Top row: 5 frames around the label timestamp (t-4..t+4).
    Bottom row: evidence frames of predicted events with type and timestamp.
    Each frame width ~232 px.
    """
    frame_w = 232
    frame_h = 424
    header_h = 44
    title_bar_h = 28
    pad = 4

    cap = cv2.VideoCapture(str(entry.path))

    # --- 1. Top row: 5 frames around label (or evenly spaced) ---
    top_frames: list[np.ndarray] = []
    top_labels: list[str] = []

    dur = max(0.1, entry.duration_sec)
    if entry.label_timestamps:
        if len(entry.label_timestamps) == 1:
            t = entry.label_timestamps[0]
            times = [
                max(0.0, t - 4.0),
                max(0.0, t - 2.0),
                t,
                min(dur - 0.05, t + 2.0),
                min(dur - 0.05, t + 4.0),
            ]
        else:
            # Multiple labels (e.g. X-4: 57s, 68s)
            t1, t2 = entry.label_timestamps[0], entry.label_timestamps[-1]
            times = [
                max(0.0, t1 - 4.0),
                t1,
                (t1 + t2) / 2.0,
                t2,
                min(dur - 0.05, t2 + 4.0),
            ]
    else:
        times = [
            dur * 0.1,
            dur * 0.3,
            dur * 0.5,
            dur * 0.7,
            dur * 0.9,
        ]

    for t in times:
        raw = extract_frame_at(cap, t)
        if raw is None:
            raw = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
        else:
            raw = cv2.resize(raw, (frame_w, frame_h))
        # Top banner with timestamp
        cv2.rectangle(raw, (0, 0), (frame_w, 28), (0, 0, 0), -1)
        cv2.putText(
            raw,
            f"t={t:.1f}s",
            (8, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        if entry.label_timestamps and any(abs(t - lbl) < 0.2 for lbl in entry.label_timestamps):
            cv2.rectangle(raw, (0, frame_h - 26), (frame_w, frame_h), (0, 0, 180), -1)
            cv2.putText(
                raw,
                "LABEL EVENT",
                (8, frame_h - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
        top_frames.append(raw)

    cap.release()

    # --- 2. Bottom row: evidence frames of predicted events ---
    bottom_frames: list[np.ndarray] = []
    events = getattr(analysis_res, "events", []) or []

    for ev in events:
        evidence_list = ev.meta.get("evidence", [])
        code = getattr(ev, "event_code", "EV")
        peak = getattr(ev, "peak_sec", 0.0)
        if evidence_list:
            for ef in evidence_list[:3]:  # up to 3 per event
                jpeg_data = getattr(ef, "jpeg_bytes", b"")
                if jpeg_data:
                    buf = np.frombuffer(jpeg_data, dtype=np.uint8)
                    ef_img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                    if ef_img is not None:
                        ef_resized = cv2.resize(ef_img, (frame_w, frame_h))
                        t_frame = getattr(ef, "timestamp_sec", peak)
                        cv2.rectangle(ef_resized, (0, 0), (frame_w, 28), (0, 0, 0), -1)
                        cv2.putText(
                            ef_resized,
                            f"{code} t={t_frame:.1f}s",
                            (8, 20),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (0, 255, 0),
                            2,
                            cv2.LINE_AA,
                        )
                        bottom_frames.append(ef_resized)
        else:
            # Fallback if no frames in evidence: extract peak frame
            c2 = cv2.VideoCapture(str(entry.path))
            raw = extract_frame_at(c2, peak)
            c2.release()
            if raw is not None:
                ef_resized = cv2.resize(raw, (frame_w, frame_h))
                cv2.rectangle(ef_resized, (0, 0), (frame_w, 28), (0, 0, 0), -1)
                cv2.putText(
                    ef_resized,
                    f"{code} t={peak:.1f}s",
                    (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                bottom_frames.append(ef_resized)

    # Pad or truncate bottom row to 5 frames
    if not bottom_frames:
        for _ in range(5):
            card = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
            card[:] = (20, 20, 20)
            cv2.putText(
                card,
                "no events",
                (30, frame_h // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (120, 120, 120),
                1,
                cv2.LINE_AA,
            )
            bottom_frames.append(card)
    elif len(bottom_frames) < 5:
        while len(bottom_frames) < 5:
            card = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
            card[:] = (16, 16, 16)
            bottom_frames.append(card)
    else:
        bottom_frames = bottom_frames[:5]

    # --- Assemble Canvas ---
    total_w = frame_w * 5 + pad * 6
    total_h = header_h + title_bar_h + frame_h + pad + title_bar_h + frame_h + pad * 2
    canvas = np.zeros((total_h, total_w, 3), dtype=np.uint8)
    canvas[:] = (26, 26, 26)

    # Header bar
    outcome_colors = {
        "TP": (0, 180, 0),
        "TN": (180, 140, 0),
        "FP": (0, 0, 220),
        "FN": (0, 120, 255),
    }
    cv2.rectangle(canvas, (0, 0), (total_w, header_h), (38, 38, 38), -1)
    lbl_ts_str = "; ".join(f"{t:.1f}s" for t in entry.label_timestamps) if entry.label_timestamps else "None"
    pred_verdict = getattr(analysis_res, "verdict", "n/a")
    header_text = (
        f"Video: {entry.display_name} | Group: {entry.angle_group} | Dur: {entry.duration_sec:.1f}s | "
        f"Label: {entry.label_verdict} ({lbl_ts_str}) | Pred: {pred_verdict}"
    )
    cv2.putText(canvas, header_text, (pad + 4, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (240, 240, 240), 2, cv2.LINE_AA)

    # Outcome badge
    badge_color = outcome_colors.get(outcome, (120, 120, 120))
    cv2.rectangle(canvas, (total_w - 75, 6), (total_w - pad, header_h - 6), badge_color, -1)
    cv2.putText(canvas, outcome, (total_w - 65, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

    # Top row title
    y_cursor = header_h
    cv2.rectangle(canvas, (0, y_cursor), (total_w, y_cursor + title_bar_h), (30, 30, 30), -1)
    cv2.putText(
        canvas,
        "TOP ROW: Frames around label timestamp (t-4..t+4, or evenly spaced)",
        (pad + 4, y_cursor + 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )
    y_cursor += title_bar_h

    # Blit top frames
    for idx, f in enumerate(top_frames):
        x = pad + idx * (frame_w + pad)
        canvas[y_cursor : y_cursor + frame_h, x : x + frame_w] = f
    y_cursor += frame_h + pad

    # Bottom row title
    cv2.rectangle(canvas, (0, y_cursor), (total_w, y_cursor + title_bar_h), (30, 30, 30), -1)
    cv2.putText(
        canvas,
        "BOTTOM ROW: Predicted event evidence frames (type & timestamp)",
        (pad + 4, y_cursor + 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )
    y_cursor += title_bar_h

    # Blit bottom frames
    for idx, f in enumerate(bottom_frames):
        x = pad + idx * (frame_w + pad)
        canvas[y_cursor : y_cursor + frame_h, x : x + frame_w] = f

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

    # Also write alias and display_name copies so user or scripts can inspect by any format
    for alias in set(entry.aliases + [entry.display_name]):
        alias_path = output_path.parent / f"{alias}.jpg"
        if alias_path != output_path:
            shutil.copyfile(output_path, alias_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate ForteVision pipeline on dataset.")
    parser.add_argument("--videos", type=Path, default=ROOT.parent / "data_raw" / "open" / "videos")
    parser.add_argument("--labels", type=Path, default=ROOT.parent / "data_raw" / "open" / "open_labels.xlsx")
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "inference.default.json")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory (default: artifacts/eval/<ts>)")
    parser.add_argument("--only", type=str, default="", help="Comma-separated list of clip IDs to evaluate (e.g. X-14,X-16)")
    parser.add_argument("--cluster-threshold", type=float, default=0.88, help="Clustering correlation threshold")
    parser.add_argument("--no-sheets", action="store_true", help="Skip contact sheet generation")
    args = parser.parse_args()

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or (ROOT / "artifacts" / "eval" / timestamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== ForteVision Evaluation ===")
    print(f"Videos dir:   {args.videos}")
    print(f"Labels file:  {args.labels}")
    print(f"Config path:  {args.config}")
    print(f"Output dir:   {out_dir}")

    # 1. Load dataset & deduplicate by SHA256
    entries = load_dataset(args.videos, args.labels)
    total_deduped = len(entries)
    print(f"Dataset: 42 raw files -> {total_deduped} unique videos by SHA256")
    n_viol = sum(1 for e in entries if e.label_verdict == "violation")
    n_no_viol = sum(1 for e in entries if e.label_verdict == "no_violation")
    print(f"Ground truth balance: {n_viol} violation, {n_no_viol} no_violation")

    # 2. Cluster camera angles
    clusters_dir = out_dir / "clusters"
    cluster_angle_groups(entries, clusters_dir, threshold=args.cluster_threshold)
    unique_groups = sorted({e.angle_group for e in entries})
    print(f"Angle groups: {len(unique_groups)} clusters identified ({', '.join(unique_groups)})")

    # 3. Filter by --only if provided
    active_entries = entries
    if args.only:
        requested = [normalize_name(item) for item in args.only.replace(" ", "").split(",") if item]
        requested_set = set(requested)
        active_entries = [
            e for e in entries
            if e.primary_name in requested_set or any(a in requested_set for a in e.aliases)
        ]
        print(f"Filtered via --only: {len(active_entries)} of {len(entries)} videos selected")

    # 4. Initialize pipeline analyzer
    cfg = PipelineConfig.load(args.config)
    registry = RuleRegistry.load()
    analyzer = VideoAnalyzer(cfg=cfg, registry=registry, project_root=ROOT)

    sheets_dir = out_dir / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)

    results_rows: list[dict] = []
    timecode_errors: list[float] = []
    errors_by_group: dict[str, list[float]] = {g: [] for g in unique_groups}

    print(f"\nRunning analysis across {len(active_entries)} clips...")

    for idx, entry in enumerate(active_entries, 1):
        t0 = time.perf_counter()
        try:
            res = analyzer.analyze(entry.path)
            runtime_sec = round(time.perf_counter() - t0, 2)
            pred_verdict = res.verdict
            events = res.events
        except Exception as exc:
            runtime_sec = round(time.perf_counter() - t0, 2)
            pred_verdict = "error"
            events = []
            print(f"[{idx:02d}/{len(active_entries):02d}] {entry.primary_name}: ERROR: {exc}")

        # Format predicted events as type@timecode;…
        pred_events_str = ";".join(f"{e.event_code}@{e.peak_sec:.1f}" for e in events) if events else ""

        # Determine outcome
        actual_viol = entry.label_verdict == "violation"
        pred_viol = pred_verdict == "violation"
        if actual_viol and pred_viol:
            outcome = "TP"
        elif not actual_viol and pred_viol:
            outcome = "FP"
        elif actual_viol and not pred_viol:
            outcome = "FN"
        else:
            outcome = "TN"

        # Timecode error for TP
        label_ts_str = ";".join(str(int(t) if t.is_integer() else t) for t in entry.label_timestamps)
        if outcome == "TP" and entry.label_timestamps and events:
            peaks = [e.peak_sec for e in events]
            for lbl_t in entry.label_timestamps:
                best_err = min(abs(p - lbl_t) for p in peaks)
                timecode_errors.append(round(best_err, 2))
                errors_by_group[entry.angle_group].append(round(best_err, 2))

        # Format results.csv row:
        # video, sha, angle_group, duration, label, label_ts, pred_verdict, pred_events (type@timecode;…), runtime_sec, outcome (TP/FP/FN/TN)
        row = {
            "video": entry.display_name,
            "sha": entry.sha256[:16],
            "angle_group": entry.angle_group,
            "duration": entry.duration_sec,
            "label": entry.label_verdict,
            "label_ts": label_ts_str,
            "pred_verdict": pred_verdict,
            "pred_events": pred_events_str,
            "runtime_sec": runtime_sec,
            "outcome": outcome,
        }
        results_rows.append(row)

        print(
            f"[{idx:02d}/{len(active_entries):02d}] {entry.display_name:16s} | "
            f"Dur: {entry.duration_sec:5.1f}s | Label: {entry.label_verdict:12s} ({label_ts_str:10s}) | "
            f"Pred: {pred_verdict:12s} | {outcome:2s} | Time: {runtime_sec:5.2f}s",
            flush=True,
        )

        if not args.no_sheets:
            sheet_file = sheets_dir / f"{entry.primary_name}.jpg"
            generate_contact_sheet(entry, res, outcome, sheet_file)

    # 5. Write results.csv
    csv_path = out_dir / "results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "video",
            "sha",
            "angle_group",
            "duration",
            "label",
            "label_ts",
            "pred_verdict",
            "pred_events",
            "runtime_sec",
            "outcome",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results_rows)

    # 6. Calculate metrics
    def calc_metrics(rows: list[dict], errors: list[float]) -> dict[str, Any]:
        tp = sum(1 for r in rows if r["outcome"] == "TP")
        fp = sum(1 for r in rows if r["outcome"] == "FP")
        fn = sum(1 for r in rows if r["outcome"] == "FN")
        tn = sum(1 for r in rows if r["outcome"] == "TN")
        total = tp + fp + fn + tn
        precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
        recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
        f1 = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0

        tc_metrics = {
            "median": round(float(np.median(errors)), 2) if errors else None,
            "max": round(float(np.max(errors)), 2) if errors else None,
            "mean": round(float(np.mean(errors)), 2) if errors else None,
            "count": len(errors),
        }
        return {
            "total": total,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "timecode_error": tc_metrics,
        }

    overall_metrics = calc_metrics(results_rows, timecode_errors)
    by_angle_group: dict[str, dict] = {}
    for g in unique_groups:
        g_rows = [r for r in results_rows if r["angle_group"] == g]
        if g_rows:
            by_angle_group[g] = calc_metrics(g_rows, errors_by_group[g])

    metrics_payload = {
        "summary": overall_metrics,
        "by_angle_group": by_angle_group,
    }

    metrics_path = out_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 50)
    print("EVALUATION COMPLETE")
    print(f"Results CSV:  {csv_path}")
    print(f"Metrics JSON: {metrics_path}")
    print(f"Sheets:       {sheets_dir}")
    print(f"Clusters:     {clusters_dir}")
    print("=" * 50)
    print(json.dumps(metrics_payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
