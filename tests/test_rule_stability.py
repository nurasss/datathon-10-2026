"""Production persistence, semantic scene vocabulary, and trigger regressions."""
from dataclasses import replace

import pytest
from rules.registry import RuleRegistry
from vision.config import PipelineConfig, RuleEngineConfig, TriggerConfig
from vision.scene_state import SceneState, DeskItem
from vision.rule_engine import evaluate_rules
from vision.triggers import Trigger, build_triggers
from vision.tracking import PersonTrack, track_people
from vision.types import Detection, FrameObservation, ZoneSpec
from vision.evaluation import type_comparison, score_actions
from vision.zones import infer_zones


def states(item=None, seconds=4.0, score=1.0, counting=None):
    return [SceneState(round(i*.2, 4), None, [DeskItem(item, (.3,.3,.4,.4), score)] if item else [],
                       None, None, counting) for i in range(int(seconds*5)+1)]


def engine(scenes, cfg=None, triggers=(), seated=True):
    presence = {s.t: {"cashier_present": True, "cashier_seated": seated} for s in scenes}
    return evaluate_rules(list(triggers), scenes, presence, 30, RuleRegistry.load(), cfg)


@pytest.mark.parametrize("item", ["handbag", "backpack", "clothing_or_bag", "other", "valuables", "banknotes"])
def test_personal_bag_is_never_treated_as_cash(item):
    events = engine(states(item), triggers=[Trigger("cashier_leave", 1, 8)])
    assert "LEAVE_VALUABLES" not in {e.event_code for e in events}


@pytest.mark.parametrize("item", ["cash", "cash_bag"])
def test_scene_cash_vocabulary(item):
    events = engine(states(item), triggers=[Trigger("cashier_leave", 1, 8)])
    assert [e.event_code for e in events] == ["LEAVE_VALUABLES"]
    assert events[0].start_sec == events[0].peak_sec == 1
    assert events[0].evidence_times == [0, 1, 2.5]


@pytest.mark.parametrize("code,item,counting", [("PERSONAL_ITEMS", "cup", None), ("MANUAL_COUNT", None, 1.0)])
def test_production_default_requires_three_seconds(code, item, counting):
    assert engine(states(item, seconds=2.8, counting=counting)) == []
    assert [e.event_code for e in engine(states(item, seconds=3.0, counting=counting))] == [code]


@pytest.mark.parametrize("code,item,counting", [("PERSONAL_ITEMS", "cup", None), ("MANUAL_COUNT", None, .9)])
def test_raised_threshold_and_disabled_type(code, item, counting):
    scenes = states(item, score=.85, counting=counting)
    assert engine(scenes) == []
    cfg = RuleEngineConfig()
    cfg.types[code].enabled = False
    assert engine(states(item, counting=1.0 if counting else None), cfg) == []


def test_frame_count_and_long_gaps_are_not_wall_time_persistence():
    cfg = RuleEngineConfig()
    cfg.types["PERSONAL_ITEMS"].min_frames = 30
    assert engine(states("cup", seconds=4), cfg) == []
    scenes = [s for s in states("cup", seconds=6) if s.t < 2 or s.t > 4]
    assert engine(scenes) == []


def test_closed_screen_before_exit_cancels_old_document():
    scenes = [replace(s, screen_state="document" if s.t < 3 else "lockscreen") for s in states(seconds=6)]
    assert not engine(scenes, triggers=[Trigger("cashier_leave", 1, 10, facts={"exit_sec": 5})])


def test_one_contradictory_frame_does_not_replace_a_stable_document_state():
    scenes = [replace(s, screen_state="document") for s in states(seconds=6)]
    scenes[-1].screen_state = "lockscreen"
    events = engine(scenes, triggers=[Trigger("cashier_leave", 1, 10, facts={"exit_sec": 4})])
    assert [e.event_code for e in events] == ["LEAVE_SCREEN_UNLOCKED"]


def test_open_safe_under_cashier_supervision_is_not_unattended():
    scenes = [replace(s, safe_open=1.0) for s in states(seconds=4)]
    trigger = Trigger("cashier_leave", 1, 8)
    p = {s.t: {"cashier_present": True, "cashier_near_safe": True} for s in scenes}
    assert evaluate_rules([trigger], scenes, p, 10, RuleRegistry.load()) == []
    p = {s.t: {"cashier_present": False, "cashier_near_safe": False} for s in scenes}
    assert [e.event_code for e in evaluate_rules([trigger], scenes, p, 10, RuleRegistry.load())] == ["SAFE_OPEN"]


def test_config_roundtrip_and_per_type_flags():
    cfg = PipelineConfig.load()
    same = PipelineConfig.from_dict(cfg.to_dict())
    assert same.to_dict() == cfg.to_dict()
    assert same.rules.policy("PERSONAL_ITEMS").min_sec >= 3
    with pytest.raises(ValueError):
        PipelineConfig.from_dict({"rules": {"types": {"PERSONAL_ITEMS": {"min_frames": 0}}}})


def test_type_ok_is_exact_code_set_and_norms_have_no_action_time():
    e = engine(states("cash"), triggers=[Trigger("cashier_leave", 1, 8)])[0]
    assert type_comparison([e], [{"type_code": "LEAVE_VALUABLES"}])["type_ok"]
    assert not type_comparison([e], [{"type_code": "SAFE_OPEN"}])["type_ok"]
    row = score_actions("X-31", [Trigger("cashier_enter", 3, 9).as_dict()], [{"type_code": "NONE", "start_sec": "0"}])
    assert row["expected_sec"] is None and row["timing_ok"] is None and row["present"]


def test_foreground_chair_detection_cannot_become_cashier_zone():
    cfg = PipelineConfig()
    chair = Detection(20, 730, 400, 848, .95)
    person = Detection(170, 260, 280, 470, .7)
    observations = [FrameObservation(i*.2, i, [chair, person]) for i in range(30)]
    post = infer_zones(observations, 464, 848, cfg.zones)[0]
    assert post.y2 < 730
    door = ZoneSpec("door", 0, 0, 150, 350)
    tracks, assignments = track_people(observations, post, door, 464, 848, cfg.triggers)
    cashier = [tr for tr in tracks if tr.role == "cashier"]
    assert len(cashier) == 1
    assert all(d.center[1] < 730 for _, _, d in cashier[0].history)


def test_entry_recovers_cashier_appearing_near_door_before_seat():
    cfg = TriggerConfig()
    tr = PersonTrack(3, role="cashier")
    observations, assignments = [], {}
    for i in range(40):
        t = 2+i*.2
        x = min(200, 80+i*7)
        d = Detection(x, 160 if i < 20 else 260, x+90, 450, .9)
        tr.history.append((t, i, d))
        observations.append(FrameObservation(t, i, [d]))
        assignments[i] = [(tr, d)]
    triggers = build_triggers(observations, [tr], assignments, ZoneSpec("post", 150, 220, 330, 480),
                              464, 848, cfg=cfg, door=ZoneSpec("door", 0, 0, 170, 370))
    enter = [t for t in triggers if t.kind == "cashier_enter"]
    assert len(enter) == 1 and enter[0].t == 2


def test_outsider_policy_uses_dense_score_and_frame_count():
    scenes = states(seconds=2)
    tr = Trigger("outsider", 0, 2, facts={"support_times": [s.t for s in scenes],
                 "support_scores": {str(s.t): .6 for s in scenes}})
    cfg = RuleEngineConfig()
    cfg.types["OUTSIDER"].threshold = .8
    assert engine(scenes, cfg, [tr]) == []
    cfg.types["OUTSIDER"].threshold = .5
    assert engine(scenes, cfg, [tr])[0].event_code == "OUTSIDER"
    cfg.types["OUTSIDER"].enabled = False
    assert engine(scenes, cfg, [tr]) == []


def test_existing_seated_client_getting_up_is_not_a_new_entrant():
    cfg = TriggerConfig(seat_calibration_sec=2)
    cashier = PersonTrack(1, role="cashier")
    client = PersonTrack(2)
    observations, assignments = [], {}
    for i in range(81):
        t = i*.2
        staff = Detection(280, 300, 400, 510, .9)
        other = (Detection(200, 270, 290, 410, .9) if t < 12 else
                 Detection(60, 140, 170, 460, .9))
        cashier.history.append((t, i, staff))
        client.history.append((t, i, other))
        observations.append(FrameObservation(t, i, [staff, other]))
        assignments[i] = [(cashier, staff), (client, other)]
    triggers = build_triggers(observations, [cashier, client], assignments,
                              ZoneSpec("post", 260, 250, 430, 550), 464, 848, cfg=cfg)
    assert not any(t.kind in {"person_enter", "outsider"} for t in triggers)


def test_leave_is_separate_from_violation_and_keeps_onset_before_confirmation():
    cfg = TriggerConfig(seat_calibration_sec=2)
    cashier = PersonTrack(1, role="cashier")
    observations, assignments = [], {}
    for i in range(51):
        t = round(i*.2, 4)
        d = (Detection(220, 300, 340, 510, .9) if t < 4 else
             Detection(50, 160, 150, 450, .9) if t < 6 else None)
        if d is not None:
            cashier.history.append((t, i, d))
        observations.append(FrameObservation(t, i, [d] if d else []))
        assignments[i] = [(cashier, d)] if d else []
    triggers = build_triggers(observations, [cashier], assignments,
                              ZoneSpec("post", 200, 250, 400, 550), 464, 848, cfg=cfg)
    leaves = [t for t in triggers if t.kind == "cashier_leave"]
    assert len(leaves) == 1
    assert leaves[0].t == 4
    assert leaves[0].facts["confirmed_at"] > leaves[0].t
    assert evaluate_rules(leaves, states(seconds=10), {}, 10, RuleRegistry.load()) == []
