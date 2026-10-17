"""Curator-authorized baseline completeness layer. No detector or scene model.

events.py and deskobjects.py are byte-identical to 5f4ad60. The only new B
suppression is an observed lockscreen hold at the departure, plus A precedence.
"""
from dataclasses import asdict, replace

from .deskobjects import DeskWatcher
from .events import FrameState, detect_ev01, detect_ev02, merge_events
from .rule_engine import make_event
from .zones import infer_zones


def infer_baseline_zones(observations, width, height, cfg):
    # Remove only the three filters added after baseline 5f4ad60. Keep the
    # original grid, person-size margins, fallback and configured overrides.
    zcfg = replace(cfg.zones,max_person_center_y=1.,min_person_score=0.,min_person_height_frac=0.)
    zones = infer_zones(observations,width,height,zcfg,cfg.post_zone_override)
    post = zones[0]
    share = sum(any(post.contains(*d.center) for d in o.detections) for o in observations)/max(1,len(observations))
    if cfg.post_zone_override is None and post.source=='auto' and share<zcfg.min_zone_presence_share:
        zones = infer_zones(observations,width,height,zcfg,zcfg.fallback_post_rect)
        zones[0].confidence = round(min(zones[0].confidence,.25),3)
    return zones


def baseline_states(observations, assignments, post, counter, dt):
    zone_time = {}
    for obs in observations:
        for tr, d in assignments.get(obs.frame_index, []):
            if post.contains(*d.center):
                zone_time[tr.track_id] = zone_time.get(tr.track_id, 0) + dt
    primary = max(zone_time, key=zone_time.get) if zone_time else None
    if primary is not None and zone_time[primary] < max(2*dt, .6):
        primary = None
    states = []
    for obs in observations:
        people = assignments.get(obs.frame_index, [])
        n_post = int(sum(post.contains(*d.center) for _,d in people))
        clients = int(sum(tr.track_id != primary and counter.contains(*d.center) for tr,d in people))
        others = sum(tr.track_id != primary for tr,d in people)
        occupied = n_post > 0
        states.append(FrameState(obs.t, obs.frame_index, occupied, clients > 0,
                                 n_post, clients, len(obs.detections), others, primary))
    return states


def desk_candidates(observations, frames, post, width, cfg, states, dt):
    if not cfg.ev02.enabled:
        return [], {}
    watcher = DeskWatcher(cfg.desk)
    scale = cfg.desk.small_width / width
    masks = [watcher.person_masks(o.detections, f.shape, scale) for o,f in zip(observations, frames)]
    background, valid = watcher.build_background(frames, masks)
    if background is None:
        return [], {}
    per_frame = {o.frame_index: watcher.detect_blobs(f, background, valid, mask, post, o.frame_index, scale)
                 for o,f,mask in zip(observations,frames,masks)}
    blobs = watcher.track_blobs(per_frame, observations)
    raw = detect_ev02(blobs, {o.frame_index:o for o in observations}, {s.frame_index:s for s in states}, cfg.ev02, dt)
    return raw, {str(i): [asdict(b) for b in bs] for i,bs in per_frame.items()}


def lockscreen_at_departure(scenes, onset, cfg):
    """A continuous observed run must contain the departure, not some later time."""
    run = []
    for scene in sorted(scenes, key=lambda s:s.t):
        if scene.screen_state != "lockscreen" or (run and scene.t-run[-1] > cfg.locked_max_gap_sec+1e-6):
            run = []
        if scene.screen_state == "lockscreen":
            run.append(scene.t)
            if run[0] <= onset <= scene.t and scene.t-run[0]+1e-6 >= cfg.locked_hold_sec:
                return {"start":run[0], "end":scene.t, "frames":len(run)}
    return None


def combine_layers(layer_a, raw_ev01, raw_ev02, scenes, duration, registry, cfg, decisions=None):
    """Compare B ONLY against the original A set; never let B suppress A."""
    if not cfg.hybrid.enabled:
        return list(layer_a)
    candidates = []
    for legacy, code, raws in (("EV01", "LEAVE_DURING_SERVICE", raw_ev01),
                               ("EV02", "LEAVE_VALUABLES", raw_ev02)):
        if not raws and decisions is not None:
            decisions.append({"layer":"B", "legacy":legacy, "rule":code,
                              "reason":"no_baseline_candidate"})
        for raw in raws:
            # Baseline peak is the FIRST compound-condition onset, not a
            # midpoint: either departure during service, or client arrival
            # to an already unattended post; EV02 peak is unattended onset.
            onset = float(raw["peak"])
            end = float(raw["end"])
            episode_start = float(raw['start'])
            overlap = any(e.start_sec <= end and onset <= e.end_sec for e in layer_a)
            locked = lockscreen_at_departure(scenes, episode_start, cfg.hybrid) if legacy == "EV01" else None
            enabled = cfg.rules.policy(code).enabled
            reason = "type_disabled" if not enabled else "layer_a_overlap" if overlap else "stable_lockscreen_at_departure" if locked else "emitted"
            if decisions is not None:
                decisions.append({"layer":"B", "legacy":legacy, "rule":code, "start":onset,
                                  "end":end, "reason":reason, "lockscreen_hold":locked, "raw":raw})
            if not enabled or overlap or locked:
                continue
            if legacy == "EV01":
                fact = "рабочая зона не занята, у стола наблюдается другой трек человека (возможный клиент); завершение обслуживания документально не устанавливается."
            else:
                fact = "в период незанятой рабочей зоны на поверхности остаётся статичный изменившийся предмет; DeskWatcher не устанавливает, что это деньги или ценности."
            event = make_event(code, onset, end, fact, registry, duration,
                               {"layer":"B", "legacy":legacy, "raw":raw})
            if event is not None:
                event.confidence = float(raw["confidence"])
                candidates.append(event)
            elif decisions is not None:
                decisions[-1]["reason"] = "registry_not_approved_or_missing"
    candidates = merge_events(candidates, cfg.merge_gap_sec)
    # Original baseline order/option: merge first, then avoid describing one
    # EV01 episode twice as EV02.
    if cfg.prefer_absence_event:
        service = [e for e in candidates if e.event_code == "LEAVE_DURING_SERVICE"]
        candidates = [e for e in candidates if e.event_code != "LEAVE_VALUABLES" or
                      not any(s.meta['raw']['start']-1 <= e.start_sec <= s.end_sec+1 for s in service)]
    for event in candidates:
        # Baseline merging combines evidence times; preserve onset evidence contract.
        event.evidence_times = [round(max(0,event.start_sec-1),3),round(event.start_sec,3),
                                round(min(duration-.05,event.start_sec+1.5),3)]
    candidates = candidates[:max(0,cfg.max_events-len(layer_a))]
    if decisions is not None:
        for item in decisions:
            if item['reason']=='emitted' and not any(e.event_code==item['rule'] and
                    e.start_sec<=item['start']<=e.end_sec for e in candidates):
                item['reason']='legacy_episode_duplicate_or_output_limit'
    return sorted([*layer_a, *candidates], key=lambda e:e.start_sec)
