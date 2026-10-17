"""Typed, fail-closed temporal rule engine. No EV01 absence heuristic.

Scene-dependent rules require positive scene evidence. COCO detections alone
are diagnostic, never treated as money, an unlocked screen or an open safe.
"""
from __future__ import annotations

from rules.registry import RuleRegistry

from .config import RuleEngineConfig
from .scene_state import SceneState
from .triggers import Trigger
from .types import EventCandidate


def make_event(code: str, start: float, end: float, fact: str, registry: RuleRegistry,
               duration: float, meta: dict | None = None) -> EventCandidate | None:
    rule = registry.for_event(code)
    if rule is None:
        return None
    if code == "OUTSIDER":
        risk = "Это может указывать на нарушение режима изоляции кассы, но не доказывает отсутствие служебных полномочий."
    elif code == "LEAVE_SCREEN_UNLOCKED":
        risk = "Это может создавать риск несанкционированного доступа; норма задаёт автоблокировку по неактивности, а не обязанность вручную блокировать экран при каждом уходе."
    else:
        risk = "Это может указывать на несоблюдение требования; необходима проверка контекста человеком."
    caveat = " ".join(rule.cannot_infer)
    return EventCandidate(
        event_code=code, event_label=rule.event_label, start_sec=start, peak_sec=start,
        end_sec=min(duration, max(start, end)), rule_id=rule.rule_id,
        observation=f"С {start:.2f} с: {fact}",
        hypothesis=f"{risk} {rule.full_citation()} По видео не устанавливается: {caveat}",
        evidence_times=[round(max(0, start-1), 3), round(start, 3), round(min(duration-0.05, start+1.5), 3)],
        confidence=0.75, meta=meta or {},
    )


def _runs(samples, predicate, min_sec):
    run = []
    for sample in samples:
        if predicate(sample):
            if run and sample[0].t - run[-1][0].t > 0.8:
                if run[-1][0].t-run[0][0].t >= min_sec:
                    yield run
                run = []
            run.append(sample)
        else:
            if run and run[-1][0].t-run[0][0].t >= min_sec:
                yield run
            run = []
    if run and run[-1][0].t-run[0][0].t >= min_sec:
        yield run


def evaluate_rules(triggers: list[Trigger], scenes: list[SceneState], presence: dict[float, dict],
                   duration: float, registry: RuleRegistry, cfg: RuleEngineConfig | None = None):
    cfg = cfg or RuleEngineConfig()
    scenes = sorted(scenes, key=lambda s: s.t)
    events = []

    def emit(code, start, end, fact, meta=None):
        event = make_event(code, start, end, fact, registry, duration, meta)
        if event is not None:
            events.append(event)

    for tr in triggers:
        if tr.kind == "outsider":
            reason = (f"одновременно видны {tr.facts['person_count']} человека" if tr.facts.get("person_count")
                      else f"трек {tr.track_id}, не отнесённый к кассиру, появляется через область двери и проходит в кассовую зону к {tr.facts.get('zone_reached_sec', tr.t):.2f} с")
            emit("OUTSIDER", tr.t, tr.end, reason + ".", {"trigger": tr.as_dict()})
        elif tr.kind == "cashier_leave":
            nearby = [s for s in scenes if tr.t-1.5 <= s.t <= tr.t+0.4]
            if not nearby:
                continue
            scene = min(nearby, key=lambda s: abs(s.t-tr.t))
            valuables = [i.cls for i in scene.desk_items if i.score >= cfg.scene_threshold and i.cls in
                         {"cash", "money", "banknotes", "valuables", "bag", "valuables_bag", "handbag", "backpack", "clothing_or_bag"}]
            base = f"кассир (трек {tr.track_id}) начинает уход с рабочего места; "
            if valuables:
                emit("LEAVE_VALUABLES", tr.t, tr.end, base + f"на столе в {scene.t:.2f} с различимы: {', '.join(valuables)}.")
            if scene.screen_state == "document":
                emit("LEAVE_SCREEN_UNLOCKED", tr.t, tr.end, base + f"в {scene.t:.2f} с на экране виден рабочий документ, а не экран блокировки.")
            if scene.safe_open is not None and scene.safe_open >= cfg.scene_threshold:
                emit("SAFE_OPEN", tr.t, tr.end, base + f"дверца сейфа видимо открыта в {scene.t:.2f} с.")
        elif tr.kind == "cashier_enter":
            after = [(s, {}) for s in scenes if tr.t <= s.t <= tr.end]
            for run in _runs(after, lambda pair: pair[0].door_open is not None and pair[0].door_open >= cfg.scene_threshold,
                             cfg.door_hold_sec + 1e-6):
                if run[0][0].t > tr.t+1:
                    break  # a later opening is not failure to close on entry
                if any(s.door_open is not None and s.door_open < cfg.scene_threshold
                       for s, _ in after if s.t < run[0][0].t):
                    break  # it was closed after entry, then reopened
                emit("DOOR_OPEN", tr.t, run[-1][0].t,
                     f"кассир (трек {tr.track_id}) входит; дверь остаётся видимо открытой с {run[0][0].t:.2f} до {run[-1][0].t:.2f} с.")
                break
    samples = [(s, presence.get(round(s.t, 4), {})) for s in scenes]
    predicates = {
        "PERSONAL_ITEMS": lambda s, p: p.get("cashier_present", False) and any(
            i.score >= cfg.scene_threshold and i.cls in {"cup", "bottle", "food", "clothing_or_bag", "handbag", "backpack", "clothing"} for i in s.desk_items),
        "PHONE_USE": lambda s, p: p.get("cashier_present", False) and any(
            i.score >= cfg.scene_threshold and i.cls in {"phone", "cell phone"} for i in s.desk_items),
        "MANUAL_COUNT": lambda s, p: p.get("cashier_seated", False) and s.cashier_hand_counting is not None and s.cashier_hand_counting >= cfg.scene_threshold,
    }
    for code, predicate in predicates.items():
        for run in _runs(samples, lambda pair: predicate(*pair), cfg.min_scene_sec):
            s, _ = run[0]
            fact = ("кассир сидит; виден ручной пересчёт банкнот." if code == "MANUAL_COUNT" else
                    f"при кассире на рабочем столе различимы предметы: {', '.join(i.cls for i in s.desk_items if i.score >= cfg.scene_threshold)}.")
            emit(code, s.t, run[-1][0].t, fact)
    # One episode per code, retaining the beginning and its evidence triplet.
    merged = []
    for e in sorted(events, key=lambda e: (e.event_code, e.start_sec)):
        if merged and e.event_code == merged[-1].event_code and e.start_sec <= merged[-1].end_sec+3:
            merged[-1].end_sec = max(merged[-1].end_sec, e.end_sec)
        else:
            merged.append(e)
    return sorted(merged, key=lambda e: e.start_sec)
