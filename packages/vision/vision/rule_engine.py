"""Typed rule engine: per-type thresholds and dense-frame persistence gates."""
from __future__ import annotations
from dataclasses import asdict, replace

from rules.registry import RuleRegistry
from .config import RuleEngineConfig, RulePolicy
from .scene_state import SceneState
from .triggers import Trigger
from .types import EventCandidate

VALUABLE_CLASSES = {"cash", "cash_bag"}
PERSONAL_CLASSES = {"cup", "food", "clothing_or_bag", "handbag", "backpack"}


def make_event(code, start, end, fact, registry, duration, meta=None):
    rule = registry.for_event(code)
    if rule is None:
        return None
    if code == "OUTSIDER":
        risk = "Это может указывать на нарушение режима изоляции кассы, но не доказывает отсутствие служебных полномочий."
    elif code == "LEAVE_SCREEN_UNLOCKED":
        risk = "Это может создавать риск несанкционированного доступа; норма задаёт автоблокировку по неактивности, а не обязанность вручную блокировать экран при каждом уходе."
    else:
        risk = "Это может указывать на несоблюдение требования; необходима проверка контекста человеком."
    return EventCandidate(event_code=code, event_label=rule.event_label, start_sec=start, peak_sec=start,
        end_sec=min(duration, max(start, end)), rule_id=rule.rule_id,
        observation=f"С {start:.2f} с: {fact}",
        hypothesis=f"{risk} {rule.full_citation()} По видео не устанавливается: {' '.join(rule.cannot_infer)}",
        evidence_times=[round(max(0, start-1), 3), round(start, 3), round(min(max(0, duration-.05), start+1.5), 3)],
        confidence=.75, meta=meta or {})


def stable_runs(samples, predicate, policy: RulePolicy):
    """Only supplied dense samples count. False/unknown and long gaps reset."""
    run = []
    for sample in samples:
        if not predicate(*sample) or (run and sample[0].t-run[-1][0].t > policy.max_gap_sec):
            if len(run) >= policy.min_frames and run[-1][0].t-run[0][0].t + 1e-6 >= policy.min_sec:
                yield run
            run = []
        if predicate(*sample):
            run.append(sample)
    if len(run) >= policy.min_frames and run[-1][0].t-run[0][0].t + 1e-6 >= policy.min_sec:
        yield run


def evaluate_rules(triggers: list[Trigger], scenes: list[SceneState], presence: dict[float, dict],
                   duration: float, registry: RuleRegistry, cfg: RuleEngineConfig | None = None,
                   decisions: list | None = None, track_roles: dict | None = None):
    cfg = cfg or RuleEngineConfig()
    scenes = sorted(scenes, key=lambda s: s.t)
    samples = [(s, presence.get(round(s.t, 4), {})) for s in scenes]
    events = []

    def record(code, trigger, window, reason, predicate=None):
        if decisions is None:
            return
        action = None if trigger is None else {
            "kind": trigger.kind, "start": trigger.t, "end": trigger.end,
            "track_id": trigger.track_id,
            "role": (track_roles or {}).get(trigger.track_id,
                "cashier" if trigger.kind.startswith("cashier_") else "unknown")}
        decisions.append({"rule": code, "trigger": action, "reason": reason,
            "policy": asdict(cfg.policy(code)),
            "positive_mask": [bool(predicate(s,p)) for s,p in window] if predicate is not None else None,
            "scene_window": [
                {"t": s.t, "screen_state": s.screen_state,
                 "desk_items": [asdict(i) for i in s.desk_items], "safe_open": s.safe_open,
                 "door_open": s.door_open, "hand_counting": s.cashier_hand_counting,
                 "presence": p} for s, p in window]})

    for code, kind in {"LEAVE_VALUABLES": "cashier_leave", "LEAVE_SCREEN_UNLOCKED": "cashier_leave",
                       "SAFE_OPEN": "cashier_leave", "DOOR_OPEN": "cashier_enter", "OUTSIDER": "outsider"}.items():
        if not any(t.kind == kind for t in triggers):
            record(code, None, [], "missing_trigger")

    def emit(code, start, end, fact, support, trigger=None):
        policy = cfg.policy(code)
        if not policy.enabled:
            return
        meta = {"support_times": support, "min_frames": policy.min_frames, "min_sec": policy.min_sec,
                "threshold": policy.threshold}
        if trigger is not None:
            meta["trigger"] = trigger.as_dict()
        event = make_event(code, start, end, fact, registry, duration, meta)
        if event is not None:
            events.append(event)
        record(code, trigger, [(s, p) for s, p in samples if start <= s.t <= end],
               "emitted" if event is not None else "registry_not_approved_or_missing")

    for tr in triggers:
        if tr.kind == "outsider":
            policy = cfg.policy("OUTSIDER")
            if not policy.enabled:
                record("OUTSIDER", tr, [], "type_disabled")
                continue
            times = sorted(set(tr.facts.get("support_times", [])))
            dense = {round(s.t, 4) for s in scenes}
            times = [t for t in times if round(t, 4) in dense]
            support = {round(t, 4) for t in times}
            scores = tr.facts.get("support_scores", {})
            runs = list(stable_runs([(s, p) for s, p in samples if tr.t <= s.t <= tr.end],
                                   lambda s, p: round(s.t, 4) in support and scores.get(str(round(s.t, 4)), 1.0) >= policy.threshold, policy))
            if not runs:
                record("OUTSIDER", tr, [(s,p) for s,p in samples if tr.t <= s.t <= tr.end], "insufficient_dense_support")
                continue
            fact = (f"одновременно видны {tr.facts['person_count']} человека" if tr.facts.get("person_count") else
                    f"трек {tr.track_id}, не отнесённый к кассиру, пересекает область двери во время работы кассира и проходит в служебную зону")
            emit("OUTSIDER", tr.t, tr.end, fact+".", times, tr)
        elif tr.kind == "cashier_leave":
            for code in ("LEAVE_VALUABLES", "LEAVE_SCREEN_UNLOCKED", "SAFE_OPEN"):
                policy = cfg.policy(code)
                if not policy.enabled:
                    record(code, tr, [], "type_disabled")
                    continue
                if code in {"LEAVE_VALUABLES", "LEAVE_SCREEN_UNLOCKED"} and "exit_sec" in tr.facts and tr.facts["exit_sec"] is None:
                    record(code, tr, [(s,p) for s,p in samples if tr.t <= s.t <= tr.end], "exit_not_confirmed")
                    continue
                confirmation = tr.facts.get("confirmed_at", tr.t)
                reference = tr.facts.get("exit_sec") if code != "SAFE_OPEN" else None
                reference = confirmation if reference is None else reference
                window_start = (tr.t if code != "SAFE_OPEN" else reference)-policy.lookback_sec
                window_end = tr.end if code != "SAFE_OPEN" else min(tr.end, reference+cfg.leave_observation_sec)
                window = [(s, p) for s, p in samples if window_start <= s.t <= window_end]
                def positive(s, p):
                    if code == "LEAVE_SCREEN_UNLOCKED":
                        return s.screen_state == "document"
                    if code == "SAFE_OPEN":
                        unattended = p.get("cashier_present") is False or p.get("cashier_near_safe") is False
                        return s.safe_open is not None and s.safe_open >= policy.threshold and unattended
                    return any(i.cls in VALUABLE_CLASSES and i.score >= policy.threshold for i in s.desk_items)
                runs = list(stable_runs(window, positive, policy))
                # A normal departure after locking/clearing the desk must not
                # inherit an earlier document/cash state from before preparation.
                if not runs:
                    field = {"LEAVE_SCREEN_UNLOCKED": "screen_state", "SAFE_OPEN": "safe_open"}.get(code)
                    reason = ("no_dense_samples" if not window else "scene_unknown" if field and
                              all(getattr(s, field) is None for s,p in window) else "no_stable_positive_or_presence_gate")
                    record(code, tr, window, reason, predicate=positive)
                    continue
                run = runs[-1]
                def negative(s, p):
                    if code == "LEAVE_SCREEN_UNLOCKED":
                        return s.screen_state in {"lockscreen", "off", "closed", "locked"}
                    if code == "SAFE_OPEN":
                        return s.safe_open is not None and s.safe_open < policy.threshold
                    return not positive(s, p)
                negative_runs = list(stable_runs(window, negative, policy))
                if negative_runs and negative_runs[-1][-1][0].t > run[-1][0].t:
                    record(code, tr, window, "later_stable_negative", predicate=positive)
                    continue
                fact = {"LEAVE_SCREEN_UNLOCKED": "на экране остаётся рабочий документ, а не экран блокировки",
                        "LEAVE_VALUABLES": "на столе различимы наличные или инкассаторская сумка",
                        "SAFE_OPEN": "сейф видимо открыт при оставлении рабочего места"}[code]
                emit(code, tr.t, tr.end, f"кассир (трек {tr.track_id}) покидает рабочее место; {fact} "
                     f"(подтверждено с {run[0][0].t:.2f} до {run[-1][0].t:.2f} с).", [s.t for s, _ in run], tr)
        elif tr.kind == "cashier_enter":
            policy = cfg.policy("DOOR_OPEN")
            if not policy.enabled:
                record("DOOR_OPEN", tr, [], "type_disabled")
                continue
            window = [(s, p) for s, p in samples if tr.t <= s.t <= min(tr.end, tr.t+cfg.entry_observation_sec)]
            runs = list(stable_runs(window, lambda s, p: s.door_open is not None and s.door_open >= policy.threshold, policy))
            if not runs:
                record("DOOR_OPEN", tr, window, "scene_unknown" if window and all(s.door_open is None for s,p in window)
                       else "no_stable_positive")
            for run in runs:
                clear_policy = replace(policy, min_frames=policy.clear_frames, min_sec=policy.clear_sec)
                closed = bool(list(stable_runs([(s, p) for s, p in window if s.t < run[0][0].t],
                    lambda s, p: s.door_open is not None and s.door_open < policy.threshold, clear_policy)))
                if closed or run[0][0].t-tr.t > policy.lookback_sec:
                    record("DOOR_OPEN", tr, window, "door_closed_before_run" if closed else "positive_too_late")
                    break
                emit("DOOR_OPEN", tr.t, run[-1][0].t, f"кассир (трек {tr.track_id}) входит; дверь остаётся открытой "
                     f"с {run[0][0].t:.2f} до {run[-1][0].t:.2f} с.", [s.t for s, _ in run], tr)
                break
    for code in ("PERSONAL_ITEMS", "PHONE_USE", "MANUAL_COUNT"):
        policy = cfg.policy(code)
        if not policy.enabled:
            record(code, None, [], "type_disabled")
            continue
        def positive(s, p):
            if code == "MANUAL_COUNT":
                return p.get("cashier_seated", False) and s.cashier_hand_counting is not None and s.cashier_hand_counting >= policy.threshold
            classes = PERSONAL_CLASSES if code == "PERSONAL_ITEMS" else {"phone"}
            return p.get("cashier_present", False) and any(i.cls in classes and i.score >= policy.threshold for i in s.desk_items)
        runs = list(stable_runs(samples, positive, policy))
        if not runs:
            record(code, None, samples, "no_stable_positive_or_presence_gate", predicate=positive)
        for run in runs:
            s, _ = run[0]
            fact = ("кассир сидит; виден ручной пересчёт банкнот." if code == "MANUAL_COUNT" else
                    f"при кассире на столе различимы: {', '.join(i.cls for i in s.desk_items if i.score >= policy.threshold)}.")
            emit(code, s.t, run[-1][0].t, fact, [s.t for s, _ in run])
    merged = []
    for event in sorted(events, key=lambda e: (e.event_code, e.start_sec)):
        if merged and event.event_code == merged[-1].event_code and event.start_sec <= merged[-1].end_sec+cfg.merge_gap_sec:
            merged[-1].end_sec = max(merged[-1].end_sec, event.end_sec)
        else:
            merged.append(event)
    return sorted(merged, key=lambda e: e.start_sec)
