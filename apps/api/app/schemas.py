"""Public response models — the machine-checkable contract of the service.

Only the fields defined by the customer's contract are *required*:
``job_id``, ``status``, ``verdict``, ``violations[]`` with
``type``/``timecode_sec``/``description``/``evidence_frames[]`` and
``timestamp_sec``/``image_base64`` inside a frame.  Everything else is an
explicitly optional extension (documented in docs/API.md) and may be ignored.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class EvidenceFrame(BaseModel):
    timestamp_sec: float = Field(ge=0, description="Секунда от начала исходной записи")
    image_base64: str = Field(description="JPEG (base64, без префикса data:)")


class Violation(BaseModel):
    type: str
    timecode_sec: float = Field(ge=0)
    description: str
    evidence_frames: list[EvidenceFrame]


class AnalyzeAccepted(BaseModel):
    job_id: str
    status: Literal["processing"] = "processing"


class JobResponse(BaseModel):
    """Ответ GET /api/v1/jobs/{job_id}."""

    job_id: str
    status: Literal["processing", "done", "failed"]
    #: присутствует только при status=done
    verdict: Optional[Literal["violation", "no_violation"]] = None
    #: присутствует только при status=done (пустой список при no_violation)
    violations: Optional[list[Violation]] = None
    # -- необязательные расширения (не входят в обязательный контракт) --
    error: Optional[str] = None
    progress: Optional[float] = None
    queue_position: Optional[int] = None
    phase: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    queue_length: int
    worker_busy: bool
    uptime_sec: float
    analytics_rate_ok: bool = True


class RuleInfo(BaseModel):
    rule_id: str
    event_type: str
    event_label: str
    act_number: str
    source_url: str
    paragraph: str
    quoted_text: str
    status: str
    cannot_infer: list[str]
