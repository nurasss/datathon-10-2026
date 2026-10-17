"""Scene state analysis module for ForteVision.

Provides structured scene observation (laptop screen, desk items,
safe door state, room entrance door state, and cashier manual counting)
for rule validation and event detection.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Sequence
import numpy as np

from .scene.screen import ScreenClassifier
from .scene.desk import DeskItem, DeskItemDetector
from .scene.safe import SafeStateDetector
from .scene.door import DoorStateDetector
from .scene.hands import HandCountDetector

__all__ = ["DeskItem", "SceneState", "analyze_scene"]


@dataclass
class SceneState:
    """Scene observation state at a specific timestamp."""

    t: float
    screen_state: Optional[str]  # "document" | "lockscreen" | "off" | None (ноутбук не найден)
    desk_items: list[DeskItem]
    safe_open: Optional[float]  # 0..1, None если сейф не локализован
    door_open: Optional[float]  # 0..1, None если дверь не локализована
    cashier_hand_counting: Optional[float]  # 0..1 — кассир держит/перебирает купюры руками


def analyze_scene(
    frames: list[np.ndarray],
    timestamps: list[float],
    detections: list[list[dict]],  # на кадр: {"cls": str (COCO), "score": float, "box": (x1,y1,x2,y2) норм.}
    context: Optional[dict] = None,
) -> list[SceneState]:
    """Analyzes a sequence of video frames and COCO detections into SceneState objects.

    Parameters:
        frames: list of BGR video frames (numpy arrays).
        timestamps: list of presentation timestamps in seconds.
        detections: per-frame COCO detections [{"cls": str, "score": float, "box": (x1,y1,x2,y2)}].
        context: optional persistent scene context (e.g. cached desk/safe/door boxes, stationary equipment).

    Returns:
        list of SceneState dataclasses matching each frame.
    """
    if not frames:
        return []

    ctx = context or {}
    first_frame = frames[0]

    screen_clf = ScreenClassifier()
    desk_detector = DeskItemDetector()
    safe_detector = SafeStateDetector()
    door_detector = DoorStateDetector()
    hands_detector = HandCountDetector()

    # 1. Localize stationary scene landmarks dynamically
    flat_dets = [d for frame_dets in detections for d in frame_dets]
    desk_box = desk_detector.localize_desk(first_frame, flat_dets, ctx.get("desk_box"))

    # Stationary equipment (laptop, bill counter, desk phone)
    stationary_boxes: list[tuple[float, float, float, float]] = list(ctx.get("stationary_boxes", []))
    if not stationary_boxes:
        laptop_boxes = [d["box"] for d in flat_dets if d.get("cls") == "laptop" and d.get("score", 0) > 0.30]
        if laptop_boxes:
            avg_lap = (
                float(np.mean([b[0] for b in laptop_boxes])),
                float(np.mean([b[1] for b in laptop_boxes])),
                float(np.mean([b[2] for b in laptop_boxes])),
                float(np.mean([b[3] for b in laptop_boxes])),
            )
            stationary_boxes.append(avg_lap)

    # Localize safe (grey rectangular object near wall/desk)
    safe_box = safe_detector.localize_safe(first_frame, desk_box, ctx.get("safe_box"))

    # Localize door (glass area with entry/exit tracks or vertical frame)
    door_box = door_detector.localize_door(first_frame, detections, ctx.get("door_box"))

    # Reference frame for background comparison
    ref_frame = ctx.get("ref_frame", first_frame)

    states: list[SceneState] = []
    for i, (frame, t, frame_dets) in enumerate(zip(frames, timestamps, detections)):
        # 1. Screen state
        laptop_candidates = [d["box"] for d in frame_dets if d.get("cls") == "laptop" and d.get("score", 0) > 0.28]
        current_laptop_box = laptop_candidates[0] if laptop_candidates else (stationary_boxes[0] if stationary_boxes else None)
        screen_state = screen_clf.classify(frame, current_laptop_box)

        # 2. Desk items detection (excluding stationary devices)
        desk_items = desk_detector.detect_items(frame, frame_dets, desk_box, stationary_boxes, ref_frame)

        # 3. Safe door state estimation
        safe_open = safe_detector.estimate_open_score(frame, safe_box, ref_frame)

        # 4. Room door state estimation
        door_open = door_detector.estimate_open_score(frame, door_box, ref_frame)

        # 5. Cashier hand counting estimation
        # Filter genuine person detections (score >= 0.45, aspect ratio >= 1.05, height >= 0.20)
        valid_persons = []
        for d in frame_dets:
            if d.get("cls") == "person" and d.get("score", 0) >= 0.45:
                bx = d["box"]
                pw = bx[2] - bx[0]
                ph = bx[3] - bx[1]
                if ph >= 0.18 and (ph / max(0.01, pw)) >= 1.05:
                    valid_persons.append(d)

        cashier_box = None
        if valid_persons:
            # Pick person closest to the desk center
            def person_dist_to_desk(p):
                bx = p["box"]
                pcx = (bx[0] + bx[2]) / 2.0
                dcx = (desk_box[0] + desk_box[2]) / 2.0
                return -abs(pcx - dcx)
            cashier_box = max(valid_persons, key=person_dist_to_desk)["box"]

        hand_counting = hands_detector.estimate_counting_score(frame, cashier_box, desk_box)

        states.append(
            SceneState(
                t=t,
                screen_state=screen_state,
                desk_items=desk_items,
                safe_open=safe_open,
                door_open=door_open,
                cashier_hand_counting=hand_counting,
            )
        )

    return states
