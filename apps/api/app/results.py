"""Validate the pipeline adapter, including evidence paths and video bounds."""

from __future__ import annotations

import base64
import math
from pathlib import Path

from .db import Job
from .schemas import DoneJob


def done_response(job: Job, evidence_root: Path) -> DoneJob:
    duration = job.duration_sec
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Missing valid video duration")
    violations = []
    for event in job.events:
        frames = []
        for frame in event["evidence"]:
            path = Path(frame["path"]).resolve()
            path.relative_to((evidence_root / job.job_id).resolve())
            timestamp = float(frame["timestamp_sec"])
            if not 0 <= timestamp <= duration:
                raise ValueError("Evidence timestamp outside video")
            frames.append({
                "timestamp_sec": timestamp,
                "image_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            })
        timecode = float(event["timecode_sec"])
        if not 0 <= timecode <= duration:
            raise ValueError("Event timecode outside video")
        violations.append({
            "type": event["type"],
            "timecode_sec": timecode,
            "description": event["description"],
            "evidence_frames": frames,
        })
    return DoneJob(job_id=job.job_id, status="done", verdict=job.verdict, violations=violations)
