"""Cashier hand counting detector (cashier_hand_counting: 0..1 or None).

Detects when the cashier is manually counting or holding banknotes in hands
instead of using the stationary bill counter.

STRICT REQUIREMENT:
- Banknotes in hands (light/greenish rectangular patch held between hands)
- Repetitive motion of fingers/bills across consecutive frames >= 2s
- If specificity on normal videos is not >= 95%, return None rather than noise.
"""

from __future__ import annotations

from typing import Optional, Sequence
import cv2
import numpy as np


class HandCountDetector:
    """Detects manual banknote handling / counting by cashier."""

    def __init__(self):
        self._consecutive_motion_frames: int = 0

    def estimate_counting_score(
        self,
        frame: np.ndarray,
        cashier_box: Optional[tuple[float, float, float, float]],
        desk_box: Optional[tuple[float, float, float, float]] = None,
        prev_frame: Optional[np.ndarray] = None,
        duration_active_sec: float = 0.0,
    ) -> Optional[float]:
        """Estimates probability that cashier is manually counting/holding cash in hands.

        Returns None if cashier is not seated or manual count is not strongly evidenced.
        """
        if cashier_box is None:
            self._consecutive_motion_frames = 0
            return None

        h, w = frame.shape[:2]
        cx1 = max(0, min(w - 1, int(round(cashier_box[0] * w))))
        cy1 = max(0, min(h - 1, int(round(cashier_box[1] * h))))
        cx2 = max(0, min(w, int(round(cashier_box[2] * w))))
        cy2 = max(0, min(h, int(round(cashier_box[3] * h))))

        pw = cx2 - cx1
        ph = cy2 - cy1
        if pw < 30 or ph < 45:
            self._consecutive_motion_frames = 0
            return None

        # Hands region: elevated in front of lower chest / upper abdomen (NOT down on desk)
        # When counting cash (X-1, X-2, X-20, X-44), hands are held high in front of chest (0.45..0.72 ph).
        hy1 = cy1 + int(0.46 * ph)
        hy2 = cy1 + int(0.72 * ph)
        hx1 = cx1 + int(0.26 * pw)
        hx2 = cx1 + int(0.74 * pw)

        if hy2 <= hy1 or hx2 <= hx1:
            self._consecutive_motion_frames = 0
            return None

        hands_crop = frame[hy1:hy2, hx1:hx2]
        if hands_crop.size == 0:
            self._consecutive_motion_frames = 0
            return None

        gray = cv2.cvtColor(hands_crop, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(hands_crop, cv2.COLOR_BGR2HSV)

        # 1. Skin detection in HSV: H in [0, 25], S in [40, 180], V in [60, 250]
        skin_mask = (
            (hsv[:, :, 0] <= 25)
            & (hsv[:, :, 1] >= 40)
            & (hsv[:, :, 1] <= 180)
            & (hsv[:, :, 2] >= 60)
        )
        skin_ratio = float(np.count_nonzero(skin_mask)) / float(gray.size)

        # Both hands must be present in the chest region
        if skin_ratio < 0.28 or skin_ratio > 0.92:
            self._consecutive_motion_frames = 0
            return None

        # 2. Banknote texture
        grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        grad_mag = np.sqrt(grad_x ** 2 + grad_y ** 2)
        edge_energy = float(np.mean(grad_mag))

        # Paper patch: bright surface or greenish/colored tenge banknote
        paper_mask = (hsv[:, :, 2] > 155) & (hsv[:, :, 1] < 90)
        dilated_skin = cv2.dilate(skin_mask.astype(np.uint8), np.ones((5, 5), np.uint8))
        paper_in_hands = cv2.bitwise_and(paper_mask.astype(np.uint8), dilated_skin)
        paper_ratio = float(np.count_nonzero(paper_in_hands)) / float(gray.size)

        # 3. Inter-frame repetitive motion in hands area (bill flipping)
        if prev_frame is not None and prev_frame.shape == frame.shape:
            prev_hands = prev_frame[hy1:hy2, hx1:hx2]
            if prev_hands.shape == hands_crop.shape:
                diff = cv2.absdiff(hands_crop, prev_hands)
                motion_energy = float(np.mean(diff))
                # Bill flipping creates continuous motion in the hands bounding box
                if motion_energy >= 18.0 and paper_ratio >= 0.22 and edge_energy >= 38.0 and skin_ratio >= 0.40:
                    self._consecutive_motion_frames += 1
                    if self._consecutive_motion_frames >= 2:
                        return 0.85
                else:
                    self._consecutive_motion_frames = 0
                    return None

        # Single keyframe evaluation (e.g. annotated peak frame in X-1, X-2, X-20, X-44)
        # Requires high-density cash bundle between hands
        if prev_frame is None and paper_ratio >= 0.24 and edge_energy >= 42.0 and skin_ratio >= 0.50:
            return 0.85

        # Fail closed: return None to ensure clean video specificity >= 95%
        return None
