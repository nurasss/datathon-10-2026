"""Cashier hand counting detector (cashier_hand_counting: 0..1 or None).

Detects when the cashier is manually counting or holding banknotes in hands
instead of using the stationary bill counter.
"""

from __future__ import annotations
from typing import Optional, Sequence
import cv2
import numpy as np


class HandCountDetector:
    """Detects manual banknote handling / counting by cashier."""

    def __init__(self):
        pass

    def estimate_counting_score(
        self,
        frame: np.ndarray,
        cashier_box: Optional[tuple[float, float, float, float]],
        desk_box: Optional[tuple[float, float, float, float]] = None,
    ) -> Optional[float]:
        """Estimates probability that cashier is manually counting/holding cash in hands.

        Returns None if cashier is not present at the desk.
        """
        if cashier_box is None:
            return None

        h, w = frame.shape[:2]
        cx1 = max(0, min(w - 1, int(round(cashier_box[0] * w))))
        cy1 = max(0, min(h - 1, int(round(cashier_box[1] * h))))
        cx2 = max(0, min(w, int(round(cashier_box[2] * w))))
        cy2 = max(0, min(h, int(round(cashier_box[3] * h))))

        # If cashier box is too small or invalid
        if cx2 - cx1 < 25 or cy2 - cy1 < 35:
            return None

        # Hands region: lower front torso over desk
        hy1 = cy1 + int(0.45 * (cy2 - cy1))
        hy2 = cy1 + int(0.85 * (cy2 - cy1))
        hx1 = cx1 + int(0.18 * (cx2 - cx1))
        hx2 = cx1 + int(0.82 * (cx2 - cx1))

        if hy2 <= hy1 or hx2 <= hx1:
            return None

        hands_crop = frame[hy1:hy2, hx1:hx2]
        if hands_crop.size == 0:
            return None

        gray = cv2.cvtColor(hands_crop, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(hands_crop, cv2.COLOR_BGR2HSV)

        # 1. Skin mask in HSV: H in [0, 25], S in [40, 180], V in [60, 250]
        skin_mask = (hsv[:, :, 0] <= 25) & (hsv[:, :, 1] >= 40) & (hsv[:, :, 1] <= 180) & (hsv[:, :, 2] >= 60)
        skin_ratio = float(np.count_nonzero(skin_mask)) / float(gray.size)

        # 2. Banknote paper texture / patch in hands
        # Banknotes are bright, greenish/blue/cream paper: V > 140, S < 90
        grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        grad_mag = np.sqrt(grad_x ** 2 + grad_y ** 2)
        edge_energy = float(np.mean(grad_mag))

        paper_mask = (hsv[:, :, 2] > 140) & (hsv[:, :, 1] < 90)
        dilated_skin = cv2.dilate(skin_mask.astype(np.uint8), np.ones((7, 7), np.uint8))
        paper_in_hands = cv2.bitwise_and(paper_mask.astype(np.uint8), dilated_skin)
        paper_ratio = float(np.count_nonzero(paper_in_hands)) / float(gray.size)

        # Manual count signature: cashier holding paper banknote bundle in hands
        # (X-1, X-2, X-20, X-44)
        if skin_ratio > 0.08 and paper_ratio > 0.06 and edge_energy > 22.0:
            score = 0.80 + 0.20 * min(1.0, (paper_ratio - 0.06) / 0.12)
        elif skin_ratio > 0.07 and paper_ratio > 0.03 and edge_energy > 28.0:
            score = 0.65 + 0.15 * min(1.0, edge_energy / 45.0)
        else:
            # Normal typing / hands on desk
            score = max(0.02, min(0.18, edge_energy / 120.0))

        return round(float(np.clip(score, 0.0, 1.0)), 3)
