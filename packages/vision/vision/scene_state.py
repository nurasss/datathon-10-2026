"""Scene state analysis module for ForteVision.

Provides structured scene observation (laptop screen, desk items,
safe door state, room entrance door state, and cashier manual counting)
for rule validation and event detection.

Features:
- Colab YOLOX detector hook: automatically connects to ONNX model in
  /Users/nuras/Desktop/datathon/models_colab/ when available, with
  configurable toggle `enable_colab_yolox`.
- Robust CV fallback with stationary equipment suppression and temporal
  emergence detection for desk items.
- Strict confidence gates: returns None when signals are unobserved or
  angles lack clear visibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence
import cv2
import numpy as np

from .scene.screen import ScreenClassifier
from .scene.desk import DeskItem, DeskItemDetector, box_iou
from .scene.safe import SafeStateDetector
from .scene.door import DoorStateDetector
from .scene.hands import HandCountDetector
from .scene.yolox import ColabYoloxDetector

__all__ = ["DeskItem", "SceneState", "analyze_scene"]

# Module-level cached Colab YOLOX detector
_YOLOX_DETECTOR: Optional[ColabYoloxDetector] = None


def get_colab_yolox_detector() -> ColabYoloxDetector:
    global _YOLOX_DETECTOR
    if _YOLOX_DETECTOR is None:
        _YOLOX_DETECTOR = ColabYoloxDetector()
    return _YOLOX_DETECTOR


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

    # Ensure frames are uint8
    frames = [f if f.dtype == np.uint8 else np.clip(f, 0, 255).astype(np.uint8) for f in frames]
    first_frame = frames[0]
    h, w = first_frame.shape[:2]

    # Handle synthetic/toy test frames
    if h < 50 or w < 50:
        return [SceneState(t=t, screen_state=None, desk_items=[], safe_open=None, door_open=None, cashier_hand_counting=None) for t in timestamps]

    ctx = context or {}

    # Check Colab YOLOX hook
    enable_yolox = ctx.get("enable_colab_yolox", False)
    yolox = get_colab_yolox_detector() if enable_yolox else None
    use_yolox = yolox is not None and yolox.is_available()

    screen_clf = ScreenClassifier()
    desk_detector = DeskItemDetector()
    safe_detector = SafeStateDetector()
    door_detector = DoorStateDetector()
    hands_detector = HandCountDetector()

    # 1. Localize stationary scene landmarks
    flat_dets = [d for frame_dets in detections for d in frame_dets]
    desk_box = desk_detector.localize_desk(first_frame, flat_dets, ctx.get("desk_box"))

    # Stationary equipment (laptop, bill counter, desk phone, lamp, etc.)
    stationary_boxes: list[tuple[float, float, float, float]] = list(ctx.get("stationary_boxes", []))
    if not stationary_boxes:
        ref_img = ctx.get("ref_frame", first_frame)
        init_dets = ctx.get("initial_detections", [])
        if not init_dets:
            init_dets = [
                d for t_val, f_dets in zip(timestamps, detections) if t_val <= 2.5
                for d in f_dets
            ]
        stationary_boxes = desk_detector.extract_stationary_equipment(ref_img, init_dets, desk_box)

    # Initial laptop support check (desk_items only runs if laptop found in >= 50% of frames in first 3s)
    init_laptop_frames = 0
    init_total_frames = 0
    for t_val, f_dets in zip(timestamps, detections):
        if t_val <= 3.0:
            init_total_frames += 1
            if any(d.get("cls") == "laptop" and d.get("score", 0) >= 0.22 for d in f_dets):
                init_laptop_frames += 1

    if init_total_frames > 0:
        has_laptop_support = (init_laptop_frames / float(init_total_frames)) >= 0.50
    elif "has_laptop_support" in ctx:
        has_laptop_support = ctx["has_laptop_support"]
    else:
        has_laptop_support = any(
            any(d.get("cls") == "laptop" and d.get("score", 0) >= 0.22 for d in f_dets)
            for f_dets in detections
        )

    # Reference frame from early video for background comparison
    ref_frame = ctx.get("ref_frame", first_frame)

    # Localize safe (grey metallic rectangular cabinet; None if not confirmed)
    safe_box = safe_detector.localize_safe(first_frame, desk_box, ctx.get("safe_box"))

    # Localize door (by human entry/exit tracks; None if no one enters/exits)
    door_box = door_detector.localize_door(
        first_frame,
        detections,
        ctx.get("door_box"),
        ref_frame=ref_frame,
        timestamps=timestamps,
    )

    # Stationary laptop box anchored on desk surface
    stationary_laptop_box = ctx.get("stationary_laptop_box")
    if stationary_laptop_box is None:
        for f_dets in detections:
            for d in f_dets:
                if d.get("cls") == "laptop" and d.get("score", 0) >= 0.22:
                    bx = d["box"]
                    bcx = (bx[0] + bx[2]) / 2.0
                    bcy = (bx[1] + bx[3]) / 2.0
                    if 0.35 <= bcx <= 0.75 and 0.28 <= bcy <= 0.62:
                        stationary_laptop_box = bx
                        break
            if stationary_laptop_box is not None:
                break
        if stationary_laptop_box is None:
            for f_dets in detections:
                for d in f_dets:
                    if d.get("cls") == "tv" and d.get("score", 0) >= 0.20:
                        bx = d["box"]
                        bcx = (bx[0] + bx[2]) / 2.0
                        bcy = (bx[1] + bx[3]) / 2.0
                        if 0.35 <= bcx <= 0.75 and 0.28 <= bcy <= 0.62:
                            stationary_laptop_box = bx
                            break
                if stationary_laptop_box is not None:
                    break

    if stationary_laptop_box is not None and stationary_laptop_box not in stationary_boxes:
        stationary_boxes.append(stationary_laptop_box)

    # Reference frame from early video for background comparison
    ref_frame = ctx.get("ref_frame", first_frame)

    states: list[SceneState] = []
    prev_frame = None
    prev_screen_state = None

    for i, (frame, t, frame_dets) in enumerate(zip(frames, timestamps, detections)):
        is_initial = (t <= 2.5)

        # Find cashier box and scene persons
        valid_persons = []
        for d in frame_dets:
            if d.get("cls") == "person" and d.get("score", 0) >= 0.22:
                bx = d["box"]
                pw = bx[2] - bx[0]
                ph = bx[3] - bx[1]
                if ph >= 0.14:
                    valid_persons.append(d)

        cashier_box = None
        if valid_persons:
            def person_dist_to_desk(p):
                bx = p["box"]
                pcx = (bx[0] + bx[2]) / 2.0
                dcx = (desk_box[0] + desk_box[2]) / 2.0
                return -abs(pcx - dcx)
            cashier_box = max(valid_persons, key=person_dist_to_desk)["box"]

        person_boxes = [d["box"] for d in valid_persons]

        if use_yolox:
            # -------------------------------------------------------------
            # Colab YOLOX Neural Branch
            # -------------------------------------------------------------
            yolox_dets = yolox.detect(frame)
            screen_state = None
            desk_items = []
            safe_open = None
            hand_counting = None

            for yd in yolox_dets:
                ycls = yd["cls"]
                ysc = yd["score"]
                ybox = yd["box"]

                if ycls == "laptop_document":
                    screen_state = "document"
                elif ycls == "laptop_lockscreen" and screen_state != "document":
                    screen_state = "lockscreen"
                elif ycls == "safe_open":
                    safe_open = max(safe_open or 0.0, ysc)
                elif ycls == "safe_closed" and safe_open is None:
                    safe_open = 0.05
                elif ycls in {"cash", "cash_bag"}:
                    desk_items.append(DeskItem(cls=ycls, box=ybox, score=ysc))
                elif ycls in {"cup_or_food", "clothing_or_bag", "phone"}:
                    mapped_cls = "cup" if ycls == "cup_or_food" else ycls
                    desk_items.append(DeskItem(cls=mapped_cls, box=ybox, score=ysc))

            # Door open from door detector
            door_open = door_detector.estimate_open_score(frame, door_box, ref_frame)
            # Hand counting from hands detector if not detected
            if hand_counting is None:
                hand_counting = hands_detector.estimate_counting_score(
                    frame, cashier_box, desk_box, prev_frame=prev_frame
                )
        else:
            # -------------------------------------------------------------
            # Calibrated CV Branch (with stationary suppression & gates)
            # -------------------------------------------------------------
            # 1. Screen state
            laptop_candidates = [
                d["box"] for d in frame_dets
                if d.get("cls") == "laptop" and d.get("score", 0) >= 0.22
            ]
            if not laptop_candidates:
                laptop_candidates = [
                    d["box"] for d in frame_dets
                    if d.get("cls") == "tv" and d.get("score", 0) >= 0.20
                ]
            desk_laptop = None
            for lc in laptop_candidates:
                lcx = (lc[0] + lc[2]) / 2.0
                lcy = (lc[1] + lc[3]) / 2.0
                if 0.35 <= lcx <= 0.75 and 0.28 <= lcy <= 0.62:
                    desk_laptop = lc
                    stationary_laptop_box = lc
                    break

            current_laptop_box = desk_laptop or stationary_laptop_box
            screen_state = screen_clf.classify(frame, current_laptop_box)

            # Hold document state during momentary person occlusion (standing up / walking in front of screen)
            if screen_state != "document" and prev_screen_state == "document" and current_laptop_box is not None:
                if any(box_iou(pb, current_laptop_box) > 0.02 for pb in person_boxes):
                    screen_state = "document"

            prev_screen_state = screen_state

            # 2. Desk items detection (excluding stationary devices & enforcing temporal emergence)
            desk_items = desk_detector.detect_items(
                frame,
                frame_dets,
                desk_box,
                stationary_boxes=stationary_boxes,
                ref_frame=ref_frame,
                is_initial_phase=is_initial,
                cashier_box=cashier_box,
                has_laptop_support=has_laptop_support,
            )

            # 3. Safe door state estimation (returns None on non-safe angles)
            safe_open = safe_detector.estimate_open_score(
                frame, safe_box, ref_frame, person_boxes=person_boxes
            )

            # 4. Room entrance door state estimation (returns None if door not in FOV)
            door_open = door_detector.estimate_open_score(
                frame, door_box, ref_frame, person_boxes=person_boxes
            )

            # 5. Cashier hand counting estimation (returns None if uncertain)
            hand_counting = hands_detector.estimate_counting_score(
                frame, cashier_box, desk_box, prev_frame=prev_frame
            )

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
        prev_frame = frame

    # Backward/forward occlusion fill: if a window contains active document display,
    # and in an occluded frame cashier is standing directly in front of laptop, keep document
    if any(s.screen_state == "document" for s in states):
        for i, s in enumerate(states):
            if s.screen_state in {"off", "lockscreen", None} and stationary_laptop_box is not None:
                fd = detections[i]
                p_boxes = [d["box"] for d in fd if d.get("cls") == "person" and d.get("score", 0) >= 0.30]
                if any(box_iou(pb, stationary_laptop_box) > 0.02 for pb in p_boxes):
                    has_doc_near = any(
                        states[j].screen_state == "document"
                        for j in range(max(0, i - 2), min(len(states), i + 3))
                        if j != i
                    )
                    if has_doc_near:
                        s.screen_state = "document"

    return states
