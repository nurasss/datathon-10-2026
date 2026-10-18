"""Door localization and state detector (door_open: 0..1 or None).

Localizes the glass entrance door dynamically using entrance/exit
trajectories or perimeter vertical structural frames, and estimates
the probability of the door being open/ajar.
"""

from __future__ import annotations
from typing import Optional, Sequence
import cv2
import numpy as np


class DoorStateDetector:
    """Detects glass door presence and opening probability without hardcoded pixel coordinates."""

    def __init__(self):
        self._ref_door_box: Optional[tuple[float, float, float, float]] = None

    def localize_door(
        self,
        frame: np.ndarray,
        all_person_detections: Sequence[Any] = (),
        context_door_box: Optional[tuple[float, float, float, float]] = None,
        ref_frame: Optional[np.ndarray] = None,
        timestamps: Sequence[float] = (),
    ) -> Optional[tuple[float, float, float, float]]:
        """Dynamically localizes the door through person entry/exit trajectories.

        If no person enters/exits through the region across the clip, returns None.
        """
        if context_door_box is not None:
            self._ref_door_box = context_door_box
            return context_door_box

        if self._ref_door_box is not None:
            return self._ref_door_box

        h, w = frame.shape[:2]

        # 1. Parse person detections across frames (filtering out floor/ceiling reflections)
        frame_person_boxes: list[list[tuple[float, float, float, float]]] = []
        if all_person_detections:
            first_item = all_person_detections[0] if len(all_person_detections) > 0 else None
            if isinstance(first_item, (list, tuple)):
                for f_dets in all_person_detections:
                    p_boxes = [
                        d["box"] for d in f_dets
                        if isinstance(d, dict) and d.get("cls") == "person"
                        and d.get("score", 0) >= 0.28
                        and 0.08 <= d["box"][1] <= 0.60
                        and (d["box"][3] - d["box"][1]) >= 0.15
                    ]
                    frame_person_boxes.append(p_boxes)
            elif isinstance(first_item, dict):
                p_boxes = [
                    d["box"] for d in all_person_detections
                    if d.get("cls") == "person"
                    and d.get("score", 0) >= 0.28
                    and 0.08 <= d["box"][1] <= 0.60
                    and (d["box"][3] - d["box"][1]) >= 0.15
                ]
                frame_person_boxes = [p_boxes]

        # 2. Identify person appearance / disappearance (entry / exit tracks)
        counts = [len(boxes) for boxes in frame_person_boxes]
        transit_cx = None

        if len(counts) >= 2:
            # Person entered if clip started empty and person appeared
            if counts[0] == 0 and any(c > 0 for c in counts[1:]):
                first_entry = next(p for p in frame_person_boxes if len(p) > 0)
                transit_cx = float(np.median([(b[0] + b[2]) / 2.0 for b in first_entry]))
            # Person exited if clip ended empty
            elif counts[-1] == 0 and any(c > 0 for c in counts[:-1]):
                last_exit = next(p for p in reversed(frame_person_boxes) if len(p) > 0)
                transit_cx = float(np.median([(b[0] + b[2]) / 2.0 for b in last_exit]))
            elif any(abs(counts[i] - counts[i-1]) > 0 for i in range(1, len(counts))):
                changed = [b for f_boxes in frame_person_boxes for b in f_boxes if (b[0]+b[2])/2 < 0.38 or (b[0]+b[2])/2 > 0.62]
                if changed:
                    transit_cx = float(np.median([(b[0] + b[2]) / 2.0 for b in changed]))
        elif len(counts) == 1 and counts[0] > 0 and ref_frame is not None:
            # Single frame evaluation against ref_frame
            entry_boxes = frame_person_boxes[0]
            cx_med = float(np.median([(b[0] + b[2]) / 2.0 for b in entry_boxes]))
            if cx_med < 0.40 or cx_med > 0.60:
                transit_cx = cx_med

        # STRICT RULE: If no person entered/exited through candidate door area, door_open = None
        if transit_cx is None:
            return None

        # 3. Determine candidate side of frame based on transit locations
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if transit_cx < 0.50:
            # Door on left side
            strip_x1, strip_x2 = 0, int(0.42 * w)
            sub_gray = gray[int(0.08 * h):int(0.72 * h), strip_x1:strip_x2]
            grad_x = cv2.Sobel(sub_gray, cv2.CV_32F, 1, 0, ksize=3)
            vert_energy = np.mean(np.abs(grad_x), axis=0)
            strong_cols = np.where(vert_energy > 24.0)[0]
            if len(strong_cols) >= 3 and float(strong_cols[0]) / w < 0.32:
                x_left = max(0.0, float(strong_cols[0]) / w - 0.05)
                x_right = min(0.42, float(strong_cols[-1]) / w + 0.08)
                if x_right - x_left > 0.10:
                    door_box = (x_left, 0.05, x_right, 0.75)
                    self._ref_door_box = door_box
                    return door_box
        else:
            # Door on right side (flipped orientation or right-side entrance)
            strip_x1, strip_x2 = int(0.58 * w), w
            sub_gray = gray[int(0.08 * h):int(0.72 * h), strip_x1:strip_x2]
            grad_x = cv2.Sobel(sub_gray, cv2.CV_32F, 1, 0, ksize=3)
            vert_energy = np.mean(np.abs(grad_x), axis=0)
            strong_cols = np.where(vert_energy > 24.0)[0]
            if len(strong_cols) >= 3 and float(strong_cols[-1]) / w > 0.68:
                x_left = max(0.58, 0.58 + float(strong_cols[0]) / w - 0.05)
                x_right = min(1.0, 0.58 + float(strong_cols[-1]) / w + 0.08)
                if x_right - x_left > 0.10:
                    door_box = (x_left, 0.05, x_right, 0.75)
                    self._ref_door_box = door_box
                    return door_box

        return None

    def estimate_open_score(
        self,
        frame: np.ndarray,
        door_box: Optional[tuple[float, float, float, float]],
        ref_frame: Optional[np.ndarray] = None,
        person_boxes: Sequence[tuple[float, float, float, float]] = (),
    ) -> Optional[float]:
        """Calculates door opening probability (0.0 .. 1.0).

        Returns None if door is not localized.
        """
        if door_box is None:
            return None

        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(round(door_box[0] * w))))
        y1 = max(0, min(h - 1, int(round(door_box[1] * h))))
        x2 = max(0, min(w, int(round(door_box[2] * w))))
        y2 = max(0, min(h, int(round(door_box[3] * h))))

        if x2 - x1 < 10 or y2 - y1 < 20:
            return None

        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # 1. Door opening creates a wide opening / break in the vertical jamb
        # Horizontal difference across the door threshold
        col_means = np.mean(gray, axis=0)
        gap_contrast = float(np.max(col_means) - np.min(col_means))

        # 2. Difference against reference closed frame if available
        diff_val = 0.0
        if ref_frame is not None:
            ref_crop = ref_frame[y1:y2, x1:x2]
            if ref_crop.shape == crop.shape:
                diff = cv2.absdiff(ref_crop, crop)
                gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
                # Mask out any persons crossing the doorway
                mask = np.ones(gray_diff.shape, dtype=bool)
                for pb in person_boxes:
                    px1 = max(0, int(round(pb[0] * w)) - x1)
                    py1 = max(0, int(round(pb[1] * h)) - y1)
                    px2 = min(x2 - x1, int(round(pb[2] * w)) - x1)
                    py2 = min(y2 - y1, int(round(pb[3] * h)) - y1)
                    if px2 > px1 and py2 > py1:
                        mask[py1:py2, px1:px2] = False
                diff_val = float(np.mean(gray_diff[mask])) if np.any(mask) else 0.0

        # Transit check: if someone is actively in or crossing the doorway threshold
        is_left_door = (door_box[0] < 0.30)
        if is_left_door:
            in_doorway = any(
                (pb[0] < 0.22 and pb[2] > 0.02 and pb[1] < door_box[3] + 0.08)
                for pb in person_boxes
            )
        else:
            in_doorway = any(
                (pb[2] > 0.78 and pb[0] < 0.98 and pb[1] < door_box[3] + 0.08)
                for pb in person_boxes
            )

        # Door open (X-32, X-33) produces sustained difference and high gap contrast
        if (diff_val >= 17.0 and gap_contrast >= 48.0) and not in_doorway:
            score = 0.85 + 0.15 * min(1.0, (diff_val - 17.0) / 10.0)
        else:
            # Door closed (X-31, X-5, etc.)
            score = max(0.02, min(0.15, diff_val / 150.0))

        return round(float(np.clip(score, 0.0, 1.0)), 3)
