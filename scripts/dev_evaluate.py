#!/usr/bin/env python
"""Development evaluation over the local (open) dataset.

Not part of the service runtime.  The dataset stays outside the repository and
only aggregate metrics are reported; no video frames are copied anywhere.

Usage::

    python scripts/dev_evaluate.py \
        --videos /Users/nuras/Desktop/datathon/data_raw/open/videos \
        --labels /Users/nuras/Desktop/datathon/data_raw/open/open_labels.xlsx \
        --out reports/dev_eval.json --clips X-1 X-2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in ("vision", "rules"):
    sys.path.insert(0, str(ROOT / "packages" / extra))

from rules.registry import RuleRegistry  # noqa: E402
from vision.config import PipelineConfig  # noqa: E402
from vision.pipeline import AnalysisError, AnalysisOptions, VideoAnalyzer  # noqa: E402

CYR_X = "\u0425"  # Cyrillic capital letter HA, used in part of the dataset file names


def normalize(name: str) -> str:
    return name.replace(CYR_X, "X").strip()


def load_labels(path: Path | None) -> dict[str, dict]:
    if path is None or not path.is_file():
        return {}
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    labels: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        name = normalize(str(row[0]))
        raw_ts = "" if row[1] is None else str(row[1]).lower()
        verdict = str(row[2] or "").strip().lower()
        seconds: float | None = None
        if raw_ts:
            digits = "".join(ch if ch.isdigit() else " " for ch in raw_ts).split()
            numbers = [int(d) for d in digits]
            if "мин" in raw_ts:
                minutes = numbers[0] if numbers else 0
                secs = numbers[1] if len(numbers) > 1 else 0
                seconds = float(minutes * 60 + secs)
            elif numbers:
                seconds = float(numbers[0])
        labels[name] = {"violation": verdict.startswith("наруш"), "timecode_sec": seconds}
    return labels


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--config", type=Path, default=ROOT / "configs" / "inference.default.json")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "dev_eval.json")
    ap.add_argument("--clips", nargs="*", default=None)
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--trace", action="store_true")
    args = ap.parse_args()

    labels = load_labels(args.labels)
    files = sorted(args.videos.glob("*.mov")) + sorted(args.videos.glob("*.MOV"))
    by_name = {normalize(p.stem): p for p in files}
    names = sorted(by_name)
    if args.clips:
        wanted = {normalize(c) for c in args.clips}
        names = [n for n in names if n in wanted]
    if args.max_clips:
        names = names[: args.max_clips]

    cfg = PipelineConfig.load(args.config)
    registry = RuleRegistry.load()
    analyzer = VideoAnalyzer(cfg=cfg, registry=registry, project_root=ROOT)

    results = []
    hashes: dict[str, str] = {}
    started = time.time()
    for name in names:
        path = by_name[name]
        label = labels.get(name, {})
        digest = hashes.setdefault(name, sha256(path))
        rec = {
            "clip": name,
            "file": path.name,
            "sha256": digest[:16],
            "label_violation": label.get("violation"),
            "label_timecode": label.get("timecode_sec"),
        }
        t0 = time.perf_counter()
        try:
            result = analyzer.analyze(path, options=AnalysisOptions(keep_trace=args.trace))
            rec.update(
                {
                    "ok": True,
                    "verdict": result.verdict,
                    "elapsed_sec": round(time.perf_counter() - t0, 2),
                    "frames_analyzed": result.frames_analyzed,
                    "analysis_fps": round(result.analysis_fps, 2),
                    "persons": result.persons_detected,
                    "zones": result.quality.get("zones"),
                    "warnings": result.warnings,
                    "events": [
                        {
                            "code": e.event_code,
                            "type": e.event_label,
                            "peak": round(e.peak_sec, 2),
                            "start": round(e.start_sec, 2),
                            "end": round(e.end_sec, 2),
                            "confidence": round(e.confidence, 3),
                            "evidence": [round(f.timestamp_sec, 2) for f in e.meta.get("evidence", [])],
                            "variant": e.meta.get("variant"),
                        }
                        for e in result.events
                    ],
                }
            )
            if args.trace:
                rec["trace"] = result.quality.get("trace")
        except AnalysisError as exc:
            rec.update({"ok": False, "error": str(exc), "error_code": exc.error_code})
        except Exception as exc:  # noqa: BLE001 - dev tool
            rec.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        results.append(rec)
        print(json.dumps({k: v for k, v in rec.items() if k != "trace"}, ensure_ascii=False), flush=True)

    # ---- video-level metrics -------------------------------------------------------
    tp = fp = fn = tn = 0
    timecode_errors = []
    for rec in results:
        if not rec.get("ok"):
            continue
        if rec["label_violation"] is None:
            continue
        predicted = rec["verdict"] == "violation"
        actual = bool(rec["label_violation"])
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1
        if actual and predicted and rec["label_timecode"] is not None and rec["events"]:
            peaks = [e["peak"] for e in rec["events"]]
            timecode_errors.append(min(abs(p - rec["label_timecode"]) for p in peaks))
    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    summary = {
        "clips_evaluated": total,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "timecode_mae_sec": round(sum(timecode_errors) / len(timecode_errors), 2) if timecode_errors else None,
        "timecode_within_2s": round(sum(1 for e in timecode_errors if e <= 2.0) / len(timecode_errors), 3) if timecode_errors else None,
        "timecode_within_4s": round(sum(1 for e in timecode_errors if e <= 4.0) / len(timecode_errors), 3) if timecode_errors else None,
        "runtime_total_sec": round(time.time() - started, 1),
        "runtime_mean_sec": round(sum(r["elapsed_sec"] for r in results if r.get("ok")) / max(1, sum(1 for r in results if r.get("ok"))), 2),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nSUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
