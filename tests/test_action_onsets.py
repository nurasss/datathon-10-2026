from vision.action_onsets import departure_onset, entry_onset, retime_events
from vision.rule_engine import make_event
from rules.registry import RuleRegistry


def test_departure_is_first_false_not_confirmation_or_midpoint():
    samples = [{'t': t, 'cashier_seated': t < 4, 'cashier_at_workplace': t < 7} for t in range(12)]
    assert departure_onset(samples, 9) == (4, 'cashier_seated_true_to_false', 3)


def test_reseating_starts_an_independent_action():
    samples = [{'t': t, 'cashier_seated': t in {0, 1, 5, 6}} for t in range(10)]
    assert departure_onset(samples, 9)[0] == 7


def test_no_observed_seating_uses_workplace_exit():
    samples = [{'t': t, 'cashier_seated': False, 'cashier_at_workplace': t < 3} for t in range(8)]
    assert departure_onset(samples, 6) == (3, 'workplace_exit_without_observed_seating', 2)


def test_entry_uses_first_door_appearance_without_requiring_stability():
    samples = [{'t': t, 'persons': [{'track_id': 7, 'score': .8, 'bbox': box}]}
               for t, box in [(1, (.6,.1,.8,.5)), (2, (.1,.1,.3,.5)), (3, (.2,.2,.4,.6))]]
    assert entry_onset(samples, 7, 3, (0,0,.4,.5), .3) == (2, 'first_track_observation_at_door', 1)


def test_output_retime_does_not_change_support_or_event_membership():
    trigger = {'kind': 'cashier_leave', 't': 8, 'end': 12, 'track_id': 1, 'facts': {}}
    event = make_event('LEAVE_SCREEN_UNLOCKED', 8, 12, 'fact', RuleRegistry.load(), 15,
                       {'trigger': trigger, 'support_times': [8,8.2,8.4]})
    events = [event]
    samples = [{'t': t, 'cashier_seated': t < 4} for t in range(13)]
    retime_events(events, samples, 15)
    assert events == [event] and event.peak_sec == event.start_sec == 4
    assert event.evidence_times == [3,4,5.5]
    assert event.meta['trigger'] == trigger and event.meta['support_times'] == [8,8.2,8.4]
    assert event.meta['action_onset']['rule_timecode_sec'] == 8
    assert event.observation.startswith('С 4.00 с:')


def test_selected_policy_keeps_all_other_types_at_production_timestamps():
    trigger = {'kind': 'cashier_leave', 't': 8, 'end': 12, 'track_id': 1, 'facts': {}}
    events = [make_event(code, 8, 12, 'fact', RuleRegistry.load(), 15,
                        {'trigger':trigger,'support_times':[8,8.2,8.4]})
              for code in ('LEAVE_VALUABLES','SAFE_OPEN','OUTSIDER','DOOR_OPEN','LEAVE_DURING_SERVICE')]
    before = [(e.start_sec,e.peak_sec,e.end_sec,list(e.evidence_times),dict(e.meta)) for e in events]
    retime_events(events,[{'t':t,'cashier_seated':t<4} for t in range(13)],15)
    assert before == [(e.start_sec,e.peak_sec,e.end_sec,e.evidence_times,e.meta) for e in events]
