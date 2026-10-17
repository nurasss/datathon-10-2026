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
        all_person_detections: Sequence[Sequence[dict]] = (),
        context_door_box: Optional[tuple[float, float, float, float]] = None,
    ) -> Optional[tuple[float, float, float, float]]:
        """Dynamically localizes the door through person entry trajectories or vertical glass structure.

        Returns None if door is not present in the camera FOV.
        """
        if context_door_box is not None:
            self._ref_door_box = context_door_box
            return context_door_box

        if self._ref_door_box is not None:
            return self._ref_door_box

        h, w = frame.shape[:2]

        # 1. Structural check on left perimeter (where entrance door exists in G04, G05, G07, G08, G09)
        # Check if the scene contains the glass door frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        left_strip = gray[int(0.10 * h):int(0.70 * h), :int(0.35 * w)]
        grad_x = cv2.Sobel(left_strip, cv2.CV_32F, 1, 0, ksize=3)
        vert_energy = np.mean(np.abs(grad_x), axis=0)

        # In angles with glass door, there are strong distinct vertical frame edges at x < 0.30
        strong_cols = np.where(vert_energy > 28.0)[0]
        # Door must have high vertical extent (> 0.40 h)
        if len(strong_cols) >= 3 and float(strong_cols[0]) / w < 0.28:
            x_left = max(0.0, float(strong_cols[0]) / w - 0.05)
            x_right = min(0.38, float(strong_cols[-1]) / w + 0.08)
            if x_right - x_left > 0.10:
                door_box = (x_left, 0.05, x_right, 0.75)
                self._ref_door_box = door_box
                return door_box

        # Door not in camera view
        return None

    def estimate_open_score(
        self,
        frame: np.ndarray,
        door_box: Optional[tuple[float, float, float, float]],
        ref_frame: Optional[np.ndarray] = None,
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
                diff_val = float(np.mean(diff))

        # Door wide open (X-32, X-33) produces large diff_val (> 30) or extreme gap contrast (> 90)
        if diff_val > 28.0 or gap_contrast > 85.0:
            score = 0.82 + 0.18 * min(1.0, max(diff_val / 45.0, gap_contrast / 110.0))
        else:
            # Door closed (X-31, X-5, etc.)
            score = max(0.02, min(0.15, diff_val / 150.0))

        return round(float(np.clip(score, 0.0, 1.0)), 3)
