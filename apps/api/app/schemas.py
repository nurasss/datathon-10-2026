"""Strict customer contract. UI diagnostics stay outside the public result."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EvidenceFrame(ContractModel):
    timestamp_sec: float = Field(ge=0)
    image_base64: str

    @field_validator("image_base64")
    @classmethod
    def jpeg_base64(cls, value: str) -> str:
        import cv2
        import numpy as np

        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Expected plain JPEG base64") from exc
        if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise ValueError("Expected JPEG bytes")
        if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
            raise ValueError("JPEG cannot be decoded")
        return value


class Violation(ContractModel):
    type: str = Field(min_length=1)
    timecode_sec: float = Field(ge=0)
    description: str = Field(min_length=1)
    evidence_frames: list[EvidenceFrame] = Field(min_length=1, max_length=3)


class AnalyzeAccepted(ContractModel):
    job_id: str


class DoneJob(ContractModel):
    job_id: str
    status: Literal["done"]
    verdict: Literal["violation", "no_violation"]
    violations: list[Violation]

    @model_validator(mode="after")
    def verdict_matches_events(self) -> DoneJob:
        if (self.verdict == "no_violation") != (len(self.violations) == 0):
            raise ValueError("no_violation iff violations is empty")
        return self


class ProcessingJob(ContractModel):
    job_id: str
    status: Literal["processing"]


class FailedJob(ContractModel):
    job_id: str
    status: Literal["failed"]
    error: str | None = None


JobResponse = Annotated[DoneJob | ProcessingJob | FailedJob, Field(discriminator="status")]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    queue_length: int
    worker_busy: bool
    uptime_sec: float


class RuleDetails(ContractModel):
    act_name: str
    act_number: str
    paragraph: str
    quote: str
    source_url: str


class DetailedViolation(ContractModel):
    type: str = Field(min_length=1)
    timecode_sec: float = Field(ge=0)
    description: str = Field(min_length=1)
    evidence_frames: list[EvidenceFrame] = Field(min_length=1, max_length=3)
    rule: RuleDetails


class DoneJobDetails(ContractModel):
    job_id: str
    status: Literal["done"]
    verdict: Literal["violation", "no_violation"]
    violations: list[DetailedViolation]
    video_duration_sec: float

    @model_validator(mode="after")
    def verdict_matches_events(self) -> DoneJobDetails:
        if (self.verdict == "no_violation") != (len(self.violations) == 0):
            raise ValueError("no_violation iff violations is empty")
        return self


class ProcessingJobDetails(ContractModel):
    job_id: str
    status: Literal["processing"]
    video_duration_sec: float | None = None


class FailedJobDetails(ContractModel):
    job_id: str
    status: Literal["failed"]
    error: str | None = None
    video_duration_sec: float | None = None


JobDetailsResponse = Annotated[
    DoneJobDetails | ProcessingJobDetails | FailedJobDetails,
    Field(discriminator="status")
]

