"""Detection of objects that stay on the workplace surface ("desk watch").

This is a model-free signal: it compares each sampled frame with a background
built from *person-free* pixels (pixel-wise median excluding person boxes), so
the detector cannot fire merely because the cashier moved away.

The signal answers one narrow question: "is there a static, person-sized-or-
smaller blob on the workplace surface that is not explained by a person and was
not there during most of the clip?"  It never claims to identify banknotes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import warnings
import numpy as np

from .types import Detection, FrameObservation, ZoneSpec

SMALL_W = 160


@dataclass
class Blob:
    """A persistent changed object on the surface."""

    first_index: int
    last_index: int
    x1: float
    y1: float
    x2: float
    y2: float
    area: float

    def iou(self, other: "Blob") -> float:
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = (
            (self.x2 - self.x1) * (self.y2 - self.y1)
            + (other.x2 - other.x1) * (other.y2 - other.y1)
            - inter
        )
        return inter / union if union > 0 else 0.0


@dataclass
class DeskWatchConfig:
    small_width: int = SMALL_W
    diff_threshold: int = 22
    min_blob_area_ratio: float = 0.02  # of the workplace ROI (in the small frame)
    min_blob_px: int = 22
    person_pad_ratio: float = 0.06
    roi_expand: float = 0.25
    blur_ksize: int = 3


@dataclass
class DeskWatchResult:
    background: np.ndarray | None = None
    valid_mask: np.ndarray | None = None
    blobs: list[Blob] = field(default_factory=list)
    per_frame_blobs: dict[int, list[Blob]] = field(default_factory=dict)
    scale: float = 1.0


class DeskWatcher:
    """Builds a person-free background and finds static foreground blobs."""

    def __init__(self, cfg: DeskWatchConfig | None = None):
        self.cfg = cfg or DeskWatchConfig()

    # -- background ------------------------------------------------------------------

    def downscale(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        scale = self.cfg.small_width / max(w, 1)
        small = cv2.resize(frame, (self.cfg.small_width, max(1, int(round(h * scale)))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if self.cfg.blur_ksize > 1:
            gray = cv2.GaussianBlur(gray, (self.cfg.blur_ksize, self.cfg.blur_ksize), 0)
        return gray

    def build_background(
        self,
        frames: list[np.ndarray],
        person_masks: list[np.ndarray],
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Pixel-wise median over frames where the pixel is not covered by a person."""

        if not frames:
            return None, None
        stack = np.stack(frames).astype(np.float32)
        masks = np.stack(person_masks)
        masked = np.where(masks, np.nan, stack)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            bg = np.nanmedian(masked, axis=0)
        valid_counts = np.sum(~masks, axis=0)
        global_median = np.median(stack, axis=0)
        bg = np.where(np.isnan(bg), global_median, bg)
        valid = valid_counts >= 3
        return bg.astype(np.float32), valid

    # -- per-frame blobs -------------------------------------------------------------

    def detect_blobs(
        self,
        gray_small: np.ndarray,
        background: np.ndarray,
        valid: np.ndarray,
        person_mask: np.ndarray,
        roi: ZoneSpec,
        frame_index: int,
        scale: float,
    ) -> list[Blob]:
        h, w = gray_small.shape[:2]
        diff = np.abs(gray_small - background)
        mask = ((diff > self.cfg.diff_threshold) & valid & (~person_mask)).astype(np.uint8)
        roi_mask = np.zeros((h, w), dtype=np.uint8)
        x1 = int(np.clip(roi.x1 * scale, 0, w - 1))
        y1 = int(np.clip(roi.y1 * scale, 0, h - 1))
        x2 = int(np.clip(roi.x2 * scale, 1, w))
        y2 = int(np.clip(roi.y2 * scale, 1, h))
        if x2 <= x1 or y2 <= y1:
            return []
        roi_mask[y1:y2, x1:x2] = 1
        mask &= roi_mask
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        roi_area = float((y2 - y1) * (x2 - x1))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs: list[Blob] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < max(self.cfg.min_blob_px, roi_area * self.cfg.min_blob_area_ratio):
                continue
            bx, by, bw, bh = cv2.boundingRect(cnt)
            blobs.append(
                Blob(
                    first_index=frame_index,
                    last_index=frame_index,
                    x1=bx / scale,
                    y1=by / scale,
                    x2=(bx + bw) / scale,
                    y2=(by + bh) / scale,
                    area=area / (scale * scale),
                )
            )
        return blobs

    def person_masks(
        self,
        detections: list[Detection],
        shape: tuple[int, int],
        scale: float,
    ) -> np.ndarray:
        """Boolean mask (small-frame resolution) of person boxes, dilated."""

        h, w = shape
        mask = np.zeros((h, w), dtype=bool)
        frame_area = float(h * w)
        for det in detections:
            pad_x = det.width * self.cfg.person_pad_ratio
            pad_y = det.height * self.cfg.person_pad_ratio
            x1 = int(np.clip((det.x1 - pad_x) * scale, 0, w - 1))
            y1 = int(np.clip((det.y1 - pad_y) * scale, 0, h - 1))
            x2 = int(np.clip((det.x2 + pad_x) * scale, 1, w))
            y2 = int(np.clip((det.y2 + pad_y) * scale, 1, h))
            if (x2 - x1) * (y2 - y1) > 0.85 * frame_area:
                continue  # ignore degenerate full-frame boxes
            mask[y1:y2, x1:x2] = True
        return mask

    def foreground_ratio(
        self,
        gray_small: np.ndarray,
        background: np.ndarray,
        valid: np.ndarray,
        person_mask: np.ndarray,
        roi: ZoneSpec,
        scale: float,
    ) -> float:
        """Share of workplace-ROI pixels that differ from the person-free background.

        A diagnostic signal only: it rises when a new object appears on the
        surface and is used for development/reporting, not for verdicts.
        """

        h, w = gray_small.shape[:2]
        x1 = int(np.clip(roi.x1 * scale, 0, w - 1))
        y1 = int(np.clip(roi.y1 * scale, 0, h - 1))
        x2 = int(np.clip(roi.x2 * scale, 1, w))
        y2 = int(np.clip(roi.y2 * scale, 1, h))
        if x2 <= x1 or y2 <= y1:
            return 0.0
        diff = np.abs(gray_small[y1:y2, x1:x2] - background[y1:y2, x1:x2])
        changed = (diff > self.cfg.diff_threshold) & valid[y1:y2, x1:x2] & (~person_mask[y1:y2, x1:x2])
        return float(changed.mean())

    # -- temporal association --------------------------------------------------------

    def track_blobs(self, per_frame: dict[int, list[Blob]], frames: list[FrameObservation]) -> list[Blob]:
        """Link per-frame blobs into persistent object tracks."""

        tracks: list[Blob] = []
        order = [obs.frame_index for obs in frames]
        for idx in order:
            current = per_frame.get(idx, [])
            used: set[int] = set()
            for blob in current:
                best, best_iou = None, 0.2
                for t in tracks:
                    if t.last_index < idx - 2:
                        continue
                    score = t.iou(blob)
                    if score > best_iou:
                        best, best_iou = t, score
                if best is not None:
                    best.last_index = idx
                    # adapt size to the latest observation
                    best.x1, best.y1, best.x2, best.y2 = blob.x1, blob.y1, blob.x2, blob.y2
                    best.area = blob.area
                    used.add(id(blob))
                else:
                    tracks.append(blob)
                    used.add(id(blob))
        return [t for t in tracks if t.last_index > t.first_index]
