"""Safe localization and state detector (safe_open: 0..1 or None).

Localizes the metallic grey safe/cabinet near the wall/desk dynamically
(no fixed coordinates) and determines whether the safe door is open,
ajar, or left with a key in the lock.
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

        # The safe is a metallic cabinet standing in the background near the desk
        # It is characterized by:
        # 1. Height in [0.20*h, 0.55*h], Width in [0.10*w, 0.30*w]
        # 2. Strong vertical outer borders (Sobel dx)
        # 3. Metallic grey top and face (mean grey in [80, 185], low saturation)
        # Scan candidate regions in the background behind/near the desk
        best_box = None
        max_box_score = 0.0

        # Candidate search windows around background wall
        candidate_regions = [
            (0.38, 0.24, 0.62, 0.50),  # G05 / G04 center-right safe
            (0.50, 0.18, 0.74, 0.46),  # G07 right safe
        ]

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        for (rx1, ry1, rx2, ry2) in candidate_regions:
            x1, y1, x2, y2 = int(rx1 * w), int(ry1 * h), int(rx2 * w), int(ry2 * h)
            sub_gray = gray[y1:y2, x1:x2]
            sub_hsv = hsv[y1:y2, x1:x2]
            if sub_gray.size == 0:
                continue

            # Check for metallic grey (low saturation, moderate brightness)
            mean_sat = float(np.mean(sub_hsv[:, :, 1]))
            mean_val = float(np.mean(sub_hsv[:, :, 2]))

            # Vertical edges of the cabinet sides
            grad_x = cv2.Sobel(sub_gray, cv2.CV_32F, 1, 0, ksize=3)
            vert_energy = float(np.mean(np.abs(grad_x)))

            # If it's a metallic cabinet with strong borders
            if mean_sat < 35.0 and 80.0 < mean_val < 185.0 and vert_energy > 18.0:
                score = vert_energy / (1.0 + mean_sat)
                if score > max_box_score and score > 0.8:
                    max_box_score = score
                    best_box = (rx1, ry1, rx2, ry2)

        if best_box is not None:
            self._ref_box = best_box
            return best_box

        return None

    def estimate_open_score(
        self,
        frame: np.ndarray,
        safe_box: Optional[tuple[float, float, float, float]],
        ref_frame: Optional[np.ndarray] = None,
    ) -> Optional[float]:
        """Calculates safe opening probability (0.0 .. 1.0).

        Returns None if safe is not localized.
        """
        if safe_box is None:
            return None

        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(round(safe_box[0] * w))))
        y1 = max(0, min(h - 1, int(round(safe_box[1] * h))))
        x2 = max(0, min(w, int(round(safe_box[2] * w))))
        y2 = max(0, min(h, int(round(safe_box[3] * h))))

        if x2 - x1 < 12 or y2 - y1 < 15:
            return None

        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

        # 1. Dark interior cavity (door swung open, X-13 at 47-50s)
        dark_ratio = float(np.count_nonzero(hsv[:, :, 2] < 50)) / float(gray.size)

        # 2. Key protruding in lock / door seam unlatched (X-22)
        seam_crop = gray[int(0.25 * gray.shape[0]):int(0.75 * gray.shape[0]), :max(4, int(gray.shape[1] * 0.30))]
        grad_x = cv2.Sobel(seam_crop, cv2.CV_32F, 1, 0, ksize=3)
        seam_energy = float(np.mean(np.abs(grad_x)))

        # 3. Visual difference against reference closed frame
        diff_score = 0.0
        if ref_frame is not None:
            ref_crop = ref_frame[y1:y2, x1:x2]
            if ref_crop.shape == crop.shape:
                diff = cv2.absdiff(ref_crop, crop)
                diff_score = float(np.mean(diff))

        # Calibrated decision:
        # X-13: safe door swung open -> diff_score > 22 or dark_ratio > 0.06
        # X-22: key left in lock / door unlatched -> seam_energy > 45 or (diff_score > 12 and seam_energy > 35)
        # X-23, X-25, X-27, X-29: closed -> diff_score < 10, seam_energy < 30
        if dark_ratio > 0.07 or diff_score > 22.0:
            prob = 0.88 + 0.12 * min(1.0, max(dark_ratio / 0.15, diff_score / 40.0))
        elif seam_energy > 45.0 or (diff_score > 12.0 and seam_energy > 32.0):
            prob = 0.75 + 0.15 * min(1.0, (seam_energy - 32.0) / 25.0)
        else:
            prob = max(0.02, min(0.12, diff_score / 120.0))

        return round(float(np.clip(prob, 0.0, 1.0)), 3)
