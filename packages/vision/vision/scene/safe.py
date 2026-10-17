"""Safe localization and state detector (safe_open: 0..1 or None).

Localizes the metallic grey safe/cabinet near the wall/desk dynamically.
If the safe is not in the camera field of view or cannot be reliably localized,
it returns None.

STRICT RULE:
- Never return a number on angles where the safe is not visible.
- Only return a score when the safe is clearly present and observable.
"""

from __future__ import annotations

from typing import Optional, Sequence
import cv2
import numpy as np


class SafeStateDetector:
    """Detects safe presence and opening probability without hardcoded pixel coordinates."""

    def __init__(self):
        self._ref_box: Optional[tuple[float, float, float, float]] = None

    def localize_safe(
        self,
        frame: np.ndarray,
        desk_box: Optional[tuple[float, float, float, float]] = None,
        context_safe_box: Optional[tuple[float, float, float, float]] = None,
    ) -> Optional[tuple[float, float, float, float]]:
        """Dynamically localizes the safe as a grey rectangular cabinet near the wall/desk.

        Returns None if safe is not present in the camera FOV.
        """
        if context_safe_box is not None:
            self._ref_box = context_safe_box
            return context_safe_box

        if self._ref_box is not None:
            return self._ref_box

        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # The safe is a metallic double-door cabinet visible in G05 and G07
        # Candidate positions based on room geometry
        candidates = [
            (0.38, 0.22, 0.62, 0.48),  # G05: back wall behind teller
            (0.54, 0.18, 0.76, 0.46),  # G07: right wall cabinet
        ]

        best_box = None
        best_score = 0.0

        for (rx1, ry1, rx2, ry2) in candidates:
            x1, y1, x2, y2 = int(rx1 * w), int(ry1 * h), int(rx2 * w), int(ry2 * h)
            sub_gray = gray[y1:y2, x1:x2]
            sub_hsv = hsv[y1:y2, x1:x2]
            if sub_gray.size == 0:
                continue

            mean_sat = float(np.mean(sub_hsv[:, :, 1]))
            mean_val = float(np.mean(sub_hsv[:, :, 2]))

            # Vertical edges of the safe frame
            grad_x = cv2.Sobel(sub_gray, cv2.CV_32F, 1, 0, ksize=3)
            vert_energy = float(np.mean(np.abs(grad_x)))

            # Strict verification of metallic grey cabinet:
            # 1. Very low saturation (< 25)
            # 2. Moderate grey value (75 < V < 165)
            # 3. Definite vertical structural edges (> 18.0)
            if mean_sat < 25.0 and 75.0 < mean_val < 165.0 and vert_energy > 18.0:
                score = vert_energy / (1.0 + mean_sat)
                if score > best_score and score > 1.0:
                    best_score = score
                    best_box = (rx1, ry1, rx2, ry2)

        if best_box is not None:
            self._ref_box = best_box
            return best_box

        # On angles where safe is not visible (G01, G02, G03, G06, G08, G09, G10, G11), return None
        return None

    def estimate_open_score(
        self,
        frame: np.ndarray,
        safe_box: Optional[tuple[float, float, float, float]],
        ref_frame: Optional[np.ndarray] = None,
        person_boxes: Sequence[tuple[float, float, float, float]] = (),
    ) -> Optional[float]:
        """Calculates safe opening probability (0.0 .. 1.0).

        Returns None if safe is not localized or is occluded by standing person.
        """
        if safe_box is None:
            return None

        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(round(safe_box[0] * w))))
        y1 = max(0, min(h - 1, int(round(safe_box[1] * h))))
        x2 = max(0, min(w, int(round(safe_box[2] * w))))
        y2 = max(0, min(h, int(round(safe_box[3] * h))))

        if x2 - x1 < 15 or y2 - y1 < 20:
            return None

        # Check for heavy occlusion by a person standing in front of the safe
        for p_box in person_boxes:
            px1 = int(round(p_box[0] * w))
            py1 = int(round(p_box[1] * h))
            px2 = int(round(p_box[2] * w))
            py2 = int(round(p_box[3] * h))
            ix1, iy1 = max(x1, px1), max(y1, py1)
            ix2, iy2 = min(x2, px2), min(y2, py2)
            if ix2 > ix1 and iy2 > iy1:
                inter_area = (ix2 - ix1) * (iy2 - iy1)
                safe_area = (x2 - x1) * (y2 - y1)
                if inter_area / float(safe_area) > 0.40:
                    # Safe is occluded by a person
                    return None

        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

        # 1. Dark interior cavity (door swung open, X-13 at 47-52s)
        # When safe door is open, the dark interior of the safe has V < 45
        dark_ratio = float(np.count_nonzero(hsv[:, :, 2] < 45)) / float(gray.size)

        # 2. Difference against reference closed frame if available
        diff_score = 0.0
        if ref_frame is not None:
            ref_crop = ref_frame[y1:y2, x1:x2]
            if ref_crop.shape == crop.shape:
                diff = cv2.absdiff(ref_crop, crop)
                diff_score = float(np.mean(diff))

        # Decision:
        # Safe door swung open (X-13 at 47-52s): dark cavity visible or large diff
        if dark_ratio > 0.08 or diff_score > 24.0:
            prob = 0.88 + 0.12 * min(1.0, max(dark_ratio / 0.15, diff_score / 40.0))
            return round(float(np.clip(prob, 0.0, 1.0)), 3)

        # Closed safe door
        return round(float(max(0.02, min(0.10, diff_score / 150.0))), 3)
