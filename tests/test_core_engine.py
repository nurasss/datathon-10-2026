"""Action, scene contract, tracking, oracle and fail-closed rule regressions."""
from dataclasses import replace
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from rules.registry import RuleRegistry, TAXONOMY
from vision.config import PipelineConfig, RuleEngineConfig
from vision.pipeline import candidate_windows, VideoAnalyzer, AnalysisOptions
from vision.rule_engine import evaluate_rules
from vision.scene_state import DeskItem, SceneState, analyze_scene
from vision.tracking import PersonTracker
from vision.triggers import Trigger, build_triggers
from vision.types import Detection, FrameObservation, ZoneSpec


def scene(t, **kwargs):
    return replace(SceneState(t, None, [], None, None, None), **kwargs)


def run(triggers=(), scenes=(), presence=None):
    cfg = RuleEngineConfig()
    # Isolate semantic mapping/citations here; production persistence has its
    # own tests below with the default policies, not single-frame fixtures.
    for code, policy in cfg.types.items():
        if code != "DOOR_OPEN":
            policy.min_frames, policy.min_sec = 1, 0.0
    return evaluate_rules(list(triggers), list(scenes), presence or {}, 30, RuleRegistry.load(), cfg)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/api/docs", "/api/redoc", "/api/openapi.json"])
def test_docs_are_404_by_default(path):
    from app.main import app
    assert TestClient(app).get(path).status_code == 404


def test_worker_default_two():
    from app.config import Settings
    assert Settings().worker_concurrency == 2


@pytest.mark.parametrize("raw,enabled", [("0", False), ("false", False), ("1", True), (" true ", True), ("on", True)])
def test_docs_flag_is_explicit(monkeypatch, raw, enabled):
    from app.main import _docs_enabled
    monkeypatch.setenv("FV_ENABLE_DOCS", raw)
    assert _docs_enabled() is enabled


def test_unknown_scene_is_not_negative_or_violation():
    states = [scene(4+i*.2) for i in range(30)]
    assert run([Trigger("cashier_leave", 4, 8, 1)], states) == []
    assert run([Trigger("cashier_enter", 4, 8, 1)], states,
               {round(s.t, 4): {"cashier_present": True, "cashier_seated": True} for s in states}) == []


@pytest.mark.parametrize("changes,code", [
    ({"screen_state": "document"}, "LEAVE_SCREEN_UNLOCKED"),
    ({"safe_open": 1.0}, "SAFE_OPEN"),
    ({"desk_items": [DeskItem("cash", (0, 0, 1, 1), .9)]}, "LEAVE_VALUABLES"),
])
def test_leave_types_use_onset_and_exact_norm(changes, code):
    events = run([Trigger("cashier_leave", 6, 12, 7)], [scene(6, **changes)], {6: {"cashier_present": False}})
    assert [e.event_code for e in events] == [code]
    event = events[0]
    assert event.start_sec == event.peak_sec == 6
    assert event.evidence_times == [5, 6, 7.5]
    ref = RuleRegistry.load().for_event(code)
    assert event.rule_id == TAXONOMY[code][1]
    assert ref.quoted_text in event.hypothesis
    assert ref.act_number in event.hypothesis
    assert all(s in event.hypothesis for s in ref.cannot_infer)


@pytest.mark.parametrize("screen", [None, "locked", "off", "closed", "unknown"])
def test_ordinary_departure_never_ev01(screen):
    assert run([Trigger("cashier_leave", 6, 12, 7)], [scene(6, screen_state=screen)]) == []


def test_no_stale_scene_at_leave():
    assert run([Trigger("cashier_leave", 10, 12, 7)], [scene(5, safe_open=1.0)]) == []


def test_door_hold_unknown_and_closed_reset():
    tr = [Trigger("cashier_enter", 3, 20, 1)]
    states = [scene(round(3+i*.2, 4), door_open=1.0) for i in range(21)]
    events = run(tr, states)
    assert [e.event_code for e in events] == ["DOOR_OPEN"]
    assert events[0].peak_sec == 3
    assert run(tr, [replace(s, door_open=0.0) for s in states]) == []
    assert run(tr, [replace(s, door_open=None) for s in states]) == []
    assert run(tr, [replace(s, door_open=None) if s.t == 5 else s for s in states]) == []
    assert run(tr, [replace(s, door_open=0.0) if s.t <= 3.4 else s for s in states]) == []


@pytest.mark.parametrize("item,code", [("cup", "PERSONAL_ITEMS"), ("food", "PERSONAL_ITEMS"),
                                      ("clothing_or_bag", "PERSONAL_ITEMS"), ("phone", "PHONE_USE")])
def test_desk_scene_requires_cashier_and_persistence(item, code):
    states = [scene(i*.2, desk_items=[DeskItem(item, (0, 0, 1, 1), 1)]) for i in range(6)]
    assert run(scenes=states) == []
    p = {round(s.t, 4): {"cashier_present": True} for s in states}
    assert [e.event_code for e in run(scenes=states, presence=p)] == [code]


def test_laptop_is_not_a_personal_item():
    states = [scene(i*.2, desk_items=[DeskItem("laptop", (0, 0, 1, 1), 1)]) for i in range(6)]
    assert run(scenes=states, presence={round(s.t, 4): {"cashier_present": True} for s in states}) == []


def test_manual_count_requires_seated_cashier():
    states = [scene(i*.2, cashier_hand_counting=1.0) for i in range(6)]
    assert run(scenes=states, presence={round(s.t, 4): {"cashier_seated": False} for s in states}) == []
    assert run(scenes=states, presence={round(s.t, 4): {"cashier_seated": True} for s in states})[0].event_code == "MANUAL_COUNT"


def test_outsider_has_approved_isolation_caveat():
    event = run([Trigger("outsider", 18, 22, 3, {"person_count": 3, "support_times": [18]})], [scene(18)])[0]
    assert event.rule_id == "KZ-NB14-P16-1"
    assert "служебные полномочия" in event.hypothesis.lower()
    assert event.peak_sec == 18


def test_unapproved_registry_cannot_emit():
    registry = RuleRegistry.load()
    tr = Trigger("outsider", 2, 5, facts={"support_times": [2, 2.2, 2.4]})
    scenes = [scene(t) for t in [2, 2.2, 2.4]]
    assert evaluate_rules([tr], scenes, {}, 20, registry)
    registry._by_id["KZ-NB14-P16-1"].status = "draft"
    assert evaluate_rules([tr], scenes, {}, 20, registry) == []


def test_kalman_keeps_id_across_motion_dropout_and_low_score():
    tracker = PersonTracker()
    for i in range(15):
        dets = [] if i in {5, 6} else [Detection(10+i*3, 20, 60+i*3, 120, .2 if i == 8 else .9)]
        tracker.update(FrameObservation(i*.2, i, dets))
    assert len(tracker.tracks) == 1
    assert len(tracker.tracks[0].history) == 13


def test_low_score_cannot_start_a_person_track():
    tracker = PersonTracker()
    tracker.update(FrameObservation(0, 0, [Detection(0, 0, 30, 60, .2)]))
    assert tracker.tracks == []


def test_low_score_giant_box_cannot_hide_high_score_people():
    tracker = PersonTracker()
    tracker.update(FrameObservation(0, 0, [Detection(0, 0, 400, 600, .2), Detection(100, 200, 180, 400, .9),
                                           Detection(220, 200, 300, 400, .8)]))
    assert len(tracker.tracks) == 2


def test_candidate_windows_are_bounded_and_merge():
    cfg = PipelineConfig().sampling
    post = ZoneSpec("post", 0, 0, 100, 100)
    observations = [FrameObservation(t, i, [Detection(10, 10, 40, 80, .9)] if t < 10 else [])
                    for i, t in enumerate(range(20))]
    windows = candidate_windows(observations, post, 20, cfg)
    assert windows[0] == [0, 4]
    assert windows[1][0] <= 10 <= windows[1][1]
    assert windows[1][1] <= 15


def test_all_coco_classes_reach_scene_in_normalized_form(compliant_clip, project_root, monkeypatch):
    from support import ColorPersonDetector
    class Objects(ColorPersonDetector):
        def detect_all(self, frame, min_score):
            return self.detect(frame) + [Detection(20, 20, 40, 40, .9, "cat"), Detection(50, 20, 60, 40, .9, "cup")]
    captured = []
    def analyze(frames, timestamps, detections, context=None):
        captured.extend(detections)
        return [scene(t) for t in timestamps]
    monkeypatch.setattr("vision.scene_state.analyze_scene", analyze)
    res = VideoAnalyzer(detector=Objects(), project_root=project_root).analyze(compliant_clip, AnalysisOptions(keep_trace=True))
    assert captured and {"cat", "cup", "person"} <= {d["cls"] for d in captured[0]}
    assert all(0 <= v <= 1 for frame in captured for d in frame for v in d["box"])
    assert res.quality["trace"][0]["persons"][0]["track_id"]


def test_oracle_is_explicit_engine_only():
    from vision.oracle import oracle_inputs
    rows = [{"type_code": "SCREEN_NOT_LOCKED", "start_sec": "4", "end_sec": "12"}]
    triggers, states, presence, unsupported = oracle_inputs(rows, 20)
    assert not unsupported
    assert run(triggers, states, presence)[0].event_code == "LEAVE_SCREEN_UNLOCKED"
    assert triggers[0].facts["source"] == "oracle_annotation"


def test_three_people_trigger_starts_at_entry_not_midpoint():
    from vision.tracking import PersonTrack
    tracks = [PersonTrack(i, role="cashier" if i == 1 else "entrant", entered_at=2.0 if i == 3 else None) for i in (1, 2, 3)]
    observations, assignments = [], {}
    boxes = [Detection(100, 250, 200, 550, .9), Detection(260, 200, 350, 500, .9), Detection(10, 200, 90, 500, .9)]
    for i in range(6):
        t = 2+i*.2
        observations.append(FrameObservation(t, i, boxes))
        assignments[i] = list(zip(tracks, boxes))
    post = ZoneSpec("post", 0, 150, 400, 600)
    for track, box in zip(tracks, boxes):
        track.history = [(obs.t, obs.frame_index, box) for obs in observations]
    events = build_triggers(observations, tracks, assignments, post, 464, 848)
    assert events[0].kind == "outsider"
    assert events[0].t == 2.0


def test_cashier_leave_requires_room_exit_not_merely_standing():
    from vision.tracking import PersonTrack
    post = ZoneSpec("post", 180, 220, 340, 400)
    tr = PersonTrack(1, role="cashier")
    for i in range(15):
        d = Detection(200, 240, 300, 390, .9) if i < 10 else Detection(70, 100, 170, 410, .9)
        tr.history.append((i*.2, i, d))
    observations = [FrameObservation(i*.2, i, []) for i in range(30)]
    triggers = build_triggers(observations, [tr], {}, post, 464, 848)
    assert [t.kind for t in triggers] == ["cashier_leave"]
    assert triggers[0].t == 2.0
    assert not build_triggers(observations[:15], [tr], {}, post, 464, 848)
