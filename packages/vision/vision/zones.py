"""Zone inference: locate the cashier workplace ("post") and the counter area.

The service must work on clips shot from a different angle (the organisers
explicitly test that), therefore the zones are derived from the data whenever
possible and only fall back to configured defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .types import Detection, FrameObservation, ZoneSpec

GRID_W = 48
GRID_H = 27


def _blur_grid(grid: np.ndarray, ksize: int) -> np.ndarray:
    """Box-blur the occupancy grid so that a stationary person yields a zone."""
    return cv2.blur(grid, (ksize, ksize))


def _enforce_min_span(a: float, b: float, minimum: float, limit: float) -> tuple[float, float]:
    """Grow the span [a, b] symmetrically until it is at least ``minimum`` wide."""
    span = b - a
    if span >= minimum:
        return a, b
    centre = 0.5 * (a + b)
    half = 0.5 * minimum
    a = centre - half
    b = centre + half
    if a < 0:
        b -= a
        a = 0.0
    if b > limit:
        a -= b - limit
        b = float(limit)
    return max(0.0, a), min(float(limit), b)


def _median(values: list[float], default: float) -> float:
    clean = [float(v) for v in values if v and v > 0]
    if not clean:
        return float(default)
    return float(np.median(np.asarray(clean, dtype=np.float32)))


@dataclass
class ZoneConfig:
    """Tunables for zone inference (see configs/inference.default.json)."""

    #: fallback workplace rectangle as fractions of the frame (x1, y1, x2, y2)
    fallback_post_rect: tuple[float, float, float, float] = (0.30, 0.30, 0.72, 0.92)
    #: weight threshold relative to the peak cell when growing the post zone
    cluster_rel_threshold: float = 0.35
    #: the workplace rectangle is clamped to this factor of a typical person box
    #: around the busiest cell, so that a client standing next to the cashier does
    #: not become part of the workplace
    person_window_factor: float = 1.35
    #: person box sizes are taken as the median of the detections in the cluster
    person_size_scale: float = 1.15
    #: absolute floor for the workplace rectangle (share of the frame)
    min_zone_width_frac: float = 0.12
    min_zone_height_frac: float = 0.14
    #: blur applied to the occupancy grid before clustering
    grid_blur_ksize: int = 3
    #: minimum presence share to trust an auto-derived zone
    min_presence_share: float = 0.22
    #: if the auto-derived workplace is occupied less than this share of frames the
    #: anchor was probably a client, so the configured fallback rectangle is used
    min_zone_presence_share: float = 0.15
    #: margin applied to the derived workplace rectangle (fraction of its size)
    post_margin: float = 0.12
    #: how far around the workplace a person still counts as "at the counter"
    counter_margin: float = 0.75
    #: counter margin is capped to this share of the smaller frame dimension
    counter_margin_cap: float = 0.30


def infer_zones(
    observations: list[FrameObservation],
    width: int,
    height: int,
    cfg: ZoneConfig,
    override: tuple[float, float, float, float] | None = None,
) -> list[ZoneSpec]:
    """Return ``[post_zone, counter_zone]`` for the analysed clip."""

    if override is not None:
        x1, y1, x2, y2 = override
        post = ZoneSpec("post", x1 * width, y1 * height, x2 * width, y2 * height, 1.0, "config")
        return [post, _counter_zone(post, width, height, cfg)]

    grid = np.zeros((GRID_H, GRID_W), dtype=np.float32)
    frames_with_person = 0
    dets_total = 0
    det_sizes: list[tuple[float, float]] = []
    for obs in observations:
        if obs.detections:
            frames_with_person += 1
        for det in obs.detections:
            if det.source == "motion_blob":
                continue
            cx, cy = det.center
            gx = int(np.clip(cx / max(width, 1) * GRID_W, 0, GRID_W - 1))
            gy = int(np.clip(cy / max(height, 1) * GRID_H, 0, GRID_H - 1))
            grid[gy, gx] += max(1.0, det.area / (width * height) * 50.0)
            det_sizes.append((det.width, det.height))
            dets_total += 1

    presence_share = frames_with_person / max(1, len(observations))
    if dets_total < 3 or float(grid.sum()) <= 0:
        post = _fallback_zone(width, height, cfg, presence_share)
        return [post, _counter_zone(post, width, height, cfg)]

    if cfg.grid_blur_ksize >= 3:
        k = cfg.grid_blur_ksize | 1  # odd
        grid = _blur_grid(grid, k)
    peak_index = int(np.argmax(grid))
    peak = float(grid.reshape(-1)[peak_index])
    peak_y, peak_x = divmod(peak_index, GRID_W)
    mask = grid >= max(peak * cfg.cluster_rel_threshold, 1e-6)
    ys, xs = np.nonzero(mask)
    x1 = xs.min() / GRID_W * width
    x2 = (xs.max() + 1) / GRID_W * width
    y1 = ys.min() / GRID_H * height
    y2 = (ys.max() + 1) / GRID_H * height
    # Enforce a minimum rectangle so that a person who stands still for the whole
    # clip does not produce a single-cell zone.
    x1, x2 = _enforce_min_span(x1, x2, cfg.min_zone_width_frac * width, width)
    y1, y2 = _enforce_min_span(y1, y2, cfg.min_zone_height_frac * height, height)
    # Keep the workplace anchored to the busiest cell and no wider than a person:
    # otherwise a client standing shoulder to shoulder with the cashier is counted
    # as part of the workplace and a real absence becomes invisible.
    centre_x = (peak_x + 0.5) / GRID_W * width
    centre_y = (peak_y + 0.5) / GRID_H * height
    person_w = _median([w for w, _ in det_sizes], 0.22 * width)
    person_h = _median([h for _, h in det_sizes], 0.32 * height)
    half_w = 0.5 * person_w * cfg.person_size_scale * cfg.person_window_factor
    half_h = 0.5 * person_h * cfg.person_size_scale * cfg.person_window_factor
    x1 = max(x1, centre_x - half_w)
    x2 = min(x2, centre_x + half_w)
    y1 = max(y1, centre_y - half_h)
    y2 = min(y2, centre_y + half_h)
    x1, x2 = _enforce_min_span(x1, x2, cfg.min_zone_width_frac * width, width)
    y1, y2 = _enforce_min_span(y1, y2, cfg.min_zone_height_frac * height, height)
    mx = (x2 - x1) * cfg.post_margin
    my = (y2 - y1) * cfg.post_margin
    post = ZoneSpec(
        "post",
        max(0.0, x1 - mx),
        max(0.0, y1 - my),
        min(float(width), x2 + mx),
        min(float(height), y2 + my),
        confidence=round(float(min(1.0, presence_share)), 3),
        source="auto",
    )
    if presence_share < cfg.min_presence_share:
        post.confidence = round(float(presence_share), 3)
    return [post, _counter_zone(post, width, height, cfg)]


def _fallback_zone(width: int, height: int, cfg: ZoneConfig, presence_share: float) -> ZoneSpec:
    x1, y1, x2, y2 = cfg.fallback_post_rect
    return ZoneSpec(
        "post",
        x1 * width,
        y1 * height,
        x2 * width,
        y2 * height,
        confidence=round(float(min(0.5, presence_share)), 3),
        source="fallback",
    )


def _counter_zone(post: ZoneSpec, width: int, height: int, cfg: ZoneConfig) -> ZoneSpec:
    size = max(post.x2 - post.x1, post.y2 - post.y1)
    margin = min(cfg.counter_margin * size, cfg.counter_margin_cap * min(width, height))
    return ZoneSpec(
        "counter",
        max(0.0, post.x1 - margin),
        max(0.0, post.y1 - margin),
        min(float(width), post.x2 + margin),
        min(float(height), post.y2 + margin),
        confidence=post.confidence,
        source=post.source,
    )


def inset(zone: ZoneSpec, ratio: float) -> ZoneSpec:
    """Shrink a zone by ``ratio`` of its size (used to exclude the cashier's own area)."""

    dx = (zone.x2 - zone.x1) * ratio
    dy = (zone.y2 - zone.y1) * ratio
    return ZoneSpec(
        zone.name,
        zone.x1 + dx,
        zone.y1 + dy,
        zone.x2 - dx,
        zone.y2 - dy,
        zone.confidence,
        zone.source,
    )


def detection_in_zone(det: Detection, zone: ZoneSpec, use_center: bool = True) -> bool:
    if use_center:
        cx, cy = det.center
        return zone.contains(cx, cy)
    # fall back to overlap test for large boxes
    ix1, iy1 = max(det.x1, zone.x1), max(det.y1, zone.y1)
    ix2, iy2 = min(det.x2, zone.x2), min(det.y2, zone.y2)
    if ix2 <= ix1 or iy2 <= iy1:
        return False
    inter = (ix2 - ix1) * (iy2 - iy1)
    return inter / max(det.area, 1e-6) >= 0.25
