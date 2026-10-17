"""Face-free two-stage IoU/Kalman tracker, implemented locally (MIT).

High-score association first, then low-score recovery; low-score boxes never
start tracks. Time-based ageing works on the nonuniform two-pass sampling grid.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .types import Detection, FrameObservation, ZoneSpec


def _measurement(d: Detection) -> np.ndarray:
    return np.array([*d.center, d.width, d.height], dtype=float)


@dataclass
class PersonTrack:
    track_id: int
    history: list[tuple[float, int, Detection]] = field(default_factory=list)
    role: str = "client_other"
    entered_at: float | None = None
    x: np.ndarray = field(default_factory=lambda: np.zeros(8))
    p: np.ndarray = field(default_factory=lambda: np.eye(8) * 100)
    clock: float = 0.0
    source_track_ids: list[int] = field(default_factory=list)

    def predict(self, t: float) -> Detection:
        dt = max(0.0, t - self.clock)
        f = np.eye(8)
        f[:4, 4:] = np.eye(4) * dt
        self.x = f @ self.x
        self.p = f @ self.p @ f.T + np.eye(8) * max(dt, 0.01) * 4
        self.clock = t
        cx, cy, w, h = self.x[:4]
        w, h = max(2, w), max(2, h)
        return Detection(cx-w/2, cy-h/2, cx+w/2, cy+h/2, 1.0)

    def add(self, obs: FrameObservation, d: Detection) -> None:
        z = _measurement(d)
        if not self.history:
            self.x[:4] = z
            self.clock = obs.t
        else:
            r = np.eye(4) * 16
            k = np.linalg.solve((self.p[:4, :4] + r).T, self.p[:, :4].T).T
            self.x += k @ (z - self.x[:4])
            self.p -= k @ self.p[:4, :]
        self.history.append((obs.t, obs.frame_index, d))


class PersonTracker:
    def __init__(self, high_score: float = 0.3, max_lost_sec: float = 2.0):
        self.high_score = high_score
        self.max_lost_sec = max_lost_sec
        self.tracks: list[PersonTrack] = []

    def update(self, obs: FrameObservation) -> None:
        live = [tr for tr in self.tracks if obs.t - tr.history[-1][0] <= self.max_lost_sec]
        predicted = {tr.track_id: tr.predict(obs.t) for tr in live}
        # A head/upper-body box nested in a full-body detection is not a second
        # person. Keep raw detector boxes in trace; deduplicate only for tracking.
        remaining = set()
        for i in sorted(range(len(obs.detections)), key=lambda i: (obs.detections[i].score < self.high_score, -obs.detections[i].area)):
            d = obs.detections[i]
            duplicate = False
            for j in remaining:
                other = obs.detections[j]
                intersection = max(0, min(d.x2, other.x2)-max(d.x1, other.x1)) * max(0, min(d.y2, other.y2)-max(d.y1, other.y1))
                if intersection / max(1, min(d.area, other.area)) > 0.7 or d.iou(other) > 0.35:
                    duplicate = True
                    break
            if not duplicate:
                remaining.add(i)
        unmatched = {tr.track_id: tr for tr in live}
        for high in (True, False):
            candidates = []
            for tid, tr in unmatched.items():
                pred = predicted[tid]
                for i in remaining:
                    d = obs.detections[i]
                    if (d.score >= self.high_score) != high:
                        continue
                    iou = pred.iou(d)
                    distance = np.linalg.norm(np.array(pred.center) - d.center)
                    scale = max(20, pred.height, d.height)
                    ratio = min(pred.area, d.area) / max(pred.area, d.area, 1)
                    valid_shape = ratio > (0.25 if high else 0.5)
                    gated = (iou >= 0.12 or distance / scale < 0.48) if high else iou >= 0.2
                    if valid_shape and gated:
                        candidates.append((iou + 0.25 * max(0, 1-distance/scale), tid, i))
            for _, tid, i in sorted(candidates, reverse=True):
                if tid not in unmatched or i not in remaining:
                    continue
                unmatched.pop(tid).add(obs, obs.detections[i])
                remaining.remove(i)
        for i in sorted(remaining):
            d = obs.detections[i]
            if d.score < self.high_score:
                continue
            tr = PersonTrack(len(self.tracks) + 1)
            tr.add(obs, d)
            self.tracks.append(tr)


def track_people(observations, post: ZoneSpec, door: ZoneSpec, width: int, height: int, cfg=None):
    from .config import TriggerConfig
    cfg = cfg or TriggerConfig()
    tracker = PersonTracker()
    for obs in observations:
        usable = [d for d in obs.detections if d.center[1] <= cfg.max_person_center_y*height
                  and d.height >= cfg.min_person_height_frac*height]
        tracker.update(FrameObservation(obs.t, obs.frame_index, usable))
    tracks = [tr for tr in tracker.tracks if len(tr.history) >= 3 and tr.history[-1][0]-tr.history[0][0] >= 0.35]
    # Integrate time, not frame count: windows otherwise bias the cashier choice.
    def occupancy(tr):
        total = 0.0
        for (t, _, d), (tn, _, _) in zip(tr.history, tr.history[1:]):
            if post.contains(*d.center) and d.score >= cfg.min_person_score:
                total += min(tn-t, 1.0) * (d.area / (width*height)) ** 0.5 * d.score
        return total
    primary = max(tracks, key=occupancy, default=None)
    if primary is not None and occupancy(primary) > 0.1:
        primary.role = "cashier"
        # Link temporal/body-shape fragments, but never merge two independently
        # co-present high-confidence people. Birth at the door is not a role.
        cashier_tracks = [primary]
        def linked(a, b):
            ah = {i: (t, d) for t, i, d in a.history}
            shared = [(ah[i][0], ah[i][1], d) for _, i, d in b.history if i in ah]
            independent = [t for t, da, db in shared if min(da.score, db.score) >= .45
                           and da.iou(db) < .1 and np.linalg.norm(np.array(da.center)-db.center) > .5*min(da.height, db.height)]
            if len(independent) > 1 and independent[-1]-independent[0] >= cfg.independent_overlap_sec:
                return False
            earlier, later = sorted((a, b), key=lambda tr: tr.history[0][0])
            start, _, first = later.history[0]
            nearby = [(t, d) for t, _, d in earlier.history if abs(t-start) <= cfg.fragment_gap_sec]
            if not nearby:
                return False
            _, prior = min(nearby, key=lambda pair: abs(pair[0]-start))
            distance = np.linalg.norm(np.array(first.center)-prior.center) / max(first.height, prior.height, 1)
            return first.iou(prior) >= cfg.fragment_iou or distance <= cfg.fragment_distance_ratio
        for _ in range(len(tracks)):
            added = False
            for tr in tracks:
                if tr.role == "cashier":
                    continue
                if any(linked(c, tr) for c in cashier_tracks):
                    tr.role = "cashier"
                    cashier_tracks.append(tr)
                    added = True
            if not added:
                break
        # One logical cashier identity, one actual measurement per frame. Keep
        # original IDs as provenance instead of exposing ID switches as entrants.
        per_frame = {}
        for tr in cashier_tracks:
            for t, idx, d in tr.history:
                if idx not in per_frame or d.score > per_frame[idx][2].score:
                    per_frame[idx] = (t, idx, d)
        merged = PersonTrack(primary.track_id, role="cashier",
                             history=sorted(per_frame.values()), source_track_ids=[tr.track_id for tr in cashier_tracks])
        others = [tr for tr in tracks if tr.role != "cashier"]
        groups = []
        for tr in sorted(others, key=lambda tr: tr.history[0][0]):
            target = None
            for group in groups:
                # Non-cashier re-ID is deliberately stricter: an exit/new box
                # must not be mistaken for a fresh entrance later in a session.
                if any(max(0, tr.history[0][0]-old.history[-1][0], old.history[0][0]-tr.history[-1][0]) <= cfg.noncashier_fragment_gap_sec
                       and linked(old, tr) and any(abs(t-tr.history[0][0]) <= cfg.fragment_gap_sec and
                       d.iou(tr.history[0][2]) >= cfg.fragment_iou for t, _, d in old.history) for old in group):
                    target = group
                    break
            if target is None:
                groups.append([tr])
            else:
                target.append(tr)
        merged_others = []
        for group in groups:
            by_frame = {}
            for tr in group:
                for t, idx, d in tr.history:
                    if idx not in by_frame or d.score > by_frame[idx][2].score:
                        by_frame[idx] = (t, idx, d)
            merged_others.append(PersonTrack(group[0].track_id, history=sorted(by_frame.values()),
                                            source_track_ids=[tr.track_id for tr in group]))
        tracks = merged_others + [merged]
    for tr in tracks:
        t0 = tr.history[0][0]
        early = [(t, d) for t, _, d in tr.history if t <= t0 + 1.2]
        if t0 > 0.8 and any(door.contains(*d.center) for _, d in early):
            tr.entered_at = t0
            if tr.role != "cashier":
                tr.role = "entrant"
    assignments: dict[int, list[tuple[PersonTrack, Detection]]] = {}
    for tr in tracks:
        for _, idx, d in tr.history:
            assignments.setdefault(idx, []).append((tr, d))
    return tracks, assignments
