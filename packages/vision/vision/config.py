"""Pipeline configuration (loaded from configs/*.json, overridable via env)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .deskobjects import DeskWatchConfig
from .zones import ZoneConfig


@dataclass
class SamplingConfig:
    """Temporal sampling of the decoded stream."""

    target_frames: int = 420
    min_fps: float = 1.5
    max_fps: float = 6.0
    #: never sample fewer than this many frames per second on very short clips
    short_clip_fps: float = 4.0
    short_clip_duration: float = 20.0
    #: decode frames down to this width for the analysis pass (evidence stays full)
    decode_max_width: int = 960
    coarse_fps: float = 1.5
    dense_fps: float = 5.0
    window_padding_sec: float = 4.0
    motion_threshold: float = 12.0
    cache_max_mb: int = 64


@dataclass
class DetectorConfig:
    model_path: str = "packages/vision/models/nanodet_416.onnx"
    model_name: str = "nanodet_plus_m_1.5x_416"
    input_size: int = 416
    min_score: float = 0.30
    nms_threshold: float = 0.55
    #: motion-blob fallback when the neural detector finds nothing on an active frame
    motion_fallback: bool = False
    motion_min_area_ratio: float = 0.006
    intra_op_num_threads: int = 1
    track_low_score: float = 0.15
    coarse_input_size: int = 416
    coarse_model_path: str = "packages/vision/models/nanodet_416.onnx"


@dataclass
class RuleEngineConfig:
    scene_threshold: float = 0.7
    door_hold_sec: float = 3.0
    min_outsider_sec: float = 0.6
    min_scene_sec: float = 0.6


@dataclass
class Ev01Config:
    """Cashier leaves the workplace (rule KZ-CASH-231-P22)."""

    enabled: bool = False
    min_absence_sec: float = 2.0
    #: gaps inside an absence shorter than this are treated as detector flicker
    absence_gap_tolerance_sec: float = 0.8
    #: window before the absence in which a client already present counts as "service in progress"
    pre_window_sec: float = 4.0
    post_window_sec: float = 2.0
    #: minimum continuous client presence near the counter to be considered a client
    min_client_sec: float = 1.0
    #: maximum gap inside a client-presence interval
    client_gap_tolerance_sec: float = 2.0
    min_confidence: float = 0.35


@dataclass
class Ev02Config:
    """Objects (visually: cash/valuables) left on the open workplace surface (KZ-CASH-231-P20)."""

    enabled: bool = False
    min_persist_sec: float = 2.0
    min_unattended_sec: float = 1.5
    min_confidence: float = 0.40


@dataclass
class EvidenceConfig:
    max_frames_per_event: int = 3
    max_width: int = 960
    jpeg_quality: int = 85


@dataclass
class PipelineConfig:
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    zones: ZoneConfig = field(default_factory=ZoneConfig)
    desk: DeskWatchConfig = field(default_factory=DeskWatchConfig)
    ev01: Ev01Config = field(default_factory=Ev01Config)
    ev02: Ev02Config = field(default_factory=Ev02Config)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    rules: RuleEngineConfig = field(default_factory=RuleEngineConfig)
    #: fixed workplace rectangle as frame fractions, disables automatic zone inference
    post_zone_override: tuple[float, float, float, float] | None = None
    max_events: int = 6
    merge_gap_sec: float = 3.0
    #: suppress an EV02 event that overlaps an EV01 event (same visible episode)
    prefer_absence_event: bool = True

    # -- (de)serialisation ---------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineConfig":
        kwargs: dict[str, Any] = {}
        nested = {
            "sampling": SamplingConfig,
            "detector": DetectorConfig,
            "zones": ZoneConfig,
            "desk": DeskWatchConfig,
            "ev01": Ev01Config,
            "ev02": Ev02Config,
            "evidence": EvidenceConfig,
            "rules": RuleEngineConfig,
        }
        for key, klass in nested.items():
            if key in data and isinstance(data[key], dict):
                allowed = {f.name for f in fields(klass)}
                kwargs[key] = klass(**{k: v for k, v in data[key].items() if k in allowed})
        for key in ("post_zone_override", "max_events", "merge_gap_sec", "prefer_absence_event"):
            if key in data:
                kwargs[key] = data[key]
        if kwargs.get("post_zone_override") is not None:
            kwargs["post_zone_override"] = tuple(kwargs["post_zone_override"])
        return cls(**kwargs)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PipelineConfig":
        if path is None:
            path = os.environ.get("FV_INFERENCE_CONFIG")
        if path is None:
            default = Path(__file__).resolve().parents[3] / "configs" / "inference.default.json"
            path = default if default.is_file() else None
        if path is None:
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = asdict(value) if is_dataclass(value) else value
        return out
