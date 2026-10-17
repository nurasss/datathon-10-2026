from vision.config import RuleEngineConfig
from vision.scene_state import SceneState
from vision.triggers import Trigger
from vision.rule_engine import evaluate_rules
from rules.registry import RuleRegistry


def test_unknown_window_explains_rejection_without_new_violation():
    decisions = []
    scenes = [SceneState(i*.2, None, [], None, None, None) for i in range(40)]
    trigger = Trigger("cashier_leave", 2, 7, 17, {"exit_sec": 3})
    assert evaluate_rules([trigger], scenes, {}, 10, RuleRegistry.load(),
                          decisions=decisions, track_roles={17: "cashier"}) == []
    screen = next(d for d in decisions if d['rule'] == 'LEAVE_SCREEN_UNLOCKED')
    assert screen['reason'] == 'scene_unknown'
    assert screen['trigger'] == dict(kind='cashier_leave', start=2, end=7, track_id=17, role='cashier')
    assert screen['scene_window'] and screen['scene_window'][0]['screen_state'] is None
    assert any(d['reason'] == 'missing_trigger' and d['rule'] == 'DOOR_OPEN' for d in decisions)


def test_trace_does_not_change_decision():
    scenes = [SceneState(i*.2, 'document', [], None, None, None) for i in range(40)]
    args = ([Trigger('cashier_leave', 2, 7, 1)], scenes, {}, 10, RuleRegistry.load())
    plain = evaluate_rules(*args)
    trace = []
    traced = evaluate_rules(*args, decisions=trace)
    assert [(e.event_code,e.start_sec) for e in plain] == [(e.event_code,e.start_sec) for e in traced]
    assert any(d['reason'] == 'emitted' and d['rule'] == 'LEAVE_SCREEN_UNLOCKED' for d in trace)
