"""Scene-analysis integration contract; replaced by the scene team's implementation.

Unknown is not a negative observation. This stub intentionally emits no visual
claims and cannot cause any scene-dependent rule to fire.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class DeskItem:
    cls: str
    box: tuple[float, float, float, float]
    score: float


@dataclass
class SceneState:
    t: float
    screen_state: str | None
    desk_items: list[DeskItem]
    safe_open: float | None
    door_open: float | None
    cashier_hand_counting: float | None


def analyze_scene(
    frames: list[np.ndarray],
    timestamps: list[float],
    detections: list[list[dict]],
    context: dict | None = None,
) -> list[SceneState]:
    return [SceneState(float(t), None, [], None, None, None) for t in timestamps]
