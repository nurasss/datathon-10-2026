"""Minimal, dependency-free multi-object tracker (IoU + centroid fallback)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Detection, FrameObservation


@dataclass
class Track:
    track_id: int
    detections: list[tuple[float, Detection]] = field(default_factory=list)
    frames: list[int] = field(default_factory=list)
    last_seen_index: int = -1
    first_seen_t: float = 0.0

    @property
    def last_detection(self) -> Detection:
        return self.detections[-1][1]

    def presence_in(self, zone) -> float:  # noqa: ANN001 - avoids circular import
        """Total time this track spent with its centre inside ``zone`` (seconds)."""

        total = 0.0
        for i, (t, det) in enumerate(self.detections):
            cx, cy = det.center
            if zone.contains(cx, cy):
                total += (self.detections[i + 1][0] - t) if i + 1 < len(self.detections) else 0.0
        return total

    def total_span(self) -> float:
        if len(self.detections) < 2:
            return 0.0
        return self.detections[-1][0] - self.detections[0][0]


class IouTracker:
    """Greedy IoU tracker; good enough for staged, single-camera clips.

    Tracks that disappear are *closed*, not discarded: the temporal reasoning
    needs the full history (a cashier who walks away is exactly a person whose
    track ends in the middle of the clip).
    """

    def __init__(self, iou_threshold: float = 0.25, max_age: int = 3, min_hits: int = 2):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.min_hits = min_hits
        self._tracks: list[Track] = []
        self._closed: list[Track] = []
        self._next_id = 1

    @property
    def tracks(self) -> list[Track]:
        return [t for t in (*self._tracks, *self._closed) if len(t.detections) >= self.min_hits]

    @property
    def live_tracks(self) -> list[Track]:
        return list(self._tracks)

    def update(self, obs: FrameObservation) -> None:
        unmatched = set(range(len(obs.detections)))
        for track in self._tracks:
            best_i, best_iou = -1, self.iou_threshold
            for i in unmatched:
                score = track.last_detection.iou(obs.detections[i])
                if score > best_iou:
                    best_i, best_iou = i, score
            if best_i >= 0:
                det = obs.detections[best_i]
                track.detections.append((obs.t, det))
                track.frames.append(obs.frame_index)
                track.last_seen_index = obs.frame_index
                unmatched.discard(best_i)
        for i in unmatched:
            det = obs.detections[i]
            self._tracks.append(
                Track(track_id=self._next_id, detections=[(obs.t, det)], frames=[obs.frame_index],
                      last_seen_index=obs.frame_index, first_seen_t=obs.t)
            )
            self._next_id += 1
        self._prune(obs.frame_index)

    def _prune(self, current_index: int) -> None:
        alive: list[Track] = []
        for track in self._tracks:
            if current_index - track.last_seen_index <= self.max_age:
                alive.append(track)
            elif track.detections:
                self._closed.append(track)
        self._tracks = alive
