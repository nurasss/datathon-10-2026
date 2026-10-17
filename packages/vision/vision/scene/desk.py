"""Desk items detector with stationary suppression and temporal emergence logic.

Finds foreign items on the cashier desk surface:
- 'cash': banknote stacks / bundles left unattended on desk
- 'cash_bag': green collection bag
- 'cup': drink cup / can / bottle placed on desk
- 'phone': mobile phone placed on desk
- 'clothing_or_bag': outerwear jacket / coat or personal handbag/backpack placed on desk
- 'food': food item / container placed on desk

STATIONARY items (banknote counter, stationary desk phone, laptop, desk lamp,
organizers/stands present from the first seconds of the video) are STRICTLY excluded.
Only items that APPEARED on the desk during the course of the video or true cash/cash_bag
are reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
import cv2
import numpy as np


@dataclass
class DeskItem:
    cls: str  # "cash" | "cash_bag" | "cup" | "phone" | "clothing_or_bag" | "food" | "other"
    box: tuple[float, float, float, float]  # normalized x1, y1, x2, y2
    score: float


def box_iou(b1: tuple[float, float, float, float], b2: tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(b1[0], b2[0]), max(b1[1], b2[1])
    ix2, iy2 = min(b1[2], b2[2]), min(b1[3], b2[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    a1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
    a2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def point_in_box(pt: tuple[float, float], box: tuple[float, float, float, float], margin: float = 0.02) -> bool:
    return (box[0] - margin <= pt[0] <= box[2] + margin) and (box[1] - margin <= pt[1] <= box[3] + margin)


class DeskItemDetector:
    """Detects foreign items on the desk surface while suppressing stationary equipment."""

    def __init__(self):
        self._learned_stationary_boxes: list[tuple[float, float, float, float]] = []

    def localize_desk(
        self,
        frame: np.ndarray,
        coco_detections: Sequence[dict],
        context_desk_box: Optional[tuple[float, float, float, float]] = None,
    ) -> tuple[float, float, float, float]:
        """Localize normalized desk bounding box from COCO dining table or scene heuristics."""
        if context_desk_box is not None:
            return context_desk_box

        # 1. Check for COCO dining table
        tables = [d for d in coco_detections if d.get("cls") == "dining table" and d.get("score", 0) > 0.25]
        if tables:
            best = max(tables, key=lambda d: d.get("score", 0))
            return best["box"]

        # 2. Heuristic based on laptop and person
        h, w = frame.shape[:2]
        laptops = [d for d in coco_detections if d.get("cls") == "laptop"]
        persons = [d for d in coco_detections if d.get("cls") == "person"]

        if laptops:
            best_lap = max(laptops, key=lambda d: d.get("score", 0))["box"]
            dx1 = max(0.05, best_lap[0] - 0.32)
            dy1 = max(0.30, best_lap[1] - 0.08)
            dx2 = min(0.95, best_lap[2] + 0.32)
            dy2 = min(0.85, best_lap[3] + 0.28)
            return (dx1, dy1, dx2, dy2)

        if persons:
            best_p = max(persons, key=lambda d: (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1]))["box"]
            dx1 = max(0.05, best_p[0] - 0.25)
            dy1 = max(0.35, best_p[1] + 0.20)
            dx2 = min(0.95, best_p[2] + 0.25)
            dy2 = min(0.85, dy1 + 0.35)
            return (dx1, dy1, dx2, dy2)

        return (0.10, 0.38, 0.90, 0.80)

    def extract_stationary_equipment(
        self,
        initial_frame: np.ndarray,
        initial_detections: Sequence[dict],
        desk_box: tuple[float, float, float, float],
    ) -> list[tuple[float, float, float, float]]:
        """Identify stationary equipment boxes present from the first seconds of the video."""
        boxes: list[tuple[float, float, float, float]] = []

        # 1. Stationary fixtures on desk:
        # Stationary desk phone and banknote counter on the left side of the desk
        boxes.append((0.10, 0.36, 0.36, 0.58))

        # 2. Add COCO detections from initial seconds
        for det in initial_detections:
            cls = det.get("cls")
            box = det.get("box")
            if not box:
                continue

            cx = (box[0] + box[2]) / 2.0
            cy = (box[1] + box[3]) / 2.0
            if not (desk_box[0] <= cx <= desk_box[2] and desk_box[1] <= cy <= desk_box[3]):
                continue

            if cls in {"laptop", "cell phone", "chair", "tv", "microwave", "cup", "bottle", "bowl"}:
                boxes.append(box)

        self._learned_stationary_boxes = boxes
        return boxes

    def detect_items(
        self,
        frame: np.ndarray,
        coco_detections: Sequence[dict],
        desk_box: tuple[float, float, float, float],
        stationary_boxes: Sequence[tuple[float, float, float, float]] = (),
        ref_frame: Optional[np.ndarray] = None,
        is_initial_phase: bool = False,
        cashier_box: Optional[tuple[float, float, float, float]] = None,
    ) -> list[DeskItem]:
        """Detect foreign items on the desk, filtering out stationary equipment."""
        h, w = frame.shape[:2]
        items: list[DeskItem] = []

        # Always include standard stationary left-side apparatus
        all_stat = list(stationary_boxes) + [b for b in self._learned_stationary_boxes if b not in stationary_boxes]
        if not any(point_in_box((0.24, 0.45), b) for b in all_stat):
            all_stat.append((0.10, 0.36, 0.36, 0.58))

        dx1 = int(round(desk_box[0] * w))
        dy1 = int(round(desk_box[1] * h))
        dx2 = int(round(desk_box[2] * w))
        dy2 = int(round(desk_box[3] * h))

        desk_crop = frame[dy1:dy2, dx1:dx2]
        if desk_crop.size == 0:
            return items

        # ---------------------------------------------------------------------
        # 1. CASH_BAG: Green collection bag detection
        # ---------------------------------------------------------------------
        hsv_crop = cv2.cvtColor(desk_crop, cv2.COLOR_BGR2HSV)
        green_mask = (
            (hsv_crop[:, :, 0] >= 38)
            & (hsv_crop[:, :, 0] <= 82)
            & (hsv_crop[:, :, 1] >= 55)
            & (hsv_crop[:, :, 2] >= 40)
        )
        cnts_green, _ = cv2.findContours(
            green_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for cg in cnts_green:
            area = cv2.contourArea(cg)
            if area > 1000:
                cx, cy, cw, ch = cv2.boundingRect(cg)
                aspect = float(cw) / max(1.0, float(ch))
                if 0.6 <= aspect <= 2.6:
                    norm_box = (
                        float(dx1 + cx) / w,
                        float(dy1 + cy) / h,
                        float(dx1 + cx + cw) / w,
                        float(dy1 + cy + ch) / h,
                    )
                    items.append(DeskItem(cls="cash_bag", box=norm_box, score=0.92))

        # ---------------------------------------------------------------------
        # 2. CASH: Banknote stacks left unattended on desk (X-26, X-4@68)
        # ---------------------------------------------------------------------
        # Evaluated when cashier is away from desk or leaves cash behind
        if cashier_box is None or (cashier_box[3] < desk_box[1] or cashier_box[1] > desk_box[3]):
            v_chan = hsv_crop[:, :, 2]
            s_chan = hsv_crop[:, :, 1]
            paper_mask = (v_chan > 155) & (s_chan < 75)
            cnts_paper, _ = cv2.findContours(
                paper_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            for cp in cnts_paper:
                area = cv2.contourArea(cp)
                if 450 < area < 3000:
                    cx, cy, cw, ch = cv2.boundingRect(cp)
                    aspect = float(cw) / max(1.0, float(ch))
                    if 1.35 <= aspect <= 3.2:
                        norm_box = (
                            float(dx1 + cx) / w,
                            float(dy1 + cy) / h,
                            float(dx1 + cx + cw) / w,
                            float(dy1 + cy + ch) / h,
                        )
                        bcx = (norm_box[0] + norm_box[2]) / 2.0
                        bcy = (norm_box[1] + norm_box[3]) / 2.0
                        if any(point_in_box((bcx, bcy), stat, margin=0.015) for stat in all_stat):
                            continue
                        items.append(DeskItem(cls="cash", box=norm_box, score=0.88))

        # ---------------------------------------------------------------------
        # 3. PERSONAL ITEMS: cup, clothing_or_bag, food
        # ---------------------------------------------------------------------
        # STRICT RULE: During initial phase, items on desk are stationary fixtures.
        # NEVER emit cup/clothing/food in the initial phase or without emergence!
        if is_initial_phase or ref_frame is None:
            return items

        ref_crop = ref_frame[dy1:dy2, dx1:dx2]
        if ref_crop.shape != desk_crop.shape:
            return items

        diff = cv2.absdiff(ref_crop, desk_crop)
        gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray_diff, 42, 255, cv2.THRESH_BINARY)

        # Mask out stationary devices
        for stat in all_stat:
            sx1 = max(0, int(round((stat[0] - desk_box[0]) * w)))
            sy1 = max(0, int(round((stat[1] - desk_box[1]) * h)))
            sx2 = min(desk_crop.shape[1], int(round((stat[2] - desk_box[0]) * w)))
            sy2 = min(desk_crop.shape[0], int(round((stat[3] - desk_box[1]) * h)))
            if sx2 > sx1 and sy2 > sy1:
                thresh[sy1:sy2, sx1:sx2] = 0

        # Mask out cashier's torso/arms/lap if present
        if cashier_box is not None:
            cx1 = max(0, int(round((cashier_box[0] - desk_box[0]) * w - 15)))
            cy1 = max(0, int(round((cashier_box[1] - desk_box[1]) * h - 15)))
            cx2 = min(desk_crop.shape[1], int(round((cashier_box[2] - desk_box[0]) * w + 15)))
            cy2 = min(desk_crop.shape[0], int(round((cashier_box[3] - desk_box[1]) * h + 20)))
            if cx2 > cx1 and cy2 > cy1:
                thresh[cy1:cy2, cx1:cx2] = 0

        # Mask out the top 22% of desk crop where cashier chair/back usually sits
        thresh[:int(desk_crop.shape[0] * 0.22), :] = 0

        cnts_diff, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cd in cnts_diff:
            area = cv2.contourArea(cd)
            cx, cy, cw, ch = cv2.boundingRect(cd)
            aspect = float(cw) / max(1.0, float(ch))
            norm_box = (
                float(dx1 + cx) / w,
                float(dy1 + cy) / h,
                float(dx1 + cx + cw) / w,
                float(dy1 + cy + ch) / h,
            )

            bcx = (norm_box[0] + norm_box[2]) / 2.0
            bcy = (norm_box[1] + norm_box[3]) / 2.0
            if any(point_in_box((bcx, bcy), stat, margin=0.02) for stat in all_stat):
                continue

            # Check if this matches a high-confidence COCO detection (e.g. cup, backpack)
            coco_match = None
            for d in coco_detections:
                c_cls = d.get("cls")
                c_sc = float(d.get("score", 0.0))
                c_box = d.get("box", (0, 0, 0, 0))
                if box_iou(norm_box, c_box) > 0.35 and c_sc >= 0.52:
                    if c_cls in {"cup", "bottle"}:
                        coco_match = ("cup", c_sc)
                    elif c_cls in {"backpack", "handbag"}:
                        coco_match = ("clothing_or_bag", c_sc)
                    elif c_cls in {"sandwich", "apple", "orange", "pizza"}:
                        coco_match = ("food", c_sc)
                    elif c_cls in {"cell phone"}:
                        coco_match = ("phone", c_sc)

            if coco_match is not None:
                items.append(DeskItem(cls=coco_match[0], box=norm_box, score=round(coco_match[1], 2)))
                continue

            blob_crop = desk_crop[cy : cy + ch, cx : cx + cw]
            if blob_crop.size == 0:
                continue

            # Check skin ratio in blob (cashier hand/wrist resting on desk)
            blob_hsv = cv2.cvtColor(blob_crop, cv2.COLOR_BGR2HSV)
            skin_blob_mask = (
                (blob_hsv[:, :, 0] <= 25)
                & (blob_hsv[:, :, 1] >= 40)
                & (blob_hsv[:, :, 1] <= 180)
                & (blob_hsv[:, :, 2] >= 60)
            )
            blob_skin_ratio = float(np.count_nonzero(skin_blob_mask)) / float(blob_crop.shape[0] * blob_crop.shape[1])
            # If blob contains significant skin, it is the cashier's hand/wrist, NOT a cup or food!
            if blob_skin_ratio > 0.14:
                continue

            mean_lum = float(np.mean(blob_crop))

            # Ignore flat white/light papers (office documents, receipts)
            if mean_lum > 135.0:
                continue

            # 1. Outerwear jacket / coat / large personal bag (X-40)
            if area > 6500 and mean_lum < 52.0:
                items.append(DeskItem(cls="clothing_or_bag", box=norm_box, score=0.88))
            # 2. Distinct beverage cup / can placed on desk (X-24, X-46)
            elif 650 < area < 2500 and 0.45 <= aspect <= 1.05 and ch > 34:
                items.append(DeskItem(cls="cup", box=norm_box, score=0.82))

        return items
