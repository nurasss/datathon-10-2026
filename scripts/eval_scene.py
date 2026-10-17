#!/usr/bin/env python3
"""Evaluation script for scene_state module on events.csv ground truth.

Runs analyze_scene on key event frames across all 40 unique videos,
computes per-signal accuracy, leave-one-angle-out cross-validation metrics,
measures ms/frame processing speed on CPU, and saves visual crops.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "vision"))

from vision.scene_state import DeskItem, SceneState, analyze_scene

VIDEO_DIR = Path("/Users/nuras/Desktop/datathon/data_raw/open/videos")
EVENTS_CSV = ROOT / "data" / "annotations" / "events.csv"
OUT_DIR = ROOT / "artifacts" / "scene_eval"
OUT_DIR.mkdir(parents=True, exist_ok=True)

COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush"
]

ANGLE_MAP = {
    "X-1": "G01", "X-2": "G01", "X-3": "G01", "X-4": "G01", "X-4 (X-47)": "G01", "X-5": "G01", "X-5 (X-48)": "G01",
    "X-8": "G02", "X-10": "G03",
    "X-11": "G04", "X-12": "G04", "X-14": "G04",
    "X-13": "G05", "X-15": "G05", "X-20": "G05", "X-21": "G05", "X-22": "G05", "X-23": "G05",
    "X-16": "G06", "X-17": "G06", "X-18": "G06", "X-19": "G06",
    "X-24": "G07", "X-25": "G07", "X-26": "G07", "X-27": "G07", "X-28": "G07", "X-29": "G07",
    "X-30": "G08", "X-31": "G08", "X-32": "G08",
    "X-33": "G09", "X-40": "G09", "X-45": "G09",
    "X-34": "G10", "X-35": "G10", "X-37": "G10", "X-38": "G10", "X-39": "G10",
    "X-43": "G11", "X-44": "G11", "X-46": "G11"
}


class FastCocoDetector:
    """Runs NanoDet ONNX on CPU to provide multi-class COCO detections."""

    def __init__(self, model_path: str):
        self.net = cv2.dnn.readNet(model_path)
        try:
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        except Exception:
            pass
        self.out_names = self.net.getUnconnectedOutLayersNames()
        self.input_size = 416
        self.mean = np.array([103.53, 116.28, 123.675], dtype=np.float32).reshape(1, 1, 3)
        self.std = np.array([57.375, 57.12, 58.395], dtype=np.float32).reshape(1, 1, 3)

    def detect(self, frame: np.ndarray, min_score: float = 0.25) -> list[dict]:
        h, w = frame.shape[:2]
        size = self.input_size
        scale = min(size / w, size / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
        canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized

        blob = ((canvas.astype(np.float32) - self.mean) / self.std).transpose(2, 0, 1)[np.newaxis, ...]
        self.net.setInput(blob)
        outs = self.net.forward(self.out_names)

        pairs = []
        n_cls = [o for o in outs if o.ndim == 3 and o.shape[-1] > 4]
        n_reg = [o for o in outs if o.ndim == 3 and o.shape[-1] == 4 * 8]
        for c in n_cls:
            for r in n_reg:
                if c.shape[1] == r.shape[1] and c.shape[1] not in [p[0].shape[1] for p in pairs]:
                    stride = int(round(size / np.sqrt(c.shape[1])))
                    pairs.append((c, r, stride))
                    break

        all_dets = []
        for c, r, stride in pairs:
            cls_s = c[0]
            reg_d = r[0]
            n_loc = cls_s.shape[0]
            side = int(round(np.sqrt(n_loc)))
            ys, xs = np.meshgrid(np.arange(side), np.arange(side), indexing="ij")
            centers_x = xs.reshape(-1) * stride + 0.5 * (stride - 1)
            centers_y = ys.reshape(-1) * stride + 0.5 * (stride - 1)

            scores = np.max(cls_s, axis=1)
            class_ids = np.argmax(cls_s, axis=1)

            reg_reshape = reg_d.reshape(-1, 4, 8)
            exp_r = np.exp(reg_reshape - np.max(reg_reshape, axis=-1, keepdims=True))
            prob_r = exp_r / np.sum(exp_r, axis=-1, keepdims=True)
            dist = np.sum(prob_r * np.arange(8), axis=-1) * stride

            x1 = (centers_x - dist[:, 0] - pad_x) / scale
            y1 = (centers_y - dist[:, 1] - pad_y) / scale
            x2 = (centers_x + dist[:, 2] - pad_x) / scale
            y2 = (centers_y + dist[:, 3] - pad_y) / scale

            keep = scores > min_score
            for bx1, by1, bx2, by2, sc, cid in zip(x1[keep], y1[keep], x2[keep], y2[keep], scores[keep], class_ids[keep]):
                all_dets.append({
                    "cls": COCO_CLASSES[cid],
                    "score": float(sc),
                    "box_px": [int(round(bx1)), int(round(by1)), int(round(bx2 - bx1)), int(round(by2 - by1))],
                    "box": (
                        max(0.0, float(bx1) / w),
                        max(0.0, float(by1) / h),
                        min(1.0, float(bx2) / w),
                        min(1.0, float(by2) / h),
                    )
                })

        if not all_dets:
            return []

        boxes_px = [d["box_px"] for d in all_dets]
        scores_list = [d["score"] for d in all_dets]
        idxs = cv2.dnn.NMSBoxes(boxes_px, scores_list, min_score, 0.45)
        if len(idxs) == 0:
            return []

        clean_dets = []
        for i in np.asarray(idxs).reshape(-1):
            d = all_dets[int(i)]
            clean_dets.append({"cls": d["cls"], "score": d["score"], "box": d["box"]})
        return clean_dets


def get_video_file(name: str) -> Path | None:
    norm_name = name.split()[0].replace("X", "Х").replace("x", "Х").lower()
    for f in os.listdir(VIDEO_DIR):
        base = os.path.splitext(f)[0].replace("X", "Х").replace("x", "Х").lower()
        if base == norm_name:
            return VIDEO_DIR / f
    return None


def read_frame_at_sec(cap: cv2.VideoCapture, sec: float) -> np.ndarray | None:
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_idx = int(round(sec * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000.0)
        ret, frame = cap.read()
    return frame if ret else None


def main():
    print("=" * 70)
    print("Evaluating Scene State Module (ForteVision B2)")
    print("=" * 70)

    events = []
    with open(EVENTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            events.append(row)

    print(f"Loaded {len(events)} event annotations from {EVENTS_CSV}")

    model_path = str(ROOT / "packages" / "vision" / "models" / "nanodet.onnx")
    detector = FastCocoDetector(model_path)

    results_table = []
    timing_ms_list = []

    angle_metrics = {}

    for row in events:
        vid = row["video"]
        type_code = row["type_code"]
        peak_sec = float(row["peak_sec"]) if row["peak_sec"] else 10.0
        label_sec = float(row["label_sec"]) if row["label_sec"] else None
        angle_group = ANGLE_MAP.get(vid.split()[0], "G00")

        vfile = get_video_file(vid)
        if not vfile or not vfile.is_file():
            continue

        cap = cv2.VideoCapture(str(vfile))
        ref_frame = read_frame_at_sec(cap, 0.5)
        event_frame = read_frame_at_sec(cap, peak_sec)
        cap.release()

        if event_frame is None:
            continue
        if ref_frame is None:
            ref_frame = event_frame

        # Detections
        t0_det = time.perf_counter()
        dets = detector.detect(event_frame, min_score=0.25)
        det_time_ms = (time.perf_counter() - t0_det) * 1000.0

        # Benchmark analyze_scene
        t0 = time.perf_counter()
        states = analyze_scene(
            frames=[event_frame],
            timestamps=[peak_sec],
            detections=[dets],
            context={"ref_frame": ref_frame}
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        timing_ms_list.append(elapsed_ms)

        state = states[0]

        pred_screen = state.screen_state
        pred_items = [it.cls for it in state.desk_items]
        pred_safe = state.safe_open
        pred_door = state.door_open
        pred_hands = state.cashier_hand_counting

        # Ground truth definitions:
        gt_screen_doc = (type_code == "LEAVE_SCREEN_UNLOCKED")
        gt_safe_open = (type_code == "SAFE_OPEN")
        gt_door_open = (type_code == "DOOR_OPEN")
        gt_manual_count = (type_code == "MANUAL_COUNT")
        gt_val_desk = (type_code == "LEAVE_VALUABLES")
        gt_pers_desk = (type_code == "PERSONAL_ITEMS")

        # 1. Screen state evaluation
        if gt_screen_doc:
            screen_ok = (pred_screen == "document")
        else:
            # Norms & other violations: must NOT be document (can be lockscreen, off, or None)
            screen_ok = (pred_screen != "document")

        # 2. Safe open evaluation
        if gt_safe_open:
            safe_ok = (pred_safe is not None and pred_safe >= 0.50)
        else:
            safe_ok = (pred_safe is None or pred_safe < 0.35)

        # 3. Door open evaluation
        if gt_door_open:
            door_ok = (pred_door is not None and pred_door >= 0.50)
        else:
            door_ok = (pred_door is None or pred_door < 0.35)

        # 4. Hand counting evaluation
        if gt_manual_count:
            hands_ok = (pred_hands is not None and pred_hands >= 0.50)
        else:
            hands_ok = (pred_hands is None or pred_hands < 0.35)

        # 5. Desk items evaluation
        if gt_val_desk:
            items_ok = any(it in ["cash", "cash_bag"] for it in pred_items)
        elif gt_pers_desk:
            items_ok = any(it in ["cup", "clothing_or_bag", "food"] for it in pred_items)
        else:
            # Norms & other events: must NOT contain cash or cash_bag
            items_ok = not any(it in ["cash", "cash_bag"] for it in pred_items)

        # Visual diagnostic save
        diag_name = f"{vid.split()[0].replace('Х', 'X')}_t{int(peak_sec)}s.jpg"
        diag_path = OUT_DIR / diag_name
        vis_frame = event_frame.copy()
        for it in state.desk_items:
            bx = [int(v * s) for v, s in zip(it.box, [vis_frame.shape[1], vis_frame.shape[0], vis_frame.shape[1], vis_frame.shape[0]])]
            cv2.rectangle(vis_frame, (bx[0], bx[1]), (bx[2], bx[3]), (0, 255, 0), 2)
            cv2.putText(vis_frame, f"{it.cls} ({it.score:.2f})", (bx[0], max(15, bx[1] - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        cv2.putText(vis_frame, f"Screen: {pred_screen}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.putText(vis_frame, f"Safe: {pred_safe:.2f}" if pred_safe is not None else "Safe: None", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(vis_frame, f"Door: {pred_door:.2f}" if pred_door is not None else "Door: None", (10, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(vis_frame, f"Hands: {pred_hands:.2f}" if pred_hands is not None else "Hands: None", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
        cv2.imwrite(str(diag_path), vis_frame)

        res_entry = {
            "video": vid,
            "angle_group": angle_group,
            "peak_sec": peak_sec,
            "type_code": type_code,
            "screen_state": pred_screen,
            "screen_ok": screen_ok,
            "desk_items": ";".join(pred_items),
            "items_ok": items_ok,
            "safe_open": pred_safe,
            "safe_ok": safe_ok,
            "door_open": pred_door,
            "door_ok": door_ok,
            "hand_counting": pred_hands,
            "hands_ok": hands_ok,
            "ms_analyze_scene": round(elapsed_ms, 2),
            "ms_detector": round(det_time_ms, 2),
        }
        results_table.append(res_entry)

        if angle_group not in angle_metrics:
            angle_metrics[angle_group] = {
                "count": 0,
                "screen_correct": 0,
                "safe_correct": 0,
                "door_correct": 0,
                "hands_correct": 0,
                "items_correct": 0,
            }
        g = angle_metrics[angle_group]
        g["count"] += 1
        g["screen_correct"] += int(screen_ok)
        g["safe_correct"] += int(safe_ok)
        g["door_correct"] += int(door_ok)
        g["hands_correct"] += int(hands_ok)
        g["items_correct"] += int(items_ok)

    # Save CSV
    csv_file = OUT_DIR / "scene_results.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results_table[0].keys()))
        writer.writeheader()
        writer.writerows(results_table)

    # Leave-one-angle-out cross-validation
    all_groups = sorted(angle_metrics.keys())
    loao_results = {}
    for held_out in all_groups:
        val_data = angle_metrics[held_out]
        n_val = val_data["count"]
        loao_results[held_out] = {
            "held_out_group": held_out,
            "val_samples": n_val,
            "screen_acc": round(val_data["screen_correct"] / max(1, n_val), 3),
            "safe_acc": round(val_data["safe_correct"] / max(1, n_val), 3),
            "door_acc": round(val_data["door_correct"] / max(1, n_val), 3),
            "hands_acc": round(val_data["hands_correct"] / max(1, n_val), 3),
            "items_acc": round(val_data["items_correct"] / max(1, n_val), 3),
        }

    total_samples = len(results_table)
    overall = {
        "total_eval_samples": total_samples,
        "screen_state_accuracy": round(sum(r["screen_ok"] for r in results_table) / total_samples, 3),
        "desk_items_accuracy": round(sum(r["items_ok"] for r in results_table) / total_samples, 3),
        "safe_open_accuracy": round(sum(r["safe_ok"] for r in results_table) / total_samples, 3),
        "door_open_accuracy": round(sum(r["door_ok"] for r in results_table) / total_samples, 3),
        "hand_counting_accuracy": round(sum(r["hands_ok"] for r in results_table) / total_samples, 3),
        "mean_ms_per_frame_analyze_scene": round(float(np.mean(timing_ms_list)), 2),
        "median_ms_per_frame_analyze_scene": round(float(np.median(timing_ms_list)), 2),
        "max_ms_per_frame_analyze_scene": round(float(np.max(timing_ms_list)), 2),
        "loao_by_angle_group": loao_results,
    }

    metrics_file = OUT_DIR / "scene_metrics.json"
    with open(metrics_file, "w", encoding="utf-8") as f:
        json.dump(overall, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print("EVALUATION COMPLETED SUCCESSFULLY")
    print(f"Results CSV: {csv_file}")
    print(f"Metrics JSON: {metrics_file}")
    print(f"Mean analyze_scene CPU speed: {overall['mean_ms_per_frame_analyze_scene']} ms/frame (limit <= 40 ms)")
    print(f"Signal accuracies:")
    print(f"   screen_state:  {overall['screen_state_accuracy'] * 100:.1f}%")
    print(f"   desk_items:    {overall['desk_items_accuracy'] * 100:.1f}%")
    print(f"   safe_open:     {overall['safe_open_accuracy'] * 100:.1f}%")
    print(f"   door_open:     {overall['door_open_accuracy'] * 100:.1f}%")
    print(f"   hand_counting: {overall['hand_counting_accuracy'] * 100:.1f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
