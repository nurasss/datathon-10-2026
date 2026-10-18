"""Video probing and frame access.

Decoding happens in isolated child processes (``ffprobe``) or through
OpenCV's FFmpeg backend.  No audio stream is ever decoded or analysed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from .types import VideoMeta

FFPROBE_BIN = "ffprobe"
# Hard limits for the isolated probe process: a malformed container must never
# hang the worker.
_PROBE_TIMEOUT_SEC = 30.0


class VideoProbeError(RuntimeError):
    """Raised when a file cannot be probed as a decodable video."""


def probe(path: str | Path, timeout: float = _PROBE_TIMEOUT_SEC) -> VideoMeta:
    """Return :class:`VideoMeta` for ``path`` using an isolated ffprobe call."""

    path = str(path)
    if shutil.which(FFPROBE_BIN) is None:
        return _probe_with_opencv(path)
    cmd = [
        FFPROBE_BIN,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - depends on input
        raise VideoProbeError("ffprobe timeout") from exc
    if proc.returncode != 0:
        raise VideoProbeError(
            "ffprobe failed: " + (proc.stderr.decode("utf-8", "replace")[:400] or "unknown error")
        )
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise VideoProbeError("ffprobe returned invalid JSON") from exc
    return _meta_from_probe(path, data)


def _fraction(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        if "/" in value:
            return float(Fraction(value))
        return float(value)
    except (ZeroDivisionError, ValueError):
        return 0.0


def _meta_from_probe(path: str, data: dict) -> VideoMeta:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoProbeError("no video stream found")
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    r_fps = _fraction(video.get("r_frame_rate"))
    nb_frames = int(video.get("nb_frames") or 0)
    if nb_frames <= 0 and duration > 0 and fps > 0:
        nb_frames = int(round(duration * fps))
    meta = VideoMeta(
        path=path,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        duration_sec=duration,
        nb_frames=nb_frames,
        codec=str(video.get("codec_name") or ""),
        format_name=str(fmt.get("format_name") or ""),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        variable_frame_rate=bool(r_fps and fps and abs(r_fps - fps) > 0.05 * max(r_fps, fps)),
        raw={"format": fmt.get("format_long_name"), "streams": len(streams)},
    )
    if not meta.is_decodable:
        raise VideoProbeError("video stream has no usable dimensions or duration")
    return meta


def _probe_with_opencv(path: str) -> VideoMeta:  # pragma: no cover - fallback path
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise VideoProbeError("cannot open video with OpenCV backend")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    finally:
        cap.release()
    duration = frames / fps if fps > 0 and frames > 0 else 0.0
    meta = VideoMeta(
        path=path,
        width=width,
        height=height,
        fps=fps,
        duration_sec=duration,
        nb_frames=frames,
        codec="unknown",
        format_name="opencv",
        has_audio=False,
    )
    if not meta.is_decodable:
        raise VideoProbeError("cannot probe video (ffprobe missing, OpenCV failed)")
    return meta


class FrameReader:
    """Sequential frame reader with deterministic temporal sampling.

    ``decode_max_width`` asks the backend to scale frames down while decoding:
    on a 1080p clip this is the difference between ~40 ms and ~8 ms per frame,
    and the detector never sees more than 416 px anyway.  Only the analysis pass
    is scaled; ``read_frame_at`` (evidence) always returns source resolution.
    """

    def __init__(self, path: str | Path, meta: VideoMeta, decode_max_width: int | None = None,
                 hflip: bool = False):
        self.path = str(path)
        self.meta = meta
        self.hflip = hflip
        self._cap = cv2.VideoCapture(self.path, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
        if not self._cap.isOpened():
            raise VideoProbeError(f"cannot open video for decoding: {self.path}")
        self._decoded = 0
        self._last_t = -1.0
        self.decode_scale = 1.0
        if decode_max_width and meta.width > decode_max_width:
            scale = decode_max_width / float(meta.width)
            target_w = int(round(meta.width * scale))
            target_h = max(2, int(round(meta.height * scale)))
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(target_w))
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(target_h))
            self.decode_scale = scale

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> "FrameReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def fps(self) -> float:
        return self.meta.fps or 30.0

    def iter_sampled(self, analysis_fps: float) -> Iterator[tuple[float, np.ndarray]]:
        """Yield ``(t_sec, bgr_frame)`` for a uniform sampling grid of ``analysis_fps``."""

        step = 1.0 / max(analysis_fps, 0.1)
        target = 0.0
        duration = self.meta.duration_sec
        fps = self.fps
        while target <= duration + 1e-6:
            frame = self._read_at(target, fps)
            if frame is None:
                break
            yield min(target, duration), frame
            target += step

    def iter_window(self, start: float, end: float, fps: float) -> Iterator[tuple[float, np.ndarray]]:
        """Seek once per candidate window, then decode sequentially (one thread)."""
        start_idx = int(round(start * self.fps))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, start_idx)
        self._decoded = start_idx
        target = start
        while target <= min(end, self.meta.duration_sec - 1/self.fps) + 1e-6:
            frame = self._read_at(target, self.fps)
            if frame is None:
                break
            yield target, frame
            target += 1 / fps

    def _read_at(self, t_sec: float, fps: float) -> Optional[np.ndarray]:
        """Read the frame whose presentation time is the closest available >= t_sec."""

        frame_idx = int(round(t_sec * fps))
        if frame_idx <= self._decoded:
            # Sequential decoding is much faster than seeking on long clips.
            while self._decoded < frame_idx:
                if not self._cap.grab():
                    return None
                self._decoded += 1
        else:
            while self._decoded < frame_idx:
                if not self._cap.grab():
                    return None
                self._decoded += 1
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        self._decoded += 1
        self._last_t = t_sec
        return cv2.flip(frame, 1) if self.hflip else frame

    def read_frame_at(self, t_sec: float) -> Optional[np.ndarray]:
        """Read a single frame near ``t_sec`` from an independent capture handle.

        Used for evidence extraction after the analysis pass; it never reuses the
        sequential iterator state so evidence timestamps stay correct.
        """

        return self.read_frames_at([t_sec]).get(t_sec)

    def read_frames_at(self, timestamps: list[float]) -> dict[float, Optional[np.ndarray]]:
        """One deterministic source decode for an entire evidence batch.

        Independent of the analysis iterator; single-frame and batch extraction
        use the same rounded source-frame indices, including duplicate times.
        """
        cap = cv2.VideoCapture(self.path, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
        result: dict[float, Optional[np.ndarray]] = {}
        if not cap.isOpened():
            return dict.fromkeys(timestamps)
        try:
            decoded = 0
            previous_index = -1
            previous_frame = None
            exhausted = False
            for t_sec in sorted(set(timestamps)):
                target = int(round(max(0.0, t_sec)*self.fps))
                if target == previous_index:
                    result[t_sec] = previous_frame
                    continue
                while decoded < target and not exhausted:
                    if not cap.grab():
                        exhausted = True
                    decoded += 1
                ok, frame = cap.read() if not exhausted else (False, None)
                decoded += 1
                result[t_sec] = (cv2.flip(frame, 1) if self.hflip else frame) if ok else None
                previous_index, previous_frame = target, result[t_sec]
            return result
        finally:
            cap.release()
