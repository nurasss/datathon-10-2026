"""NanoDet on ONNX Runtime CPU (MIT); checkpoint: OpenCV Zoo, Apache-2.0.

All COCO classes are decoded in one inference. No faces or cloud services.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, Sequence

import cv2
import numpy as np

from .types import Detection

PERSON_CLASS_ID = 0  # COCO class 0 == person
COCO_NAMES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)


class DetectorError(RuntimeError):
    pass


class PersonDetector(Protocol):
    name: str

    def detect(self, frame: np.ndarray, min_score: float) -> list[Detection]:
        ...


# --------------------------------------------------------------------------------------
# NanoDet (OpenCV Zoo)
# --------------------------------------------------------------------------------------


@dataclass
class NanoDetConfig:
    input_size: int = 416
    prob_threshold: float = 0.35
    nms_threshold: float = 0.5
    #: discard boxes smaller than this share of the frame (texture noise)
    min_box_area_ratio: float = 0.004
    #: discard boxes larger than this share of the frame (whole-scene activations)
    max_box_area_ratio: float = 0.75
    # Normalisation constants used by the OpenCV Zoo demo for this checkpoint.
    mean: tuple[float, float, float] = (103.53, 116.28, 123.675)
    std: tuple[float, float, float] = (57.375, 57.12, 58.395)
    reg_max: int = 7
    strides: tuple[int, ...] = (8, 16, 32)
    pad_value: int = 114
    keep_ratio: bool = True
    intra_op_num_threads: int = 1


class NanoDetPersonDetector:
    """Single-threaded per job by default: two jobs share two CPU cores."""

    name = "nanodet_plus_m_1.5x_416"

    def __init__(self, model_path: str | Path, config: NanoDetConfig | None = None):
        self.model_path = str(model_path)
        if not Path(self.model_path).is_file():
            raise DetectorError(f"detector weights not found: {self.model_path}")
        self.cfg = config or NanoDetConfig()
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, self.cfg.intra_op_num_threads)
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.net = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])
        self._input_name = self.net.get_inputs()[0].name
        expected = self.net.get_inputs()[0].shape[-2:]
        if expected != [self.cfg.input_size, self.cfg.input_size]:
            raise DetectorError(f"input size {expected} != configured {self.cfg.input_size}")
        self.name = f"nanodet_ort_cpu_{self.cfg.input_size}"
        self._project = np.arange(self.cfg.reg_max + 1, dtype=np.float32)
        self.sha256 = _sha256_file(self.model_path)

    # -- public API ---------------------------------------------------------------

    def detect(self, frame: np.ndarray, min_score: float | None = None) -> list[Detection]:
        return [d for d in self.detect_all(frame, min_score) if d.label == "person"]

    def detect_all(self, frame: np.ndarray, min_score: float | None = None) -> list[Detection]:
        threshold = self.cfg.prob_threshold if min_score is None else min_score
        blob, scale, pad = self._preprocess(frame)
        outs = self.net.run(None, {self._input_name: blob})
        raw = [np.asarray(o) for o in outs]
        boxes, scores = self._decode(raw, frame.shape[1], frame.shape[0], scale, pad)
        result: list[Detection] = []
        frame_area = float(max(1, frame.shape[0] * frame.shape[1]))
        for cls_id, name in enumerate(COCO_NAMES):
            indices = np.flatnonzero(scores[:, cls_id] >= threshold)
            if not len(indices):
                continue
            b = boxes[indices].copy()
            b[:, (0, 2)] = b[:, (0, 2)].clip(0, frame.shape[1])
            b[:, (1, 3)] = b[:, (1, 3)].clip(0, frame.shape[0])
            xywh = b.copy()
            xywh[:, 2:] -= xywh[:, :2]
            kept = cv2.dnn.NMSBoxes(
                xywh.tolist(), scores[indices, cls_id].tolist(), threshold, self.cfg.nms_threshold
            )
            for k in np.asarray(kept).reshape(-1):
                x1, y1, x2, y2 = map(float, b[k])
                area = (x2 - x1) * (y2 - y1) / frame_area
                # Tiny objects must not be filtered by person-size thresholds.
                if area <= 0 or (cls_id == 0 and not self.cfg.min_box_area_ratio <= area <= self.cfg.max_box_area_ratio):
                    continue
                result.append(Detection(x1, y1, x2, y2, float(scores[indices[k], cls_id]), name))
        return result

    # -- internals ----------------------------------------------------------------

    def _preprocess(self, frame: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        """Letterbox and normalize BGR/mean/std/NCHW, matching the Zoo export."""

        size = self.cfg.input_size
        h, w = frame.shape[:2]
        if self.cfg.keep_ratio:
            scale = min(size / w, size / h)
            nw, nh = int(round(w * scale)), int(round(h * scale))
            resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
            canvas = np.full((size, size, 3), self.cfg.pad_value, dtype=np.uint8)
            pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
            canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
        else:
            canvas = cv2.resize(frame, (size, size), interpolation=cv2.INTER_LINEAR)
            scale = 1.0
            pad_x = pad_y = 0
        rgb = canvas.astype(np.float32)
        mean = np.asarray(self.cfg.mean, dtype=np.float32).reshape(1, 1, 3)
        std = np.asarray(self.cfg.std, dtype=np.float32).reshape(1, 1, 3)
        # The exported OpenCV Zoo checkpoint expects BGR order (no swapRB) and
        # already contains the sigmoid in the classification head.
        norm = (rgb - mean) / std
        blob = norm.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
        return blob, scale, (pad_x, pad_y)

    def _decode(
        self,
        outputs: Sequence[np.ndarray],
        orig_w: int,
        orig_h: int,
        scale: float,
        pad: tuple[int, int],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Decode NanoDet head outputs of shape (1, N, 80) and (1, N, 4*(reg_max+1))."""

        pairs: list[tuple[np.ndarray, np.ndarray, int]] = []
        n_cls = [o for o in outputs if o.ndim == 3 and o.shape[-1] == 80]
        n_reg = [o for o in outputs if o.ndim == 3 and o.shape[-1] == 4 * (self.cfg.reg_max + 1)]
        # match by grid size
        for cls in n_cls:
            for reg in n_reg:
                if cls.shape[1] == reg.shape[1] and cls.shape[1] not in [p[0].shape[1] for p in pairs]:
                    stride = int(round(self.cfg.input_size / np.sqrt(cls.shape[1])))
                    pairs.append((cls, reg, stride))
                    break
        boxes: list[np.ndarray] = []
        scores: list[np.ndarray] = []
        pad_x, pad_y = pad
        for cls, reg, stride in pairs:
            cls_s = cls[0]  # (N, 80) — sigmoid already applied in the exported graph
            reg_d = reg[0]  # (N, 4*(reg_max+1))
            n_loc = cls_s.shape[0]
            side = int(round(np.sqrt(n_loc)))
            ys, xs = np.meshgrid(np.arange(side), np.arange(side), indexing="ij")
            # Anchor centres follow the OpenCV Zoo reference implementation.
            centers_x = xs.reshape(-1) * stride + 0.5 * (stride - 1)
            centers_y = ys.reshape(-1) * stride + 0.5 * (stride - 1)
            prob = np.clip(cls_s.astype(np.float32), 0.0, 1.0)
            d = reg_d.reshape(n_loc, 4, self.cfg.reg_max + 1).astype(np.float32)
            d = _softmax(d, axis=-1)
            dist = (d * self._project.reshape(1, 1, -1)).sum(axis=-1) * stride
            x1 = centers_x - dist[:, 0]
            y1 = centers_y - dist[:, 1]
            x2 = centers_x + dist[:, 2]
            y2 = centers_y + dist[:, 3]
            # undo letterbox padding + scaling
            x1 = (x1 - pad_x) / scale
            x2 = (x2 - pad_x) / scale
            y1 = (y1 - pad_y) / scale
            y2 = (y2 - pad_y) / scale
            boxes.append(np.stack([x1, y1, x2, y2], axis=1))
            scores.append(prob)
        if not boxes:
            raise DetectorError("unexpected detector output layout")
        all_boxes = np.clip(np.concatenate(boxes, axis=0), 0, None)
        all_scores = np.concatenate(scores, axis=0)
        return all_boxes, all_scores


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


def _softmax(x: np.ndarray, axis: int) -> np.ndarray:
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------------------
# Motion proxy (safety net, no learned weights)
# --------------------------------------------------------------------------------------


class MotionProxyDetector:
    """Very small foreground-blob detector used only to fill in missing persons.

    It never invents objects: a detection is produced only when the frame
    contains moving/foreground pixels that are not already covered by a neural
    detection.  Quality is explicitly reported in the job diagnostics.
    """

    name = "motion_proxy"

    def __init__(self, min_area_ratio: float = 0.004, max_blobs: int = 3):
        self.min_area_ratio = min_area_ratio
        self.max_blobs = max_blobs
        self._bg: cv2.BackgroundSubtractorMOG2 | None = None

    def reset(self) -> None:
        self._bg = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=32, detectShadows=False)

    def detect_blobs(self, frame: np.ndarray, covered: Iterable[Detection]) -> list[Detection]:
        if self._bg is None:
            self.reset()
        assert self._bg is not None
        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        mask = self._bg.apply(small)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(small.shape[0] * small.shape[1])
        covered_scaled = [
            Detection(d.x1 * 0.5, d.y1 * 0.5, d.x2 * 0.5, d.y2 * 0.5, 0.0) for d in covered
        ]
        blobs: list[tuple[float, Detection]] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area / frame_area < self.min_area_ratio:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            blob = Detection(x * 2, y * 2, (x + w) * 2, (y + h) * 2, 0.25, source="motion_blob")
            if any(blob.iou(c) > 0.25 for c in covered_scaled):
                continue
            blobs.append((area, blob))
        blobs.sort(key=lambda item: -item[0])
        return [b for _, b in blobs[: self.max_blobs]]


# --------------------------------------------------------------------------------------
# Stub detector (tests only)
# --------------------------------------------------------------------------------------


class StubDetector:
    """Deterministic detector driven by an external schedule.

    ``schedule`` maps an integer frame counter (0, 1, 2, ... in call order) to a
    list of boxes expressed in relative coordinates (0..1) of the frame.
    """

    name = "stub"

    def __init__(self, schedule: dict[int, list[tuple[float, float, float, float]]] | None = None):
        self.schedule = schedule or {}
        self.calls = 0

    def detect(self, frame: np.ndarray, min_score: float | None = None) -> list[Detection]:
        boxes = self.schedule.get(self.calls, [])
        self.calls += 1
        h, w = frame.shape[:2]
        return [
            Detection(x1 * w, y1 * h, x2 * w, y2 * h, 0.9, source="stub")
            for (x1, y1, x2, y2) in boxes
        ]
