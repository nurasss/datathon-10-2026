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


def box_containment(inner_box: tuple[float, float, float, float], outer_box: tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(inner_box[0], outer_box[0]), max(inner_box[1], outer_box[1])
    ix2, iy2 = min(inner_box[2], outer_box[2]), min(inner_box[3], outer_box[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    inner_area = max(1e-6, (inner_box[2] - inner_box[0]) * (inner_box[3] - inner_box[1]))
    return inter / inner_area


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

        # 1. Check for COCO dining table (filter out customer barrier at bottom: y1 < 0.55)
        tables = [
            d for d in coco_detections
            if d.get("cls") == "dining table" and d.get("score", 0) > 0.25 and d["box"][1] < 0.55
        ]
        if tables:
            best = max(tables, key=lambda d: d.get("score", 0))
            return best["box"]

        # 2. Heuristic based on laptop/tv and person
        h, w = frame.shape[:2]
        laptops = [d for d in coco_detections if d.get("cls") in ("laptop", "tv")]
        persons = [d for d in coco_detections if d.get("cls") == "person"]

        if laptops:
            best_lap = max(laptops, key=lambda d: d.get("score", 0))["box"]
            dx1 = max(0.05, best_lap[0] - 0.35)
            dy1 = max(0.30, best_lap[1] - 0.08)
            dx2 = min(0.95, best_lap[2] + 0.35)
            dy2 = min(0.85, best_lap[3] + 0.32)
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

        # 1. Add COCO detections from initial seconds (tv, backpack, handbag, laptop, phone, etc.)
        for det in initial_detections:
            cls = det.get("cls")
            box = det.get("box")
            if not box:
                continue

            cx = (box[0] + box[2]) / 2.0
            cy = (box[1] + box[3]) / 2.0
            if not (desk_box[0] <= cx <= desk_box[2] and desk_box[1] <= cy <= desk_box[3]):
                continue

            if cls in {
                "laptop", "cell phone", "chair", "tv", "microwave", "cup",
                "bottle", "bowl", "backpack", "handbag", "book", "clock", "suitcase",
            }:
                boxes.append(box)

        # 2. Extract persistent visual blobs present from the first seconds on the desk
        h, w = initial_frame.shape[:2]
        dx1 = int(round(desk_box[0] * w))
        dy1 = int(round(desk_box[1] * h))
        dx2 = int(round(desk_box[2] * w))
        dy2 = int(round(desk_box[3] * h))
        desk_w = max(1, dx2 - dx1)
        desk_h = max(1, dy2 - dy1)
        desk_crop = initial_frame[dy1:dy2, dx1:dx2]
        if desk_crop.size > 0:
            gray_desk = cv2.cvtColor(desk_crop, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray_desk, 40, 120)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
            dilated = cv2.dilate(edges, kernel, iterations=2)
            cnts, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                area = cv2.contourArea(c)
                # Stationary equipment is a compact object, never the entire table surface
                if 1200 <= area <= 0.25 * (desk_w * desk_h):
                    bx, by, bw, bh = cv2.boundingRect(c)
                    if 25 <= bw <= 0.40 * desk_w and 25 <= bh <= 0.50 * desk_h:
                        norm_box = (
                            float(dx1 + bx) / w,
                            float(dy1 + by) / h,
                            float(dx1 + bx + bw) / w,
                            float(dy1 + by + bh) / h,
                        )
                        boxes.append(norm_box)

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
        has_laptop_support: bool = True,
    ) -> list[DeskItem]:
        """Detect foreign items on the desk, filtering out stationary equipment."""
        items: list[DeskItem] = []
        if not has_laptop_support:
            return items

        h, w = frame.shape[:2]

        # Combine stationary equipment boxes with current equipment detections
        eq_classes = {
            "tv", "backpack", "handbag", "laptop", "cell phone", "chair",
            "cup", "bottle", "bowl", "book", "clock", "suitcase",
        }
        cur_eq_boxes = [d["box"] for d in coco_detections if d.get("cls") in eq_classes]
        all_stat = list(stationary_boxes) + [b for b in self._learned_stationary_boxes if b not in stationary_boxes] + cur_eq_boxes
        all_person_boxes = [d["box"] for d in coco_detections if d.get("cls") == "person" and d.get("score", 0) >= 0.20]

        dx1 = int(round(desk_box[0] * w))
        dy1 = int(round(desk_box[1] * h))
        dx2 = int(round(desk_box[2] * w))
        dy2 = int(round(desk_box[3] * h))

        desk_crop = frame[dy1:dy2, dx1:dx2]
        if desk_crop.size == 0:
            return items

        # ---------------------------------------------------------------------
        # 1. CASH_BAG: Green collection bag detection (X-15)
        # ---------------------------------------------------------------------
        hsv_crop = cv2.cvtColor(desk_crop, cv2.COLOR_BGR2HSV)
        green_mask = (
            (hsv_crop[:, :, 0] >= 32)
            & (hsv_crop[:, :, 0] <= 95)
            & (hsv_crop[:, :, 1] >= 25)
            & (hsv_crop[:, :, 1] <= 160)
            & (hsv_crop[:, :, 2] >= 35)
            & (hsv_crop[:, :, 2] <= 140)
        )
        cnts_green, _ = cv2.findContours(
            green_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for cg in cnts_green:
            area = cv2.contourArea(cg)
            if area >= 2200:
                cx, cy, cw, ch = cv2.boundingRect(cg)
                aspect = float(cw) / max(1.0, float(ch))
                if 0.60 <= aspect <= 3.0 and cw >= 65:
                    norm_box = (
                        float(dx1 + cx) / w,
                        float(dy1 + cy) / h,
                        float(dx1 + cx + cw) / w,
                        float(dy1 + cy + ch) / h,
                    )
                    # Skip if overlapping or touching any person (cashier or customer wearing/holding bag)
                    if any(
                        (min(norm_box[2], pb[2]) > max(norm_box[0], pb[0])) and (min(norm_box[3], pb[3]) > max(norm_box[1], pb[1]))
                        for pb in all_person_boxes
                    ):
                        continue
                    bc = ((norm_box[0] + norm_box[2]) / 2.0, (norm_box[1] + norm_box[3]) / 2.0)
                    if any(point_in_box(bc, stat) for stat in all_stat):
                        continue
                    if ref_frame is not None:
                        ref_crop = ref_frame[dy1:dy2, dx1:dx2]
                        if ref_crop.shape == desk_crop.shape:
                            diff_bag = cv2.absdiff(ref_crop[cy:cy + ch, cx:cx + cw], desk_crop[cy:cy + ch, cx:cx + cw])
                            if np.mean(diff_bag) < 24.0:
                                continue
                    items.append(DeskItem(cls="cash_bag", box=norm_box, score=0.92))

        # ---------------------------------------------------------------------
        # 2. CASH: Banknote stacks left unattended on desk (X-26, X-4@68)
        # ---------------------------------------------------------------------
        if ref_frame is not None and not is_initial_phase:
            ref_crop = ref_frame[dy1:dy2, dx1:dx2]
            if ref_crop.shape == desk_crop.shape:
                diff = cv2.absdiff(ref_crop, desk_crop)
                gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
                gray_cur = cv2.cvtColor(desk_crop, cv2.COLOR_BGR2GRAY)

                paper_pixels = (gray_diff >= 18) & (gray_cur >= 212)
                # Zero out stationary equipment (padded by 0.01 margin)
                for stat in all_stat:
                    sx1 = max(0, int(round((stat[0] - desk_box[0] - 0.01) * w)))
                    sy1 = max(0, int(round((stat[1] - desk_box[1] - 0.01) * h)))
                    sx2 = min(desk_crop.shape[1], int(round((stat[2] - desk_box[0] + 0.01) * w)))
                    sy2 = min(desk_crop.shape[0], int(round((stat[3] - desk_box[1] + 0.01) * h)))
                    if sx2 > sx1 and sy2 > sy1:
                        paper_pixels[sy1:sy2, sx1:sx2] = False

                # Zero out persons and cashier arm reach on desk
                for pb in all_person_boxes:
                    px1 = max(0, int(round((pb[0] - desk_box[0]) * w - 25)))
                    py1 = max(0, int(round((pb[1] - desk_box[1]) * h - 15)))
                    px2 = min(desk_crop.shape[1], int(round((pb[2] - desk_box[0]) * w + 25)))
                    py2 = min(desk_crop.shape[0], int(round((pb[3] - desk_box[1]) * h + 25)))
                    if px2 > px1 and py2 > py1:
                        paper_pixels[py1:py2, px1:px2] = False

                if np.count_nonzero(paper_pixels) >= 35:
                    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                    closed_p = cv2.morphologyEx(paper_pixels.astype(np.uint8), cv2.MORPH_CLOSE, k)
                    cnts_p, _ = cv2.findContours(
                        closed_p, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                    )
                    for cp in cnts_p:
                        if cv2.contourArea(cp) >= 35:
                            cx, cy, cw, ch = cv2.boundingRect(cp)
                            aspect = float(cw) / max(1.0, float(ch))
                            # Real banknote stack geometry
                            if 1.4 <= aspect <= 4.5 and 15 <= cw <= 60 and 6 <= ch <= 24:
                                norm_box = (
                                    float(dx1 + cx) / w,
                                    float(dy1 + cy) / h,
                                    float(dx1 + cx + cw) / w,
                                    float(dy1 + cy + ch) / h,
                                )
                                bc = ((norm_box[0] + norm_box[2]) / 2.0, (norm_box[1] + norm_box[3]) / 2.0)
                                if not any(point_in_box(bc, s) for s in all_stat):
                                    if not any(box_iou(norm_box, pb) > 0.05 or box_containment(norm_box, pb) > 0.15 for pb in all_person_boxes):
                                        if not any(i.cls == "cash_bag" for i in items):
                                            items.append(DeskItem(cls="cash", box=norm_box, score=0.88))
                                            break

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

        # Mask out all persons on desk (cashier, walking persons, clients)
        for pb in all_person_boxes:
            cx1 = max(0, int(round((pb[0] - desk_box[0]) * w - 20)))
            cy1 = max(0, int(round((pb[1] - desk_box[1]) * h - 15)))
            cx2 = min(desk_crop.shape[1], int(round((pb[2] - desk_box[0]) * w + 20)))
            cy2 = min(desk_crop.shape[0], int(round((pb[3] - desk_box[1]) * h + 25)))
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

            if any(box_iou(norm_box, pb) > 0.05 or box_containment(norm_box, pb) > 0.10 for pb in all_person_boxes):
                continue

            # 1. Outerwear jacket / coat / large personal bag (X-40)
            if area > 6500 and mean_lum < 52.0:
                items.append(DeskItem(cls="clothing_or_bag", box=norm_box, score=0.88))
            # 2. Distinct beverage cup / can placed on desk (X-24, X-46)
            elif 650 < area < 2500 and 0.45 <= aspect <= 1.05 and ch > 34:
                items.append(DeskItem(cls="cup", box=norm_box, score=0.82))

        return items
