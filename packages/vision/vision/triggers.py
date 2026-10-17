"""Observable action triggers; an ordinary departure is NOT a violation."""
from dataclasses import asdict, dataclass, field
import numpy as np

from .types import FrameObservation, ZoneSpec


@dataclass
class Trigger:
    kind: str
    t: float
    end: float
    track_id: int | None = None
    facts: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


def build_triggers(observations: list[FrameObservation], tracks, assignments, post: ZoneSpec,
                   width: int, height: int, min_outsider_sec: float = 0.6) -> list[Trigger]:
    triggers = []
    cashier_heads = [d.y1 for tr in tracks if tr.role == "cashier" for _, _, d in tr.history if d.score >= .3]
    cashier_boxes = [d for tr in tracks if tr.role == "cashier" for _, _, d in tr.history if d.score >= .3]
    # A customer across the desk must not be treated as entering the staff side
    # just because an upper-body centre overlaps the broad post rectangle.
    staff_side_y = float(np.median(cashier_heads)) + .12*height if cashier_heads else post.y2
    lateral_x = (float(np.median([d.center[0] for d in cashier_boxes])) -
                 .8*float(np.median([d.width for d in cashier_boxes]))) if cashier_boxes else post.x1
    seat_y = float(np.median([d.center[1] for d in cashier_boxes])) if cashier_boxes else post.y2
    for tr in tracks:
        if tr.entered_at is not None:
            triggers.append(Trigger("cashier_enter" if tr.role == "cashier" else "person_enter",
                                    tr.entered_at, tr.history[-1][0], tr.track_id))
        if tr.role == "cashier":
            # Require departure from the seat followed by disappearance near the
            # door, not a missed person box while seated or a walk to the safe.
            seated = [(t, d) for t, _, d in tr.history if post.contains(*d.center)]
            if not seated:
                continue
            baseline = sorted(d.height for _, d in seated)[len(seated)//2]
            last_seat = None
            onset = None
            for t, _, d in tr.history:
                at_post = post.contains(*d.center)
                if at_post and d.height < baseline * 1.3:
                    last_seat = t
                    onset = None
                elif last_seat is not None and onset is None:
                    onset = t
            last_t, _, last = tr.history[-1]
            door_side = last.center[0] < width * 0.45 and last.center[1] < height * 0.62
            if onset is not None and door_side and last_t < observations[-1].t - 0.8:
                triggers.append(Trigger("cashier_leave", onset, last_t, tr.track_id,
                                        {"last_seen_sec": last_t, "last_seated_sec": last_seat}))
        elif tr.role == "entrant":
            # Passage from the door towards the cashier seat / desk. The broad
            # counter zone alone is insufficient: ordinary clients use it too.
            initial = tr.history[0][2]
            reached = [(t, d) for t, _, d in tr.history
                       if d.score >= .3 and d.y2 > staff_side_y
                       and ((d.center[0]-initial.center[0]) / width)**2 + ((d.center[1]-initial.center[1]) / height)**2 > 0.05**2
                       and (d.center[0] < lateral_x or d.center[1] > seat_y + .05*height)]
            if reached and reached[-1][0] - reached[0][0] >= min_outsider_sec:
                triggers.append(Trigger("outsider", tr.entered_at, reached[-1][0], tr.track_id,
                                        {"reason": "door_to_cashier_zone", "zone_reached_sec": reached[0][0]}))
    # Three actual, confirmed tracks for a sustained interval, not low-score or
    # overlapping detections. Backdate to the entrance, not the interval centre.
    run = []
    for obs in observations:
        # Count people in the usable room view, not tiny partial bodies visible
        # through the distant glazing. This geometric prior is reported in trace.
        people = [(tr, d) for tr, d in assignments.get(obs.frame_index, [])
                  if d.score >= 0.3 and d.height >= height*0.16 and d.y2 > staff_side_y]
        independent = []
        for tr, d in sorted(people, key=lambda p: -p[1].score):
            if not any(d.iou(other) > 0.55 for _, other in independent):
                independent.append((tr, d))
        if len(independent) >= 3:
            if run and obs.t-run[-1][0] > 0.9:
                run = []
            run.append((obs.t, independent))
            if run[-1][0] - run[0][0] >= min_outsider_sec:
                entrants = [tr for tr, _ in independent if tr.role == "entrant" and tr.entered_at is not None
                            and abs(tr.entered_at-run[0][0]) <= 4]
                start = max((tr.entered_at for tr in entrants), default=run[0][0])
                if not any(t.kind == "outsider" and abs(t.t-start) < 3 for t in triggers):
                    triggers.append(Trigger("outsider", start, obs.t, entrants[0].track_id if entrants else None,
                                            {"reason": "three_people", "person_count": len(independent)}))
        else:
            run = []
    return sorted(triggers, key=lambda tr: (tr.t, tr.kind))
