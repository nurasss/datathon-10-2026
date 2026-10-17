"""Desk items detector with empty-desk background differencing.

Finds foreign items on the cashier desk surface:
- 'cash': banknote stacks / bundles
- 'cash_bag': green collection bag
- 'cup': drink cup / can / bottle
- 'phone': mobile phone on desk
- 'clothing_or_bag': outerwear jacket / coat or personal handbag/backpack
- 'food': food item / container
- 'other': other foreign items

Stationary items (banknote counter, stationary desk phone, laptop) are strictly excluded.
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


class DeskItemDetector:
    """Detects foreign items on the desk surface while suppressing stationary equipment."""

    def __init__(self):
        pass

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
            dx1 = max(0.05, best_lap[0] - 0.35)
            dy1 = max(0.20, best_lap[1] - 0.05)
            dx2 = min(0.95, best_lap[2] + 0.35)
            dy2 = min(0.85, best_lap[3] + 0.30)
            return (dx1, dy1, dx2, dy2)

        if persons:
            best_p = max(persons, key=lambda d: (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1]))["box"]
            dx1 = max(0.05, best_p[0] - 0.25)
            dy1 = max(0.25, best_p[1] + 0.20)
            dx2 = min(0.95, best_p[2] + 0.25)
            dy2 = min(0.85, dy1 + 0.35)
            return (dx1, dy1, dx2, dy2)

        return (0.15, 0.35, 0.85, 0.75)

    def detect_items(
        self,
        frame: np.ndarray,
        coco_detections: Sequence[dict],
        desk_box: tuple[float, float, float, float],
        stationary_boxes: Sequence[tuple[float, float, float, float]] = (),
        ref_frame: Optional[np.ndarray] = None,
    ) -> list[DeskItem]:
        """Detect foreign items on the desk, filtering out stationary equipment."""
        h, w = frame.shape[:2]
        items: list[DeskItem] = []

        # 1. COCO detections mapping to desk items
        coco_to_item = {
            "cup": ("cup", 0.30),
            "bottle": ("cup", 0.30),
            "cell phone": ("phone", 0.40),
            "backpack": ("clothing_or_bag", 0.48),
            "handbag": ("clothing_or_bag", 0.48),
            "sandwich": ("food", 0.35),
            "apple": ("food", 0.35),
            "orange": ("food", 0.35),
            "pizza": ("food", 0.35),
            "bowl": ("food", 0.35),
        }

        for det in coco_detections:
            cls = det.get("cls")
            score = float(det.get("score", 0.0))
            box = det.get("box")
            if not box or cls not in coco_to_item:
                continue

            item_cls, min_sc = coco_to_item[cls]
            if score < min_sc:
                continue

            bx_center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
            if not (desk_box[0] <= bx_center[0] <= desk_box[2] and desk_box[1] <= bx_center[1] <= desk_box[3]):
                continue

            if any(box_iou(box, stat) > 0.25 for stat in stationary_boxes):
                continue

            items.append(DeskItem(cls=item_cls, box=box, score=round(score, 3)))

        # 2. Difference with empty desk reference frame + color/shape analysis
        dx1 = int(round(desk_box[0] * w))
        dy1 = int(round(desk_box[1] * h))
        dx2 = int(round(desk_box[2] * w))
        dy2 = int(round(desk_box[3] * h))

        desk_crop = frame[dy1:dy2, dx1:dx2]
        if desk_crop.size == 0:
            return items

        # Check for green bag directly via color
        hsv_crop = cv2.cvtColor(desk_crop, cv2.COLOR_BGR2HSV)
        green_mask = (hsv_crop[:, :, 0] >= 38) & (hsv_crop[:, :, 0] <= 82) & (hsv_crop[:, :, 1] > 50) & (hsv_crop[:, :, 2] > 40)
        cnts_green, _ = cv2.findContours(green_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cg in cnts_green:
            if cv2.contourArea(cg) > 400:
                cx, cy, cw, ch = cv2.boundingRect(cg)
                norm_box = (float(dx1 + cx) / w, float(dy1 + cy) / h, float(dx1 + cx + cw) / w, float(dy1 + cy + ch) / h)
                if not any(box_iou(norm_box, it.box) > 0.35 for it in items):
                    items.append(DeskItem(cls="cash_bag", box=norm_box, score=0.92))

        # Check foreground difference blobs against empty desk if reference frame provided
        if ref_frame is not None:
            ref_crop = ref_frame[dy1:dy2, dx1:dx2]
            if ref_crop.shape == desk_crop.shape:
                diff = cv2.absdiff(ref_crop, desk_crop)
                gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(gray_diff, 28, 255, cv2.THRESH_BINARY)

                # Mask out stationary devices
                for stat in stationary_boxes:
                    sx1 = max(0, int(round((stat[0] - desk_box[0]) * w)))
                    sy1 = max(0, int(round((stat[1] - desk_box[1]) * h)))
                    sx2 = min(desk_crop.shape[1], int(round((stat[2] - desk_box[0]) * w)))
                    sy2 = min(desk_crop.shape[0], int(round((stat[3] - desk_box[1]) * h)))
                    if sx2 > sx1 and sy2 > sy1:
                        thresh[sy1:sy2, sx1:sx2] = 0

                cnts_diff, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for cd in cnts_diff:
                    area = cv2.contourArea(cd)
                    if area > 450:
                        cx, cy, cw, ch = cv2.boundingRect(cd)
                        norm_box = (float(dx1 + cx) / w, float(dy1 + cy) / h, float(dx1 + cx + cw) / w, float(dy1 + cy + ch) / h)
                        if any(box_iou(norm_box, it.box) > 0.35 for it in items):
                            continue

                        # Classify blob by color and shape
                        blob_hsv = hsv_crop[cy:cy + ch, cx:cx + cw]
                        if blob_hsv.size == 0:
                            continue

                        mean_val = float(np.mean(blob_hsv[:, :, 2]))
                        mean_sat = float(np.mean(blob_hsv[:, :, 1]))
                        aspect = float(cw) / max(1.0, float(ch))

                        if area > 4000 and mean_val < 65:
                            # Large dark outerwear / jacket
                            items.append(DeskItem(cls="clothing_or_bag", box=norm_box, score=0.88))
                        elif 400 < area < 3000 and 1.1 <= aspect <= 3.0 and mean_val > 115:
                            # Banknote bundle
                            items.append(DeskItem(cls="cash", box=norm_box, score=0.86))
                        elif cw < 60 and ch < 80 and aspect < 1.4:
                            # Small beverage container / cup
                            items.append(DeskItem(cls="cup", box=norm_box, score=0.82))

        return items
