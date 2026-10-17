"""Validate the pipeline adapter, including evidence paths and video bounds."""

from __future__ import annotations

import base64
import math
from typing import Any
from pathlib import Path

from .db import Job
from .schemas import DetailedViolation, DoneJob, DoneJobDetails, RuleDetails


def _extract_rule_details(event: dict[str, Any], registry: Any) -> RuleDetails:
    rule_obj = None
    if hasattr(registry, "get") and event.get("rule_id"):
        rule_obj = registry.get(event["rule_id"])
    if rule_obj is None and hasattr(registry, "for_event") and event.get("event_code"):
        rule_obj = registry.for_event(event["event_code"])
    if rule_obj is None and hasattr(registry, "for_event") and event.get("type"):
        rule_obj = registry.for_event(event["type"])
    if rule_obj is None and hasattr(registry, "rules"):
        for r in getattr(registry, "rules", []):
            if r.event_label == event.get("type") or r.event_type == event.get("event_code"):
                rule_obj = r
                break

    if rule_obj:
        return RuleDetails(
            act_name=rule_obj.act_name,
            act_number=rule_obj.act_number,
            paragraph=rule_obj.paragraph,
            quote=rule_obj.quoted_text,
            source_url=rule_obj.source_url,
        )
    return RuleDetails(
        act_name="",
        act_number="",
        paragraph="",
        quote="",
        source_url="",
    )


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


def done_details_response(job: Job, evidence_root: Path, registry: Any) -> DoneJobDetails:
    duration = job.duration_sec
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Missing valid video duration")
    violations = []
    for event in job.events:
        frames = []
        for frame in event.get("evidence", []):
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
            "rule": _extract_rule_details(event, registry),
        })
    return DoneJobDetails(
        job_id=job.job_id,
        status="done",
        verdict=job.verdict,
        violations=violations,
        video_duration_sec=float(duration),
    )

