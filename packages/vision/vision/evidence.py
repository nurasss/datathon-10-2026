"""Evidence frame extraction: real JPEG frames taken from the uploaded video."""

from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np

MAX_EVIDENCE_WIDTH = 960
JPEG_QUALITY = 85


@dataclass
class EvidenceFrame:
    timestamp_sec: float
    jpeg_bytes: bytes
    width: int
    height: int

    def as_public_dict(self) -> dict:
        """Public API representation: exact case contract (timestamp + base64 JPEG)."""

        return {
            "timestamp_sec": round(float(self.timestamp_sec), 2),
            "image_base64": base64.b64encode(self.jpeg_bytes).decode("ascii"),
        }


def encode_frame(frame: np.ndarray, timestamp_sec: float, max_width: int = MAX_EVIDENCE_WIDTH,
                 quality: int = JPEG_QUALITY) -> EvidenceFrame:
    """Downscale (only for payload size) and JPEG-encode a decoded frame."""

    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        frame = cv2.resize(frame, (max_width, int(round(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    data = buf.tobytes()
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("encoded frame is not a JPEG")
    return EvidenceFrame(float(timestamp_sec), data, frame.shape[1], frame.shape[0])


def is_valid_jpeg(data: bytes) -> bool:
    """Cheap structural check used by the tests (SOI + EOI markers)."""

    return bool(data) and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"
