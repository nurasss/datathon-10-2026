"""Temporal reasoning: turn per-frame observations into rule-grounded events.

Two visible patterns are implemented, both tied to *approved* rules of the
local registry (see packages/rules/registry.json):

* **EV01** — the workplace is unattended while a client is at the counter
  (rule KZ-CASH-231-P22: a cashier must not leave the workplace before the
  service of the client is finished).
* **EV02** — an object stays on the open workplace surface after the cashier
  left (rule KZ-CASH-231-P20: previously accepted cash must be kept in lockable
  devices).

Every event carries an objective observation, a guarded hypothesis and the
evidence timestamps.  Nothing is emitted for a rule whose registry status is not
``approved``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Ev01Config, Ev02Config
from .deskobjects import Blob, DeskWatcher
from .tracker import IouTracker
from .types import Detection, EventCandidate, FrameObservation, ZoneSpec
from .zones import ZoneSpec as _Zone  # noqa: F401  (kept for type checkers)
from .zones import detection_in_zone, inset


@dataclass
class FrameState:
    t: float
    frame_index: int
    post_occupied: bool
    client_present: bool
    persons_in_post: int
    persons_near_counter: int
    persons_total: int
    persons_other: int = 0
    primary_track: int | None = None


@dataclass
class Interval:
    start: float
    end: float
    frames: list[int] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def overlaps(self, start: float, end: float) -> bool:
        return self.end >= start and self.start <= end


def build_states(
    observations: list[FrameObservation],
    post: ZoneSpec,
    counter: ZoneSpec,
    dt: float,
    tracker: "IouTracker | None" = None,
) -> list[FrameState]:
    """Per-observation presence state relative to the inferred zones.

    Presence is resolved per *tracked person*, not per raw detection: the clip
    may contain the cashier and a client standing shoulder to shoulder, and a
    single flickering box must not flip the workplace between occupied and not.
    """

    tracker = tracker or IouTracker()
    for obs in observations:
        tracker.update(obs)
    tracks = tracker.tracks

    assignments: dict[int, list[tuple[int, Detection]]] = {}
    zone_time: dict[int, float] = {}
    for track in tracks:
        for (t, det), frame_index in zip(track.detections, track.frames):
            assignments.setdefault(frame_index, []).append((track.track_id, det))
            cx, cy = det.center
            if post.contains(cx, cy):
                zone_time[track.track_id] = zone_time.get(track.track_id, 0.0) + dt

    primary_id: int | None = None
    if zone_time:
        best_id = max(zone_time, key=lambda k: zone_time[k])
        # a credible primary worker must actually spend time at the workplace
        if zone_time[best_id] >= max(2.0 * dt, 0.6):
            primary_id = best_id

    states: list[FrameState] = []
    for obs in observations:
        in_post = 0
        near_counter = 0
        others_total = 0
        for track_id, det in assignments.get(obs.frame_index, []):
            cx, cy = det.center
            inside_post = post.contains(cx, cy)
            if inside_post:
                in_post += 1
            if track_id != primary_id:
                others_total += 1
                if counter.contains(cx, cy):
                    near_counter += 1
        states.append(
            FrameState(
                t=obs.t,
                frame_index=obs.frame_index,
                post_occupied=in_post > 0,
                client_present=near_counter > 0,
                persons_in_post=in_post,
                persons_near_counter=near_counter,
                persons_total=len(obs.detections),
                persons_other=others_total,
                primary_track=primary_id,
            )
        )
    return states


def intervals_from_flags(states: list[FrameState], flag: str, dt: float, gap_tolerance: float = 0.0) -> list[Interval]:
    """Maximal intervals where ``flag`` is true, tolerating small gaps."""

    intervals: list[Interval] = []
    current: Interval | None = None
    pending_gap: Interval | None = None
    for state in states:
        value = getattr(state, flag)
        if value:
            if current is None:
                current = Interval(state.t, state.t, [state.frame_index])
            else:
                if pending_gap is not None:
                    current.end = pending_gap.end
                    current.frames.extend(pending_gap.frames)
                    pending_gap = None
                current.end = state.t
                current.frames.append(state.frame_index)
        else:
            if current is not None:
                if pending_gap is None:
                    pending_gap = Interval(state.t, state.t, [state.frame_index])
                else:
                    pending_gap.end = state.t
                    pending_gap.frames.append(state.frame_index)
                if pending_gap.duration > gap_tolerance:
                    intervals.append(current)
                    current = None
                    pending_gap = None
    if current is not None:
        current.end += dt
        intervals.append(current)
    return intervals


def _clamp_confidence(value: float) -> float:
    return float(max(0.0, min(0.99, value)))


def detect_ev01(
    states: list[FrameState],
    cfg: Ev01Config,
    dt: float,
    quality: dict,
) -> list[dict]:
    """Rule KZ-CASH-231-P22: workplace unattended while a client is at the counter."""

    if not states or not cfg.enabled:
        return []
    absences = _complement_intervals(
        states, "post_occupied", dt, gap_tolerance=cfg.absence_gap_tolerance_sec
    )
    clients = intervals_from_flags(states, "client_present", dt, gap_tolerance=cfg.client_gap_tolerance_sec)
    clients = [c for c in clients if c.duration >= cfg.min_client_sec]

    events: list[dict] = []
    for absence in absences:
        if absence.duration < cfg.min_absence_sec:
            continue
        inside = [c for c in clients if c.overlaps(absence.start - 1e-6, absence.end + 1e-6)]
        before = [
            c
            for c in clients
            if c.start <= absence.start + 1e-6 and c.end >= absence.start - cfg.pre_window_sec
        ]
        if inside:
            client_start = min(c.start for c in inside)
            client_end = max(c.end for c in inside)
            if client_start <= absence.start + 1e-6:
                # service was already in progress when the workplace became unattended
                t_event = absence.start
                variant = "service_in_progress"
                client_ref = client_start
            else:
                # a client approached a workplace that was already unattended
                t_event = client_start
                variant = "client_arrived_to_unattended_post"
                client_ref = client_start
        elif before:
            # the client was at the counter right before the cashier walked away
            t_event = absence.start
            variant = "service_in_progress"
            client_ref = max(c.start for c in before)
            client_end = max(c.end for c in before)
        else:
            continue
        overlap = max(0.0, min(absence.end, client_end) - max(absence.start, client_ref))
        confidence = _clamp_confidence(
            0.45
            + 0.25 * min(1.0, absence.duration / 8.0)
            + 0.20 * min(1.0, overlap / 6.0)
            + 0.10 * float(quality.get("zone_confidence", 0.0))
        )
        if confidence < cfg.min_confidence:
            continue
        events.append(
            {
                "variant": variant,
                "start": absence.start,
                "peak": t_event,
                "end": absence.end,
                "client_ref": client_ref,
                "absence_duration": absence.duration,
                "client_overlap": overlap,
                "confidence": confidence,
            }
        )
    return events


def _complement_intervals(
    states: list[FrameState], flag: str, dt: float, gap_tolerance: float = 0.0
) -> list[Interval]:
    """Intervals where ``flag`` is *false* (absence runs).

    Occupied gaps shorter than ``gap_tolerance`` are treated as detector flicker
    and do not split a genuine absence into pieces.
    """

    intervals: list[Interval] = []
    current: Interval | None = None
    pending: Interval | None = None
    for state in states:
        value = getattr(state, flag)
        if not value:
            if current is None:
                current = Interval(state.t, state.t, [state.frame_index])
            else:
                if pending is not None:
                    current.end = pending.end
                    current.frames.extend(pending.frames)
                    pending = None
                current.end = state.t
                current.frames.append(state.frame_index)
        else:
            if current is not None:
                if pending is None:
                    pending = Interval(state.t, state.t, [state.frame_index])
                else:
                    pending.end = state.t
                    pending.frames.append(state.frame_index)
                if pending.duration > gap_tolerance:
                    current.end += dt
                    intervals.append(current)
                    current = None
                    pending = None
    if current is not None:
        current.end += dt
        intervals.append(current)
    return intervals


def detect_ev02(
    blobs: list[Blob],
    observations_by_index: dict[int, FrameObservation],
    states_by_index: dict[int, FrameState],
    cfg: Ev02Config,
    dt: float,
) -> list[dict]:
    """Rule KZ-CASH-231-P20: objects left on the open workplace surface."""

    events: list[dict] = []
    if not cfg.enabled:
        return events
    for blob in blobs:
        first_t = observations_by_index.get(blob.first_index)
        last_t = observations_by_index.get(blob.last_index)
        if first_t is None or last_t is None:
            continue
        persist = last_t.t - first_t.t
        if persist < cfg.min_persist_sec:
            continue
        # find the first moment after the blob appeared when the workplace is unattended
        # and stays unattended long enough while the blob is still present
        unattended_start: float | None = None
        for idx in range(blob.first_index, blob.last_index + 1):
            state = states_by_index.get(idx)
            if state is None:
                continue
            if not state.post_occupied:
                unattended_start = state.t
                break
        if unattended_start is None:
            continue
        # the blob must still be there after the workplace became unattended
        remaining_states = [
            states_by_index.get(i) for i in range(blob.first_index, blob.last_index + 1)
        ]
        if not any(s is not None and s.t >= unattended_start for s in remaining_states):
            continue
        last_visible = max(
            (s.t for s in remaining_states if s is not None and s.t >= unattended_start), default=None
        )
        if last_visible is None or (last_visible - unattended_start) < cfg.min_unattended_sec:
            continue
        confidence = _clamp_confidence(
            0.40
            + 0.25 * min(1.0, persist / 8.0)
            + 0.20 * min(1.0, (last_visible - unattended_start) / 6.0)
        )
        if confidence < cfg.min_confidence:
            continue
        events.append(
            {
                "start": first_t.t,
                "peak": unattended_start,
                "end": last_visible,
                "unattended_sec": last_visible - unattended_start,
                "persist_sec": persist,
                "confidence": confidence,
                "bbox": [blob.x1, blob.y1, blob.x2, blob.y2],
            }
        )
    return events


def merge_events(events: list[EventCandidate], gap_sec: float) -> list[EventCandidate]:
    """Merge consecutive events of the same type that describe one episode."""

    if not events:
        return []
    ordered = sorted(events, key=lambda e: (e.event_code, e.start_sec))
    merged: list[EventCandidate] = []
    for event in ordered:
        if (
            merged
            and merged[-1].event_code == event.event_code
            and event.start_sec - merged[-1].end_sec <= gap_sec
        ):
            prev = merged[-1]
            prev.end_sec = max(prev.end_sec, event.end_sec)
            prev.confidence = max(prev.confidence, event.confidence)
            for t in event.evidence_times:
                if t not in prev.evidence_times:
                    prev.evidence_times.append(t)
            prev.meta.setdefault("merged", []).append(event.meta)
            continue
        merged.append(event)
    return merged
