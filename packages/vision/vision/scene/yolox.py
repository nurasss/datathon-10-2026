"""Colab YOLOX ONNX hook for ForteVision scene state analysis.

Provides a pluggable integration point for models trained in Colab.
If an ONNX model is placed in /Users/nuras/Desktop/datathon/models_colab/,
it automatically handles inference and mapping for:
- cash, cash_bag, phone, cup_or_food, clothing_or_bag
- safe_open, safe_closed
- laptop_document, laptop_lockscreen

If the model file is absent, gracefully falls back to CV heuristics.
Can be toggled via config flag `enable_colab_yolox`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_COLAB_DIR = Path("/Users/nuras/Desktop/datathon/models_colab")

YOLOX_CLASSES = [
    "cash",
    "cash_bag",
    "phone",
    "cup_or_food",
    "clothing_or_bag",
    "safe_open",
    "safe_closed",
    "laptop_document",
    "laptop_lockscreen",
]


class ColabYoloxDetector:
    """YOLOX ONNX detector hook with automatic discovery and CPU inference."""

    def __init__(
        self,
        model_dir: str | Path = DEFAULT_COLAB_DIR,
        model_filename: Optional[str] = None,
        input_size: tuple[int, int] = (416, 416),
        conf_thresh: float = 0.35,
        nms_thresh: float = 0.45,
    ):
        self.model_dir = Path(model_dir)
        self.model_filename = model_filename
        self.input_size = input_size
        self.conf_thresh = conf_thresh
        self.nms_thresh = nms_thresh
        self.session = None
        self.model_path: Optional[Path] = None
        self._init_session()

    def _find_model_file(self) -> Optional[Path]:
        if not self.model_dir.exists():
            return None
        if self.model_filename:
            target = self.model_dir / self.model_filename
            return target if target.is_file() else None

        # Search for any .onnx file in the directory
        candidates = list(self.model_dir.glob("*.onnx"))
        if candidates:
            # Prefer files named yolox*.onnx
            yolox_matches = [c for c in candidates if "yolox" in c.name.lower()]
            return yolox_matches[0] if yolox_matches else candidates[0]
        return None

    def _init_session(self) -> None:
        self.model_path = self._find_model_file()
        if self.model_path is None:
            return

        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(
                str(self.model_path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
            logger.info("Loaded Colab YOLOX model from %s", self.model_path)
        except Exception as exc:
            logger.warning("Failed to initialize Colab YOLOX session: %s", exc)
            self.session = None

    def is_available(self) -> bool:
        """Returns True if the ONNX model is found and successfully loaded."""
        if self.session is None:
            self._init_session()
        return self.session is not None

    def detect(self, frame: np.ndarray) -> list[dict]:
        """Runs inference on a BGR frame.

        Returns:
            list of detections: [{"cls": str, "score": float, "box": (x1, y1, x2, y2)}]
            where box is normalized coordinates in 0..1 range.
        """
        if not self.is_available():
            return []

        h, w = frame.shape[:2]
        inp_h, inp_w = self.input_size

        # Letterbox resize preserving aspect ratio
        scale = min(inp_w / w, inp_h / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((inp_h, inp_w, 3), 114, dtype=np.uint8)
        pad_x = (inp_w - nw) // 2
        pad_y = (inp_h - nh) // 2
        canvas[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized

        # YOLOX standard preprocessing (BGR, float32, no mean/std normalization)
        blob = canvas.astype(np.float32).transpose(2, 0, 1)[np.newaxis, ...]

        input_name = self.session.get_inputs()[0].name
        outs = self.session.run(None, {input_name: blob})
        output = outs[0]

        if output.ndim == 3:
            output = output[0]  # shape: (N, 5 + num_classes) or (N, 4 + num_classes)

        detections = []
        if output.shape[-1] >= 5:
            boxes = output[:, :4]
            scores = output[:, 4:]

            if scores.shape[1] == len(YOLOX_CLASSES) + 1:
                # obj_conf * cls_conf
                obj_conf = scores[:, 0:1]
                cls_probs = scores[:, 1:] * obj_conf
            else:
                cls_probs = scores

            max_cls_idx = np.argmax(cls_probs, axis=1)
            max_scores = np.max(cls_probs, axis=1)

            mask = max_scores >= self.conf_thresh
            if np.any(mask):
                valid_boxes = boxes[mask]
                valid_scores = max_scores[mask]
                valid_cls = max_cls_idx[mask]

                # Convert cx, cy, w, h to x1, y1, x2, y2
                x1 = valid_boxes[:, 0] - valid_boxes[:, 2] / 2.0
                y1 = valid_boxes[:, 1] - valid_boxes[:, 3] / 2.0
                x2 = valid_boxes[:, 0] + valid_boxes[:, 2] / 2.0
                y2 = valid_boxes[:, 1] + valid_boxes[:, 3] / 2.0

                # Unpad and scale back to original frame
                x1 = np.clip((x1 - pad_x) / (scale * w), 0.0, 1.0)
                x2 = np.clip((x2 - pad_x) / (scale * w), 0.0, 1.0)
                y1 = np.clip((y1 - pad_y) / (scale * h), 0.0, 1.0)
                y2 = np.clip((y2 - pad_y) / (scale * h), 0.0, 1.0)

                # OpenCV NMS
                boxes_list = [
                    [float(a), float(b), float(c - a), float(d - b)]
                    for a, b, c, d in zip(x1 * w, y1 * h, x2 * w, y2 * h)
                ]
                indices = cv2.dnn.NMSBoxes(
                    boxes_list,
                    valid_scores.tolist(),
                    self.conf_thresh,
                    self.nms_thresh,
                )

                if len(indices) > 0:
                    indices = np.array(indices).flatten()
                    for idx in indices:
                        c_id = valid_cls[idx]
                        c_name = YOLOX_CLASSES[c_id] if c_id < len(YOLOX_CLASSES) else f"cls_{c_id}"
                        detections.append(
                            {
                                "cls": c_name,
                                "score": float(valid_scores[idx]),
                                "box": (
                                    float(x1[idx]),
                                    float(y1[idx]),
                                    float(x2[idx]),
                                    float(y2[idx]),
                                ),
                            }
                        )

        return detections
