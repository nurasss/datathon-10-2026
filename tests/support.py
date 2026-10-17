"""Deterministic helpers for tests: synthetic clips + a colour "detector".

The service must be verifiable without the organisers' private dataset, so the
tests build their own tiny clips and use a deterministic, model-free detector
that finds the two drawn actors (cashier / client) by colour.  Nothing here is
used in production inference.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from vision.types import Detection

CASHIER_COLOR_BGR = (150, 105, 235)  # pink shirt
CLIENT_COLOR_BGR = (60, 40, 30)      # dark clothes


def make_clip(
    path: str | Path,
    duration: float = 12.0,
    fps: float = 15.0,
    size: tuple[int, int] = (480, 640),
    cashier_present: list[tuple[float, float]] | None = None,
    client_present: list[tuple[float, float]] | None = None,
    seed: int = 7,
) -> Path:
    """Write a synthetic clip with coloured actors at fixed positions.

    ``cashier_present`` / ``client_present`` are lists of ``(start, end)``
    intervals; outside them the actor is not drawn.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():  # pragma: no cover - codec availability
        raise RuntimeError("cannot open video writer for synthetic clip")
    rng = np.random.default_rng(seed)
    background = np.full((height, width, 3), 210, np.uint8)
    cv2.rectangle(background, (60, 380), (width - 60, 470), (120, 120, 120), -1)  # desk
    noise = rng.integers(0, 6, size=(height, width, 3), dtype=np.int16)

    total = int(duration * fps)
    # The cashier sits closer to the camera (a larger box); the client stands
    # farther away and is smaller — the same geometry as the organisers' clips.
    for index in range(total):
        t = index / fps
        frame = np.clip(background.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        if _active(client_present, t):
            cv2.rectangle(frame, (54, 196), (128, 372), CLIENT_COLOR_BGR, -1)
            cv2.circle(frame, (91, 170), 26, CLIENT_COLOR_BGR, -1)
        if _active(cashier_present, t):
            cv2.rectangle(frame, (196, 236), (330, 452), CASHIER_COLOR_BGR, -1)
            cv2.circle(frame, (263, 205), 38, CASHIER_COLOR_BGR, -1)
        writer.write(frame)
    writer.release()
    return path


def _active(intervals: list[tuple[float, float]] | None, t: float) -> bool:
    if not intervals:
        return False
    return any(start <= t <= end for start, end in intervals)


class ColorPersonDetector:
    """Deterministic stand-in detector: finds the drawn actors by colour."""

    name = "color_stub"

    def __init__(self, min_score: float = 0.0):
        self.min_score = min_score

    def detect(self, frame: np.ndarray, min_score: float | None = None) -> list[Detection]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        detections: list[Detection] = []
        detections.extend(self._blobs(hsv, CASHIER_COLOR_BGR, frame.shape, 0.9))
        detections.extend(self._blobs(hsv, CLIENT_COLOR_BGR, frame.shape, 0.85))
        return detections

    @staticmethod
    def _blobs(hsv: np.ndarray, bgr: tuple[int, int, int], shape: tuple[int, ...], score: float) -> list[Detection]:
        target = np.uint8([[list(bgr)]])
        target_hsv = cv2.cvtColor(target, cv2.COLOR_BGR2HSV)[0][0]
        hue, sat, val = int(target_hsv[0]), int(target_hsv[1]), int(target_hsv[2])
        lower = np.array([max(0, hue - 8), max(60, sat - 60), max(40, val - 70)], np.uint8)
        upper = np.array([min(179, hue + 8), 255, min(255, val + 70)], np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(shape[0] * shape[1])
        out: list[Detection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area / frame_area < 0.01:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            out.append(
                Detection(float(x), float(y), float(x + w), float(y + h), score, source="color_stub")
            )
        return out
