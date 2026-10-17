"""Face-free action state machines. An action by itself is not a violation."""
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


def workplace_model(tracks, post, width, height, cfg):
    history = [(t, d) for tr in tracks if tr.role == "cashier" for t, _, d in tr.history if d.score >= cfg.min_person_score]
    if not history:
        return None
    start, end = history[0][0], history[-1][0]
    arrived = start > cfg.entry_initial_tolerance_sec
    boxes = [d for t, d in history if (t >= end-cfg.entry_calibration_sec if arrived else t <= start+cfg.seat_calibration_sec)]
    if not boxes:
        return None
    seated = [d for d in boxes if d.height/max(d.width, 1) <= cfg.max_seated_aspect_ratio] or boxes
    return {"cx": float(np.median([d.center[0] for d in seated])),
            "cy": float(np.median([d.center[1] for d in seated])),
            "w": float(np.median([d.width for d in seated])),
            "h": float(np.median([d.height for d in seated])),
            "head_y": float(np.median([d.y1 for d in seated]))}


def at_workplace(d, model, height, cfg, seated=False):
    if model is None:
        return False
    near = (abs(d.center[0]-model["cx"]) <= cfg.seat_radius_x*model["w"] and
            abs(d.center[1]-model["cy"]) <= cfg.seat_radius_y*model["h"])
    standing = d.height > cfg.stand_height_ratio*model["h"] or d.y1 < model["head_y"]-cfg.stand_head_delta_frac*height
    return near and (not standing and d.height/max(d.width, 1) <= cfg.max_seated_aspect_ratio if seated else True)


def _entry(tr, door, width, height, cfg, before=None):
    history = [(t, d) for t, _, d in tr.history if d.score >= cfg.min_person_score and (before is None or t < before)]
    if not history:
        return None
    door_seen = [(t, d) for t, d in history if door.contains(*d.center)]
    if not door_seen:
        return None
    first_t, first = door_seen[0]
    # A stationary fragment born over a printer/glazing is not an entrance.
    moved = [(t, d) for t, _, d in tr.history if t >= first_t and
             np.hypot((d.center[0]-first.center[0])/width, (d.center[1]-first.center[1])/height) >= cfg.entry_travel_frac]
    if not moved:
        return None
    # Backdate to the first *body crossing*, not a head visible through glass.
    crossing = [(t, d) for t, d in history if t >= first_t and d.y2 >= door.y2
                and d.height >= cfg.outsider_min_height_frac*height
                and d.score >= (cfg.min_person_score if tr.role == "cashier" else cfg.outsider_min_score)]
    if not crossing:
        return None
    run = []
    for t, d in crossing:
        if run and t-run[-1][0] > cfg.gap_tolerance_sec:
            run = []
        run.append((t, d))
        if len(run) >= cfg.entry_body_frames and t-run[0][0]+1e-6 >= cfg.entry_body_hold_sec:
            break
    else:
        return None
    start = run[0][0]
    if tr.role == "cashier":
        # Role and subsequent arrival at the workstation supply the identity
        # evidence. Detector flicker during the crossing must not retime it to a
        # later reappearance inside the room.
        return crossing[0][0]
    if start-history[0][0] > cfg.outsider_birth_window_sec:
        # An already observed client standing and moving towards the doorway is
        # not a newly entering person. A later re-entry needs a new door birth
        # (or a separately observed missing interval), not an ID/posture switch.
        prior_body = [(t, d) for t, d in history if t < start-cfg.gap_tolerance_sec
                      and d.score >= cfg.outsider_min_score]
        if prior_body and prior_body[-1][0]-prior_body[0][0] >= cfg.client_settle_sec:
            return None
    # Reject outward movement: walking from the counter towards the door is an
    # exit, even if the detector changed this person's ID while standing up.
    prior = run[0][1]
    later = [d for t, d in history if start+cfg.departure_hold_sec <= t <= start+cfg.entry_direction_window_sec]
    if later:
        # In this configured doorway view, moving right/down is into the room;
        # left/up is towards the exit. Quantiles reject one-frame box jitter.
        dx = float(np.quantile([(d.center[0]-prior.center[0])/width for d in later], cfg.entry_progress_quantile))
        dy = float(np.quantile([(d.center[1]-prior.center[1])/height for d in later], cfg.entry_progress_quantile))
        if max(dx, dy) < cfg.entry_inward_frac:
            return None
    else:
        return None
    return start


def build_triggers(observations: list[FrameObservation], tracks, assignments, post: ZoneSpec,
                   width: int, height: int, min_outsider_sec: float = 0.6, cfg=None, door=None) -> list[Trigger]:
    from .config import TriggerConfig
    cfg = cfg or TriggerConfig(outsider_min_sec=min_outsider_sec)
    door = door or ZoneSpec("door", 0, .05*height, .35*width, .42*height)
    if not observations:
        return []
    model = workplace_model(tracks, post, width, height, cfg)
    triggers = []
    standing_onsets = []
    cashier = next((tr for tr in tracks if tr.role == "cashier"), None)
    first_work = None
    for tr in tracks:
        if tr.role != "cashier":
            continue
        by_frame = {i: d for _, i, d in tr.history}
        pose_start = None
        recorded = False
        for obs in observations:
            d = by_frame.get(obs.frame_index)
            standing = d is not None and model is not None and d.score >= cfg.min_person_score and (
                d.height > cfg.stand_height_ratio*model["h"] or d.y1 < model["head_y"]-cfg.stand_head_delta_frac*height)
            if standing:
                if pose_start is None:
                    pose_start = obs.t
                if not recorded and obs.t-pose_start >= cfg.departure_hold_sec:
                    standing_onsets.append(pose_start)
                    recorded = True
            else:
                pose_start = None
                recorded = False
        flags = []
        for obs in observations:
            d = by_frame.get(obs.frame_index)
            work = d is not None and at_workplace(d, model, height, cfg)
            flags.append((obs.t, d, work))
        entry = (_entry(tr, door, width, height, cfg)
                 if tr.history[0][0] > cfg.entry_initial_tolerance_sec else None)
        # Confirm arrival at the post AFTER crossing the door. A reflection or
        # an overlapping broad post rectangle before entry cannot be arrival.
        work_start = None
        for t, _, work in flags:
            if not work or (entry is not None and t < entry):
                work_start = None
                continue
            if work_start is None:
                work_start = t
            if t-work_start+1e-6 >= cfg.seated_hold_sec:
                first_work = work_start
                break
        if entry is not None and first_work is not None:
            tr.entered_at = entry
            triggers.append(Trigger("cashier_enter", entry, observations[-1].t, tr.track_id,
                                    {"workplace_reached_sec": first_work, "source_track_ids": tr.source_track_ids}))
        seated_since = departure = confirmed = missing_since = last_seen = None
        ready = False
        outside_samples = 0
        for t, d, work in flags:
            if first_work is None or t < first_work:
                continue
            if d is not None and d.score >= cfg.seated_confirmation_score:
                last_seen = t
                missing_since = None
            elif missing_since is None:
                missing_since = t
            if work:
                if seated_since is None:
                    seated_since = t
                if t-seated_since >= cfg.seated_hold_sec:
                    if confirmed is not None:
                        triggers.append(Trigger("cashier_leave", departure, seated_since, tr.track_id,
                            {"confirmed_at": confirmed, "returned_at": seated_since, "exit_sec": None,
                             "source_track_ids": tr.source_track_ids}))
                    departure = confirmed = None
                    outside_samples = 0
                    ready = True
                continue
            seated_since = None
            if not ready:
                continue
            if departure is None:
                departure = t
            if d is not None and d.score >= cfg.min_person_score:
                outside_samples += 1
            out_long = t-departure >= cfg.absence_sec
            disappeared = missing_since is not None and t-missing_since >= cfg.absence_sec
            at_door = d is not None and door.contains(*d.center) and t-departure >= cfg.departure_hold_sec
            if confirmed is None and ((out_long and outside_samples >= cfg.departure_min_frames) or disappeared or at_door):
                confirmed = t
        if departure is not None and confirmed is not None:
            door_times = [t for t, _, d in tr.history if t >= departure and d.score >= cfg.seated_confirmation_score
                          and door.contains(*d.center)]
            exit_sec = (last_seen if last_seen is not None and observations[-1].t-last_seen >= cfg.absence_sec
                        else door_times[0] if door_times else None)
            triggers.append(Trigger("cashier_leave", departure, observations[-1].t, tr.track_id,
                {"confirmed_at": confirmed, "last_seen_sec": last_seen,
                 "exit_sec": exit_sec,
                 "source_track_ids": tr.source_track_ids}))
    # A short, observed stand-up transition immediately preceding departure is
    # its onset. Never substitute the middle of the confirmed absence.
    for trigger in triggers:
        if trigger.kind == "cashier_leave":
            onset = [t for t in standing_onsets if 0 <= trigger.t-t <= cfg.onset_lookback_sec]
            if onset:
                trigger.facts["boundary_crossed_sec"] = trigger.t
                trigger.facts["onset_source"] = "standing_transition_before_boundary"
                trigger.t = min(onset)
    # A seated person opposite the cashier is a client. Initial service arrivals
    # that settle there are not intrusions; entry during established work needs a
    # body crossing the configured staff doorway, not merely a visible head.
    for tr in tracks:
        if tr.role == "cashier":
            continue
        entry = _entry(tr, door, width, height, cfg)
        if entry is None:
            continue
        credible = [(t, d) for t, _, d in tr.history if d.score >= cfg.outsider_min_score and
                    d.height >= cfg.outsider_min_height_frac*height]
        if not credible:
            continue
        tr.entered_at = entry
        tr.role = "entrant"
        triggers.append(Trigger("person_enter", entry, tr.history[-1][0], tr.track_id))
        initial_height = float(np.quantile([d.height for _, d in credible], .8))
        settled = [(t, d) for t, _, d in tr.history if t >= entry and d.score >= cfg.min_person_score and
                   d.height < initial_height*cfg.client_seated_height_ratio and model is not None and d.center[1] < model["cy"]]
        initial_client = (settled and settled[-1][0]-settled[0][0] >= cfg.client_settle_sec and
                          first_work is not None and entry-first_work <= cfg.outsider_working_lead_sec)
        if initial_client:
            tr.role = "client_other"
            continue
        crossing = [(t, d) for t, d in credible if t >= entry and d.y2 >= door.y2]
        working_before = cashier is not None and any(
            entry-cfg.outsider_working_lead_sec <= t < entry and d.score >= cfg.min_person_score
            and at_workplace(d, model, height, cfg) for t, _, d in cashier.history)
        if working_before and crossing and crossing[-1][0]-crossing[0][0] >= cfg.outsider_min_sec:
            triggers.append(Trigger("outsider", entry, crossing[-1][0], tr.track_id,
                                    {"reason": "door_to_cashier_zone", "zone_reached_sec": crossing[0][0],
                                     "support_times": [t for t, _ in crossing],
                                     "support_scores": {str(round(t, 4)): d.score for t, d in crossing}}))
    # Count independently tracked bodies. A track must have shown a credible
    # full body; a small static person-like equipment box cannot make a third.
    real = {tr.track_id for tr in tracks if tr.history and float(np.quantile(
        [d.height for _, _, d in tr.history if d.score >= cfg.min_person_score] or [0], .8)) >= cfg.outsider_min_height_frac*height}
    run = []
    run_scores = {}
    for obs in observations:
        people = [(tr, d) for tr, d in assignments.get(obs.frame_index, []) if tr.track_id in real
                  and d.score >= cfg.outsider_min_score]
        if len(people) >= 3:
            if run and obs.t-run[-1] > cfg.gap_tolerance_sec:
                run = []
                run_scores = {}
            run.append(obs.t)
            run_scores[str(round(obs.t, 4))] = min(d.score for _, d in people)
            if run[-1]-run[0] >= cfg.outsider_min_sec and not any(t.kind == "outsider" and t.t <= run[0] <= t.end for t in triggers):
                triggers.append(Trigger("outsider", run[0], obs.t, facts={"reason": "three_people", "person_count": len(people), "support_times": list(run), "support_scores": dict(run_scores)}))
        else:
            run = []
            run_scores = {}
    return sorted(triggers, key=lambda tr: (tr.t, tr.kind))
