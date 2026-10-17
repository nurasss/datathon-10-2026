"""Laptop screen state classifier.

Classifies the laptop screen state into:
- 'document': active spreadsheet, document, white application window
- 'lockscreen': lockscreen wallpaper (e.g. blue Windows wallpaper, lockscreen photo + clock)
- 'off': display powered off or laptop lid closed flat
- None: laptop not localized / not found in the frame
"""

from __future__ import annotations
from typing import Optional, Sequence
import cv2
import numpy as np


class ScreenClassifier:
    """Classifies laptop screen state from laptop bounding box crop."""

    def __init__(
        self,
        off_brightness_threshold: float = 40.0,
        doc_white_ratio_threshold: float = 0.20,
        doc_brightness_threshold: float = 135.0,
    ):
        self.off_thresh = off_brightness_threshold
        self.doc_white_thresh = doc_white_ratio_threshold
        self.doc_bright_thresh = doc_brightness_threshold

    def classify(
        self,
        frame: np.ndarray,
        laptop_box: Optional[tuple[float, float, float, float]],
    ) -> Optional[str]:
        """Classify screen state given BGR frame and normalized laptop bbox (x1, y1, x2, y2)."""
        if laptop_box is None:
            return None

        h, w = frame.shape[:2]
        x1 = max(0, min(w - 1, int(round(laptop_box[0] * w))))
        y1 = max(0, min(h - 1, int(round(laptop_box[1] * h))))
        x2 = max(0, min(w, int(round(laptop_box[2] * w))))
        y2 = max(0, min(h, int(round(laptop_box[3] * h))))

        if x2 - x1 < 8 or y2 - y1 < 8:
            return None

        # The screen is typically the upper ~65% of the laptop box
        screen_h = int((y2 - y1) * 0.65)
        # Margin of 5% on sides to avoid desk background
        margin_x = int((x2 - x1) * 0.05)
        sx1 = x1 + margin_x
        sx2 = x2 - margin_x
        sy1 = y1
        sy2 = y1 + screen_h

        if sx2 <= sx1 or sy2 <= sy1:
            return None

        crop = frame[sy1:sy2, sx1:sx2]
        if crop.size == 0:
            return None

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

        mean_val = float(np.mean(gray))
        max_val = float(np.max(gray))

        # 1. Check if off / closed
        if mean_val < self.off_thresh or max_val < 55.0:
            return "off"

        # 2. Check for document (bright desaturated / white pixels typical of Excel/Word/tables)
        # In HSV: V > 165 and S < 60
        v_chan = hsv[:, :, 2]
        s_chan = hsv[:, :, 1]
        white_mask = (v_chan > 165) & (s_chan < 60)
        white_ratio = float(np.count_nonzero(white_mask)) / float(gray.size)

        # High brightness desaturated area indicates document
        if white_ratio >= self.doc_white_thresh or (mean_val >= self.doc_bright_thresh and white_ratio >= 0.12):
            return "document"

        # 3. Otherwise, screen is illuminated but has wallpaper / lockscreen pattern
        return "lockscreen"
