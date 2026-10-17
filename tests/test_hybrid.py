from vision.config import PipelineConfig
from vision.hybrid import combine_layers, lockscreen_at_departure, baseline_states
from vision.rule_engine import make_event
from vision.scene_state import SceneState
from rules.registry import RuleRegistry


def config():
    cfg = PipelineConfig()
    cfg.hybrid.enabled = True
    return cfg


RAW1 = dict(start=4., peak=4., end=9., confidence=.7, variant="service_in_progress",
            absence_duration=5, client_ref=2, client_overlap=4)
RAW2 = dict(start=2., peak=4., end=9., confidence=.6, persist_sec=7, bbox=[1,2,3,4])


def scenes(state, start=0, end=10):
    return [SceneState(round(i*.2,4), state, [], None, None, None) for i in range(int(start*5),int(end*5)+1)]


def test_unknown_scene_does_not_block_authorized_baseline_service():
    out = combine_layers([], [RAW1], [], scenes(None), 10, RuleRegistry.load(), config())
    assert out[0].event_code == "LEAVE_DURING_SERVICE"
    assert out[0].rule_id == "KZ-NB231-P22" and out[0].peak_sec == 4
    assert out[0].meta["layer"] == "B" and out[0].evidence_times == [3,4,5.5]


def test_stable_lock_at_departure_suppresses_only_ev01():
    cfg = config()
    assert combine_layers([], [RAW1], [], scenes('lockscreen'), 10, RuleRegistry.load(), cfg) == []
    out = combine_layers([], [RAW1], [RAW2], scenes('lockscreen'), 10, RuleRegistry.load(), cfg)
    assert [e.event_code for e in out] == ['LEAVE_VALUABLES']
    assert 'не устанавливает' in out[0].observation


def test_late_or_short_lock_does_not_suppress():
    cfg = config()
    assert lockscreen_at_departure(scenes('lockscreen',5,10), 4, cfg.hybrid) is None
    assert lockscreen_at_departure(scenes('lockscreen',3.2,4.8), 4, cfg.hybrid) is None
    assert lockscreen_at_departure(scenes('lockscreen',3,5), 4, cfg.hybrid)


def test_client_arrival_to_unattended_post_uses_compound_onset():
    raw = dict(RAW1,start=1,peak=4,variant='client_arrived_to_unattended_post')
    out = combine_layers([], [raw], [], scenes(None), 10, RuleRegistry.load(), config())
    assert out[0].start_sec == out[0].peak_sec == 4


def test_a_has_priority_per_overlap_not_per_video():
    cfg = config()
    reg = RuleRegistry.load()
    a = make_event('OUTSIDER',3,7,'test',reg,20)
    late = dict(RAW1,start=12,peak=12,end=17)
    out = combine_layers([a], [RAW1,late], [RAW2], scenes(None), 20, reg, cfg)
    assert a in out
    assert [(e.event_code,e.start_sec) for e in out] == [('OUTSIDER',3),('LEAVE_DURING_SERVICE',12)]


def test_earlier_a_does_not_block_later_unattended_object_window():
    cfg, reg = config(), RuleRegistry.load()
    a = make_event('OUTSIDER',3,7,'test',reg,20)
    raw = dict(RAW2,start=1,peak=12,end=17)
    out = combine_layers([a], [], [raw], scenes(None), 20, reg, cfg)
    assert [(e.event_code,e.start_sec) for e in out] == [('OUTSIDER',3),('LEAVE_VALUABLES',12)]


def test_no_approved_rule_no_fallback():
    reg = RuleRegistry.load()
    reg.get('KZ-NB231-P22').status = 'draft'
    assert combine_layers([], [RAW1], [], scenes(None), 10, reg, config()) == []


def test_only_lockscreen_is_an_added_baseline_suppression():
    assert combine_layers([], [RAW1], [], scenes('off'), 10, RuleRegistry.load(), config())


def test_explicit_type_disable_applies_to_hybrid_too():
    cfg = config()
    cfg.rules.policy('LEAVE_DURING_SERVICE').enabled = False
    assert combine_layers([], [RAW1], [], scenes(None), 10, RuleRegistry.load(), cfg) == []


def test_b_cannot_displace_a_at_output_limit():
    cfg = config()
    cfg.max_events = 1
    reg = RuleRegistry.load()
    a = make_event('OUTSIDER',12,17,'test',reg,20)
    assert combine_layers([a], [RAW1], [RAW2], scenes(None), 20, reg, cfg) == [a]


def test_original_baseline_duplicate_preference_is_retained_and_explained():
    decisions = []
    out = combine_layers([], [RAW1], [RAW2], scenes(None), 10, RuleRegistry.load(), config(), decisions)
    assert [e.event_code for e in out] == ['LEAVE_DURING_SERVICE']
    assert next(d for d in decisions if d['legacy']=='EV02')['reason'] == 'legacy_episode_duplicate_or_output_limit'


def test_invalid_grid_or_short_lockscreen_guard_rejected():
    import pytest
    with pytest.raises(ValueError):
        PipelineConfig.from_dict({'hybrid':{'input_grid':'unrecognized'}})
    with pytest.raises(ValueError):
        PipelineConfig.from_dict({'hybrid':{'locked_hold_sec':1}})


def test_state_adapter_matches_legacy_flags_with_given_track_assignments():
    import json
    from dataclasses import asdict
    import numpy as np
    from vision.events import build_states
    from vision.types import FrameObservation, Detection, ZoneSpec
    from vision.tracking import PersonTrack
    worker, client = PersonTrack(1), PersonTrack(2)
    observations, assigned = [], {}
    for i in range(8):
        d = Detection(20,20,40,60,.9)
        c = Detection(130,20,150,60,.9)
        people = [(worker,d),(client,c)] if i<4 else [(client,c)]
        observations.append(FrameObservation(float(i),i,[det for _,det in people]))
        assigned[i] = people
    post, counter = ZoneSpec('post',np.float64(0),0,100,100), ZoneSpec('counter',0,0,200,100)
    actual = baseline_states(observations,assigned,post,counter,1.)
    original = build_states(observations,post,counter,1.)
    assert [(s.post_occupied,s.client_present,s.persons_in_post) for s in actual] == [
        (s.post_occupied,s.client_present,s.persons_in_post) for s in original]
    assert json.dumps([asdict(s) for s in actual])
