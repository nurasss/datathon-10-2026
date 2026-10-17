"""Common data types for the local video-analysis pipeline.

Everything here is framework-agnostic: no FastAPI, no I/O side effects.
All timestamps are seconds measured from the beginning of the source video.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class VideoMeta:
    """Metadata extracted with ffprobe (isolated subprocess)."""

    path: str
    width: int
    height: int
    fps: float
    duration_sec: float
    nb_frames: int
    codec: str
    format_name: str
    has_audio: bool
    variable_frame_rate: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_decodable(self) -> bool:
        return self.width > 0 and self.height > 0 and self.duration_sec > 0


@dataclass
class Detection:
    """A single person-like detection in one sampled frame (pixel coordinates)."""

    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    label: str = "person"
    source: str = "onnx"  # onnx | motion_blob | stub

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def iou(self, other: "Detection") -> float:
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


@dataclass
class FrameObservation:
    """Result of analysing one sampled frame."""

    t: float
    frame_index: int
    detections: list[Detection] = field(default_factory=list)
    foreground_ratio: float = 0.0  # share of ROI pixels that differ from the static background
    motion: float = 0.0            # mean absolute difference to the previous sampled frame (0..255)


@dataclass
class ZoneSpec:
    """A rectangular region of interest in pixel coordinates."""

    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float = 0.0
    source: str = "auto"  # auto | config

    def contains(self, px: float, py: float) -> bool:
        return self.x1 <= px <= self.x2 and self.y1 <= py <= self.y2

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "x1": round(self.x1, 1),
            "y1": round(self.y1, 1),
            "x2": round(self.x2, 1),
            "y2": round(self.y2, 1),
            "confidence": round(self.confidence, 3),
            "source": self.source,
        }


@dataclass
class EventCandidate:
    """A potential regulation-relevant event found by the temporal logic.

    ``rule_id`` must point to an *approved* entry of the local rule registry;
    events without an approved rule are never emitted by the pipeline.
    """

    event_code: str          # EV01, EV02 ...
    event_label: str         # human readable type shown in the JSON ``type`` field
    start_sec: float
    peak_sec: float
    end_sec: float
    rule_id: str
    observation: str         # what is visible (objective wording)
    hypothesis: str          # why it requires human review (guarded wording)
    evidence_times: list[float]
    confidence: float
    detections_at: list[FrameObservation] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    """Full internal result of one analysis run (before serialisation)."""

    meta: VideoMeta
    events: list[EventCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    zones: list[ZoneSpec] = field(default_factory=list)
    frames_analyzed: int = 0
    persons_detected: int = 0
    analysis_fps: float = 0.0
    detector: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return "violation" if self.events else "no_violation"
