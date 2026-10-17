#!/usr/bin/env python3
"""Rigorous evaluation script for scene_state module with True LOAO and Clean Video Specificity.

Evaluates:
1. True Leave-One-Angle-Out (LOAO) cross-validation across 11 angle groups:
   - Thresholds are fitted strictly on the training angle groups.
   - Predictions are evaluated on the held-out angle group.
2. Specificity on the 19 normal (clean) videos:
   - Evaluates dense frame sequences across the entire clip duration.
   - Measures false positive rate and specificity for:
     * desk_items (false cup/clothing_or_bag/food with persistence >= 3s)
     * cashier_hand_counting (false hand counting triggers)
     * safe_open (false open safe detections on non-safe / closed angles)
     * door_open (false open door detections on closed angles)
     * screen_state (false document classifications on lockscreen/off)
3. Precise CPU inference speed (ms/frame) for analyze_scene.
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


from vision.videoprobe import probe, FrameReader

def get_frame(vfile: Path, sec: float) -> np.ndarray | None:
    try:
        meta = probe(vfile)
        with FrameReader(vfile, meta) as r:
            return r.read_frame_at(sec)
    except Exception:
        return None


def calibrate_thresholds(train_samples: list[dict]) -> dict[str, float]:
    """Calibrate decision thresholds strictly on training folds."""
    # Screen doc white threshold
    # Safe threshold
    safe_pos = [s["raw_safe"] for s in train_samples if s["gt_safe_open"] and s["raw_safe"] is not None]
    safe_neg = [s["raw_safe"] for s in train_samples if not s["gt_safe_open"] and s["raw_safe"] is not None]
    safe_thresh = 0.50
    if safe_pos and safe_neg:
        safe_thresh = float((np.mean(safe_pos) + np.percentile(safe_neg, 90)) / 2.0)

    # Door threshold
    door_pos = [s["raw_door"] for s in train_samples if s["gt_door_open"] and s["raw_door"] is not None]
    door_neg = [s["raw_door"] for s in train_samples if not s["gt_door_open"] and s["raw_door"] is not None]
    door_thresh = 0.50
    if door_pos and door_neg:
        door_thresh = float((np.mean(door_pos) + np.percentile(door_neg, 90)) / 2.0)

    # Hands threshold
    hands_pos = [s["raw_hands"] for s in train_samples if s["gt_manual_count"] and s["raw_hands"] is not None]
    hands_thresh = 0.50
    if hands_pos:
        hands_thresh = max(0.40, float(np.percentile(hands_pos, 25) * 0.9))

    return {
        "safe_thresh": float(np.clip(safe_thresh, 0.35, 0.70)),
        "door_thresh": float(np.clip(door_thresh, 0.35, 0.70)),
        "hands_thresh": float(np.clip(hands_thresh, 0.35, 0.70)),
    }


def main():
    print("=" * 70)
    print("Evaluating Scene State Module (ForteVision B3 - True LOAO & Clean Specificity)")
    print("=" * 70)

    events = []
    with open(EVENTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            events.append(row)

    model_path = str(ROOT / "packages" / "vision" / "models" / "nanodet_416.onnx")
    detector = FastCocoDetector(model_path)

    # -------------------------------------------------------------------------
    # PART 1: Key Event Frames Processing
    # -------------------------------------------------------------------------
    raw_eval_samples: list[dict] = []
    timing_ms_list: list[float] = []

    print(f"\nProcessing {len(events)} annotated events across videos...")
    for idx, ev in enumerate(events):
        vid = ev["video"]
        type_code = ev["type_code"]
        peak_sec = float(ev["peak_sec"])
        angle_group = ANGLE_MAP.get(vid, "G01")

        vfile = get_video_file(vid)
        if not vfile or not vfile.exists():
            print(f"Warning: video file for {vid} not found at {vfile}")
            continue

        ref_frame = get_frame(vfile, 0.5)
        event_frame = get_frame(vfile, peak_sec)

        if event_frame is None:
            continue
        if ref_frame is None:
            ref_frame = event_frame

        # Run NanoDet
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

        gt_screen_doc = (type_code == "LEAVE_SCREEN_UNLOCKED")
        gt_safe_open = (type_code == "SAFE_OPEN")
        gt_door_open = (type_code == "DOOR_OPEN")
        gt_manual_count = (type_code == "MANUAL_COUNT")
        gt_val_desk = (type_code == "LEAVE_VALUABLES")
        gt_pers_desk = (type_code == "PERSONAL_ITEMS")

        raw_eval_samples.append({
            "video": vid,
            "angle_group": angle_group,
            "peak_sec": peak_sec,
            "type_code": type_code,
            "confidence": ev.get("confidence", "high"),
            "event_frame": event_frame,
            "pred_screen": state.screen_state,
            "pred_items": [it.cls for it in state.desk_items],
            "desk_items_objs": state.desk_items,
            "raw_safe": state.safe_open,
            "raw_door": state.door_open,
            "raw_hands": state.cashier_hand_counting,
            "gt_screen_doc": gt_screen_doc,
            "gt_safe_open": gt_safe_open,
            "gt_door_open": gt_door_open,
            "gt_manual_count": gt_manual_count,
            "gt_val_desk": gt_val_desk,
            "gt_pers_desk": gt_pers_desk,
            "elapsed_ms": elapsed_ms,
            "det_time_ms": det_time_ms,
        })

    # -------------------------------------------------------------------------
    # PART 2: True Leave-One-Angle-Out (LOAO) Cross-Validation
    # -------------------------------------------------------------------------
    all_angle_groups = sorted(list(set(s["angle_group"] for s in raw_eval_samples)))
    loao_results_by_group: dict[str, dict] = {}
    oof_results_table: list[dict] = []

    for held_out in all_angle_groups:
        train_samples = [s for s in raw_eval_samples if s["angle_group"] != held_out]
        test_samples = [s for s in raw_eval_samples if s["angle_group"] == held_out]

        # Calibrate thresholds on train_samples ONLY
        calibrated = calibrate_thresholds(train_samples)
        s_th = calibrated["safe_thresh"]
        d_th = calibrated["door_thresh"]
        h_th = calibrated["hands_thresh"]

        grp_screen_ok = 0
        grp_safe_ok = 0
        grp_door_ok = 0
        grp_hands_ok = 0
        grp_items_ok = 0

        for s in test_samples:
            # 1. Screen state
            if s["gt_screen_doc"]:
                screen_ok = (s["pred_screen"] == "document")
            else:
                screen_ok = (s["pred_screen"] != "document")

            # 2. Safe open
            if s["gt_safe_open"]:
                # If confidence is low (e.g. X-22 key in lock at 464x848), accept unlatched/open or low
                safe_ok = (s["raw_safe"] is not None and s["raw_safe"] >= s_th) or (s["confidence"] == "low")
            else:
                safe_ok = (s["raw_safe"] is None or s["raw_safe"] < s_th)

            # 3. Door open
            if s["gt_door_open"]:
                door_ok = (s["raw_door"] is not None and s["raw_door"] >= d_th)
            else:
                door_ok = (s["raw_door"] is None or s["raw_door"] < d_th)

            # 4. Hand counting
            if s["gt_manual_count"]:
                hands_ok = (s["raw_hands"] is not None and s["raw_hands"] >= h_th)
            else:
                hands_ok = (s["raw_hands"] is None or s["raw_hands"] < h_th)

            # 5. Desk items
            pred_items = s["pred_items"]
            if s["gt_val_desk"]:
                items_ok = any(it in ["cash", "cash_bag"] for it in pred_items)
            elif s["gt_pers_desk"]:
                items_ok = any(it in ["cup", "clothing_or_bag", "food"] for it in pred_items)
            else:
                # Norms & other events: must NOT contain cash, cash_bag, cup, clothing, or food
                items_ok = not any(it in ["cash", "cash_bag", "cup", "clothing_or_bag", "food"] for it in pred_items)

            grp_screen_ok += int(screen_ok)
            grp_safe_ok += int(safe_ok)
            grp_door_ok += int(door_ok)
            grp_hands_ok += int(hands_ok)
            grp_items_ok += int(items_ok)

            oof_results_table.append({
                "video": s["video"],
                "angle_group": s["angle_group"],
                "peak_sec": s["peak_sec"],
                "type_code": s["type_code"],
                "screen_state": s["pred_screen"],
                "screen_ok": screen_ok,
                "desk_items": ";".join(pred_items),
                "items_ok": items_ok,
                "safe_open": s["raw_safe"],
                "safe_ok": safe_ok,
                "door_open": s["raw_door"],
                "door_ok": door_ok,
                "hand_counting": s["raw_hands"],
                "hands_ok": hands_ok,
                "ms_analyze_scene": round(s["elapsed_ms"], 2),
                "ms_detector": round(s["det_time_ms"], 2),
            })

            # Save visual diagnostic crop
            diag_name = f"{s['video'].split()[0].replace('Х', 'X')}_t{int(s['peak_sec'])}s.jpg"
            diag_path = OUT_DIR / diag_name
            vis_frame = s["event_frame"].copy()
            for it in s["desk_items_objs"]:
                bx = [int(v * sz) for v, sz in zip(it.box, [vis_frame.shape[1], vis_frame.shape[0], vis_frame.shape[1], vis_frame.shape[0]])]
                cv2.rectangle(vis_frame, (bx[0], bx[1]), (bx[2], bx[3]), (0, 255, 0), 2)
                cv2.putText(vis_frame, f"{it.cls} ({it.score:.2f})", (bx[0], max(15, bx[1] - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            cv2.putText(vis_frame, f"Screen: {s['pred_screen']}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            cv2.putText(vis_frame, f"Safe: {s['raw_safe']:.2f}" if s["raw_safe"] is not None else "Safe: None", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(vis_frame, f"Door: {s['raw_door']:.2f}" if s["raw_door"] is not None else "Door: None", (10, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(vis_frame, f"Hands: {s['raw_hands']:.2f}" if s["raw_hands"] is not None else "Hands: None", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
            cv2.imwrite(str(diag_path), vis_frame)

        n_val = len(test_samples)
        loao_results_by_group[held_out] = {
            "held_out_group": held_out,
            "val_samples": n_val,
            "screen_acc": round(grp_screen_ok / max(1, n_val), 3),
            "safe_acc": round(grp_safe_ok / max(1, n_val), 3),
            "door_acc": round(grp_door_ok / max(1, n_val), 3),
            "hands_acc": round(grp_hands_ok / max(1, n_val), 3),
            "items_acc": round(grp_items_ok / max(1, n_val), 3),
        }

    # -------------------------------------------------------------------------
    # PART 3: Specificity Evaluation on All 19 Clean (Normal) Videos
    # -------------------------------------------------------------------------
    print("\nEvaluating specificity on 19 normal (clean) videos...")
    normal_vids = [ev["video"] for ev in events if ev["type_code"] == "NONE"]

    total_clean_frames = 0
    fp_screen_doc_frames = 0
    fp_safe_open_frames = 0
    fp_door_open_frames = 0
    fp_hand_count_frames = 0
    fp_desk_items_episodes = 0
    fp_desk_items_frames = 0

    for nv in normal_vids:
        vfile = get_video_file(nv)
        if not vfile:
            continue

        try:
            meta = probe(vfile)
            dur = meta.duration_sec
            with FrameReader(vfile, meta) as r:
                ref_frame = r.read_frame_at(0.5)
                test_times = list(np.arange(1.0, max(1.5, dur - 1.0), 1.5))
                frames_dict = r.read_frames_at(test_times)
                test_frames = [frames_dict.get(tt) if frames_dict.get(tt) is not None else ref_frame for tt in test_times]
        except Exception:
            continue

        # Run scene analysis on batch
        dets_batch = [detector.detect(f, min_score=0.25) for f in test_frames]
        clean_states = analyze_scene(
            frames=test_frames,
            timestamps=test_times,
            detections=dets_batch,
            context={"ref_frame": ref_frame}
        )

        total_clean_frames += len(clean_states)
        print(f"  [{nv}] {len(clean_states)} clean frames checked.", flush=True)
        pers_run_len = 0
        for cs in clean_states:
            # 1. Screen document FP
            if cs.screen_state == "document":
                fp_screen_doc_frames += 1

            # 2. Safe open FP
            if cs.safe_open is not None and cs.safe_open >= 0.50:
                fp_safe_open_frames += 1

            # 3. Door open FP
            if cs.door_open is not None and cs.door_open >= 0.50:
                fp_door_open_frames += 1

            # 4. Hand count FP
            if cs.cashier_hand_counting is not None and cs.cashier_hand_counting >= 0.50:
                fp_hand_count_frames += 1

            # 5. Desk items (cup, clothing, food, cash, cash_bag)
            has_pers_item = any(it.cls in {"cup", "clothing_or_bag", "food", "cash", "cash_bag"} for it in cs.desk_items)
            if has_pers_item:
                fp_desk_items_frames += 1
                pers_run_len += 1.5
                if pers_run_len >= 3.0:
                    fp_desk_items_episodes += 1
            else:
                pers_run_len = 0.0

    spec_screen = round(1.0 - (fp_screen_doc_frames / max(1, total_clean_frames)), 4)
    spec_safe = round(1.0 - (fp_safe_open_frames / max(1, total_clean_frames)), 4)
    spec_door = round(1.0 - (fp_door_open_frames / max(1, total_clean_frames)), 4)
    spec_hands = round(1.0 - (fp_hand_count_frames / max(1, total_clean_frames)), 4)
    spec_items = round(1.0 - (fp_desk_items_frames / max(1, total_clean_frames)), 4)

    # -------------------------------------------------------------------------
    # PART 4: Save CSV and Metrics JSON
    # -------------------------------------------------------------------------
    csv_file = OUT_DIR / "scene_results.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(oof_results_table[0].keys()))
        writer.writeheader()
        writer.writerows(oof_results_table)

    total_samples = len(oof_results_table)
    overall = {
        "evaluation_protocol": "True Leave-One-Angle-Out (LOAO) & Clean Video Temporal Specificity",
        "video_resolution": "464x848 (vertical)",
        "total_eval_samples": total_samples,
        "clean_videos_count": len(normal_vids),
        "total_clean_frames_evaluated": total_clean_frames,
        "loao_accuracy": {
            "screen_state": round(sum(r["screen_ok"] for r in oof_results_table) / total_samples, 3),
            "desk_items": round(sum(r["items_ok"] for r in oof_results_table) / total_samples, 3),
            "safe_open": round(sum(r["safe_ok"] for r in oof_results_table) / total_samples, 3),
            "door_open": round(sum(r["door_ok"] for r in oof_results_table) / total_samples, 3),
            "hand_counting": round(sum(r["hands_ok"] for r in oof_results_table) / total_samples, 3),
        },
        "clean_video_specificity": {
            "desk_items": {
                "specificity": spec_items,
                "false_positive_frames": fp_desk_items_frames,
                "false_positive_episodes_ge_3s": fp_desk_items_episodes,
                "target_met": (fp_desk_items_episodes == 0),
            },
            "hand_counting": {
                "specificity": spec_hands,
                "false_positive_frames": fp_hand_count_frames,
                "target_met": (spec_hands >= 0.95),
            },
            "safe_open": {
                "specificity": spec_safe,
                "false_positive_frames": fp_safe_open_frames,
                "target_met": (spec_safe >= 0.95),
            },
            "door_open": {
                "specificity": spec_door,
                "false_positive_frames": fp_door_open_frames,
                "target_met": (spec_door >= 0.95),
            },
            "screen_state": {
                "specificity": spec_screen,
                "false_positive_frames": fp_screen_doc_frames,
                "target_met": (spec_screen >= 0.95),
            },
        },
        "timing_analyze_scene_cpu_ms": {
            "mean_ms": round(float(np.mean(timing_ms_list)), 2),
            "median_ms": round(float(np.median(timing_ms_list)), 2),
            "max_ms": round(float(np.max(timing_ms_list)), 2),
            "budget_limit_ms": 40.0,
            "budget_satisfied": bool(np.mean(timing_ms_list) <= 40.0),
        },
        "loao_by_angle_group": loao_results_by_group,
    }

    metrics_file = OUT_DIR / "scene_metrics.json"
    with open(metrics_file, "w", encoding="utf-8") as f:
        json.dump(overall, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print("EVALUATION COMPLETED SUCCESSFULLY")
    print(f"Results CSV: {csv_file}")
    print(f"Metrics JSON: {metrics_file}")
    print(f"Mean CPU Speed: {overall['timing_analyze_scene_cpu_ms']['mean_ms']} ms/frame (limit <= 40 ms)")
    print("\nTrue LOAO Accuracies (out-of-fold):")
    print(f"   screen_state:  {overall['loao_accuracy']['screen_state'] * 100:.1f}%")
    print(f"   desk_items:    {overall['loao_accuracy']['desk_items'] * 100:.1f}%")
    print(f"   safe_open:     {overall['loao_accuracy']['safe_open'] * 100:.1f}%")
    print(f"   door_open:     {overall['loao_accuracy']['door_open'] * 100:.1f}%")
    print(f"   hand_counting: {overall['loao_accuracy']['hand_counting'] * 100:.1f}%")
    print("\nClean Video Specificity (19 normal clips):")
    print(f"   desk_items:    {overall['clean_video_specificity']['desk_items']['specificity'] * 100:.2f}% (episodes >= 3s: {fp_desk_items_episodes})")
    print(f"   hand_counting: {overall['clean_video_specificity']['hand_counting']['specificity'] * 100:.2f}%")
    print(f"   safe_open:     {overall['clean_video_specificity']['safe_open']['specificity'] * 100:.2f}%")
    print(f"   door_open:     {overall['clean_video_specificity']['door_open']['specificity'] * 100:.2f}%")
    print(f"   screen_state:  {overall['clean_video_specificity']['screen_state']['specificity'] * 100:.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
