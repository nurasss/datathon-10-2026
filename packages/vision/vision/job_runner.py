"""Entry point executed inside the isolated analysis child process.

The API never runs the video pipeline in its own process: a dedicated child
process gives a hard kill switch (``terminate`` after the deadline), keeps the
web process responsive while one job occupies the CPU, and guarantees that a
crash inside OpenCV cannot take the service down.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .evidence import EvidenceFrame
from .pipeline import AnalysisError, AnalysisOptions, VideoAnalyzer
from .types import EventCandidate

RESULT_VERSION = 1


def _update_progress(db_path: str | None, job_id: str, phase: str, pct: float) -> None:
    if not db_path:
        return
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            conn.execute(
                "UPDATE jobs SET phase = ?, progress = ?, updated_at = ? WHERE job_id = ?",
                (phase, float(pct), time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        # Progress is a nicety; never fail a job because of it.
        pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_analysis_job(
    job_id: str,
    video_path: str,
    out_json: str,
    evidence_dir: str,
    config_path: str | None = None,
    db_path: str | None = None,
    project_root: str | None = None,
) -> None:
    """Analyse one video and persist the result for the parent process."""

    started = time.time()
    report_path = Path(out_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path = Path(evidence_dir)
    evidence_path.mkdir(parents=True, exist_ok=True)

    def progress(phase: str, pct: float) -> None:
        _update_progress(db_path, job_id, phase, pct)

    payload: dict[str, Any] = {
        "version": RESULT_VERSION,
        "job_id": job_id,
        "ok": False,
        "error_code": None,
        "error_message": None,
        "started_at": started,
    }
    try:
        cfg = PipelineConfig.load(config_path) if config_path else PipelineConfig.load()
        analyzer = VideoAnalyzer(cfg=cfg, project_root=project_root)
        result = analyzer.analyze(
            video_path,
            options=AnalysisOptions(job_id=job_id, keep_trace=False),
            progress=progress,
        )
        events = []
        for index, event in enumerate(result.events):
            events.append(_serialise_event(job_id, index, event, evidence_path))
        payload.update(
            {
                "ok": True,
                "verdict": result.verdict,
                "events": events,
                "duration_sec": result.meta.duration_sec,
                "width": result.meta.width,
                "height": result.meta.height,
                "fps": result.meta.fps,
                "frames_analyzed": result.frames_analyzed,
                "analysis_fps": result.analysis_fps,
                "detector": result.detector,
                "warnings": result.warnings,
                "quality": {
                    "zone_source": result.quality.get("zone_source"),
                    "zone_confidence": result.quality.get("zone_confidence"),
                    "zones": result.quality.get("zones"),
                    "frames_with_person": result.quality.get("frames_with_person"),
                },
                "timings": result.timings,
            }
        )
    except AnalysisError as exc:
        payload.update({"error_code": exc.error_code, "error_message": str(exc)})
    except MemoryError:
        payload.update({"error_code": "OUT_OF_MEMORY", "error_message": "недостаточно памяти"})
    except Exception as exc:  # noqa: BLE001 - the reason must be reported, not swallowed
        payload.update(
            {
                "error_code": "INTERNAL_ERROR",
                "error_message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-2000:],
            }
        )
    payload["finished_at"] = time.time()
    payload["elapsed_sec"] = round(payload["finished_at"] - started, 2)
    tmp = report_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, report_path)


def _serialise_event(
    job_id: str,
    index: int,
    event: EventCandidate,
    evidence_dir: Path,
) -> dict[str, Any]:
    frames: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(event.meta.get("evidence", [])):
        if not isinstance(frame, EvidenceFrame):
            continue
        frame_path = evidence_dir / f"event{index}_frame{frame_index}.jpg"
        frame_path.write_bytes(frame.jpeg_bytes)
        frames.append(
            {
                "timestamp_sec": round(float(frame.timestamp_sec), 2),
                "path": str(frame_path),
                "sha256": _sha256_bytes(frame.jpeg_bytes),
                "width": frame.width,
                "height": frame.height,
                "bytes": len(frame.jpeg_bytes),
            }
        )
    return {
        "event_id": f"{job_id}-e{index + 1}",
        "type": event.event_label,
        "event_code": event.event_code,
        "timecode_sec": round(float(event.peak_sec), 2),
        "start_sec": round(float(event.start_sec), 2),
        "end_sec": round(float(event.end_sec), 2),
        "description": f"{event.observation} {event.hypothesis}".strip(),
        "rule_id": event.rule_id,
        "confidence_internal": round(float(event.confidence), 3),
        "visibility_quality": (event.meta.get("variant") or "observed"),
        "evidence": frames,
    }
