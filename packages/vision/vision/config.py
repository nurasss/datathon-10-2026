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
class RulePolicy:
    enabled: bool = True
    threshold: float = 0.75
    min_frames: int = 3
    min_sec: float = 0.4
    max_gap_sec: float = 0.45
    lookback_sec: float = 2.0
    clear_frames: int = 3
    clear_sec: float = 0.4


def _rule_policies() -> dict[str, RulePolicy]:
    policies = {code: RulePolicy() for code in (
        "LEAVE_VALUABLES", "LEAVE_SCREEN_UNLOCKED", "SAFE_OPEN", "PERSONAL_ITEMS",
        "PHONE_USE", "OUTSIDER", "DOOR_OPEN", "MANUAL_COUNT")}
    policies["PERSONAL_ITEMS"] = RulePolicy(threshold=0.88, min_frames=12, min_sec=3.0)
    policies["MANUAL_COUNT"] = RulePolicy(threshold=0.95, min_frames=12, min_sec=3.0)
    policies["SAFE_OPEN"] = RulePolicy(threshold=0.95, min_frames=8, min_sec=1.4)
    policies["DOOR_OPEN"] = RulePolicy(threshold=0.8, min_frames=12, min_sec=3.0)
    policies["OUTSIDER"].threshold = 0.45
    policies["LEAVE_DURING_SERVICE"] = RulePolicy()
    return policies


@dataclass
class RuleEngineConfig:
    scene_threshold: float = 0.7
    door_hold_sec: float = 3.0
    min_outsider_sec: float = 0.6
    min_scene_sec: float = 0.6
    types: dict[str, RulePolicy] = field(default_factory=_rule_policies)
    leave_observation_sec: float = 3.0
    entry_observation_sec: float = 8.0
    merge_gap_sec: float = 3.0

    def policy(self, code: str) -> RulePolicy:
        return self.types[code]


@dataclass
class TriggerConfig:
    min_person_score: float = 0.3
    max_person_center_y: float = 0.72
    min_person_height_frac: float = 0.12
    fragment_gap_sec: float = 3.5
    noncashier_fragment_gap_sec: float = 0.8
    fragment_iou: float = 0.2
    fragment_distance_ratio: float = 0.55
    independent_overlap_sec: float = 0.6
    seat_calibration_sec: float = 6.0
    entry_calibration_sec: float = 6.0
    stand_height_ratio: float = 1.4
    stand_head_delta_frac: float = 0.05
    seat_radius_x: float = 0.45
    seat_radius_y: float = 0.5
    absence_sec: float = 2.0
    departure_hold_sec: float = 1.0
    departure_min_frames: int = 3
    onset_lookback_sec: float = 2.0
    seated_hold_sec: float = 1.0
    seated_confirmation_score: float = 0.4
    gap_tolerance_sec: float = 0.8
    entry_travel_frac: float = 0.05
    entry_inward_frac: float = 0.015
    entry_direction_window_sec: float = 4.0
    entry_progress_quantile: float = 0.9
    entry_body_frames: int = 3
    entry_body_hold_sec: float = 0.4
    entry_initial_tolerance_sec: float = 1.0
    outsider_min_sec: float = 0.6
    outsider_min_score: float = 0.45
    outsider_min_height_frac: float = 0.18
    outsider_birth_window_sec: float = 6.0
    outsider_working_lead_sec: float = 8.0
    client_seated_height_ratio: float = 0.75
    client_settle_sec: float = 2.0
    max_seated_aspect_ratio: float = 2.1
    safe_supervision_margin_frac: float = 0.08


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
class HybridConfig:
    enabled: bool = False
    input_grid: str = "observed"
    locked_hold_sec: float = 2.0
    locked_max_gap_sec: float = 0.45


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
    triggers: TriggerConfig = field(default_factory=TriggerConfig)
    hybrid: HybridConfig = field(default_factory=HybridConfig)
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
            "triggers": TriggerConfig,
            "hybrid": HybridConfig,
        }
        for key, klass in nested.items():
            if key in data and isinstance(data[key], dict):
                allowed = {f.name for f in fields(klass)}
                kwargs[key] = klass(**{k: v for k, v in data[key].items() if k in allowed})
        if "rules" in kwargs:
            raw_types = data["rules"].get("types", {})
            policies = _rule_policies()
            for code, raw in raw_types.items():
                if code not in policies or not isinstance(raw, dict):
                    raise ValueError(f"invalid rule policy: {code}")
                values = asdict(policies[code])
                values.update(raw)
                policies[code] = RulePolicy(**values)
                if policies[code].min_frames < 1 or policies[code].min_sec < 0 or policies[code].clear_frames < 1 or policies[code].clear_sec < 0 or not 0 <= policies[code].threshold <= 1:
                    raise ValueError(f"invalid stability settings: {code}")
            kwargs["rules"].types = policies
        for key in ("post_zone_override", "max_events", "merge_gap_sec", "prefer_absence_event"):
            if key in data:
                kwargs[key] = data[key]
        if kwargs.get("post_zone_override") is not None:
            kwargs["post_zone_override"] = tuple(kwargs["post_zone_override"])
        if "hybrid" in kwargs:
            hybrid = kwargs["hybrid"]
            if hybrid.input_grid not in {"coarse", "observed"}:
                raise ValueError("hybrid.input_grid must be coarse or observed")
            if hybrid.locked_hold_sec < 2 or hybrid.locked_max_gap_sec <= 0:
                raise ValueError("hybrid lockscreen hold must be >=2 seconds with a positive sample-gap limit")
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
