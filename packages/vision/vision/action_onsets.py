"""Presentation timestamps from observed actions, never from labels.

Run ONLY after rule evaluation, merging, A/B precedence and output limiting.
Rule windows/thresholds stay untouched: retiming cannot create/drop an event.
"""
from __future__ import annotations

# Selected on frozen task7 replay, not on a new full evaluation. Other event
# types keep d6d80e5 timing. See artifacts/task8/timing_selection.json.
SELECTED_RETIME_CODES = frozenset({'LEAVE_SCREEN_UNLOCKED'})


def departure_onset(samples, boundary):
    """Latest seated->not-seated transition belonging to this departure.

    Sampling is the existing analysis grid; no interpolation or ground truth.
    If no seating was observed, use the first workplace exit after occupancy.
    """
    before = [s for s in samples if s['t'] <= boundary+1e-6]
    transitions = [(b['t'], a['t']) for a, b in zip(before, before[1:])
                   if a.get('cashier_seated') is True and b.get('cashier_seated') is False]
    if transitions:
        return transitions[-1][0], 'cashier_seated_true_to_false', transitions[-1][1]
    exits = [(b['t'], a['t']) for a, b in zip(before, before[1:])
             if a.get('cashier_at_workplace') is True and b.get('cashier_at_workplace') is False]
    if exits and not any(s.get('cashier_seated') is True for s in before):
        return exits[-1][0], 'workplace_exit_without_observed_seating', exits[-1][1]
    return boundary, 'no_observed_transition_keep_rule_boundary', None


def entry_onset(samples, track_id, boundary, door, min_score):
    """First actual observation of the confirmed entrant at the doorway.

    Role/authorization is determined upstream. Low-score recovery boxes and
    extrapolated Kalman positions are not new appearances.
    """
    history = [(s['t'], p) for s in samples if s['t'] <= boundary+1e-6
               for p in s.get('persons', []) if p['track_id'] == track_id and p['score'] >= min_score]
    for t, person in history:
        box = person['bbox']
        cx, cy = (box[0]+box[2])/2, (box[1]+box[3])/2
        if door is not None and door[0] <= cx <= door[2] and door[1] <= cy <= door[3]:
            previous = max((s['t'] for s in samples if s['t'] < t), default=None)
            return t, 'first_track_observation_at_door', previous
    if history:
        t = history[0][0]
        previous = max((s['t'] for s in samples if s['t'] < t), default=None)
        return t, 'first_track_observation_in_room', previous
    return boundary, 'no_observed_track_keep_rule_boundary', None


def retime_events(events, samples, duration, door=None, min_score=.3,
                  allowed_codes=SELECTED_RETIME_CODES):
    """Mutate only already accepted event timing/evidence, keeping provenance."""
    samples = sorted(samples, key=lambda s: s['t'])
    for event in events:
        if allowed_codes is not None and event.event_code not in allowed_codes:
            continue
        trigger = event.meta.get('trigger', {})
        kind = trigger.get('kind')
        boundary = trigger.get('t', event.start_sec)
        if event.meta.get('legacy') == 'EV01':
            kind = 'cashier_leave'
            boundary = event.meta['raw']['start']
        if kind == 'cashier_leave':
            boundary = trigger.get('facts', {}).get('boundary_crossed_sec', boundary)
            onset, source, previous = departure_onset(samples, boundary)
        elif kind in {'cashier_enter', 'outsider'} and trigger.get('track_id') is not None:
            onset, source, previous = entry_onset(samples, trigger['track_id'], boundary, door, min_score)
        else:
            continue
        old = event.peak_sec
        event.meta['action_onset'] = {
            'rule_start_sec': event.start_sec, 'rule_timecode_sec': old,
            'rule_end_sec': event.end_sec, 'action_boundary_sec': boundary,
            'onset_sec': onset, 'source': source, 'previous_observation_sec': previous,
            'sampling': 'existing_observed_frames_no_interpolation',
        }
        event.start_sec = event.peak_sec = round(max(0., onset), 4)
        event.end_sec = max(event.start_sec, event.end_sec)
        # Rewrite only the presentation prefix; support facts retain their times.
        prefix = f'С {old:.2f} с:'
        if event.observation.startswith(prefix):
            event.observation = f'С {event.start_sec:.2f} с:' + event.observation[len(prefix):]
        event.evidence_times = [round(max(0., event.start_sec-1.), 4), event.start_sec,
                                round(min(max(0., duration-.05), event.start_sec+1.5), 4)]
    return events
