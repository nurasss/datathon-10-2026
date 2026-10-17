"""Pipeline behaviour on synthetic clips (deterministic, no private dataset)."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import ColorPersonDetector

from vision.config import PipelineConfig
from vision.evidence import is_valid_jpeg
from vision.pipeline import AnalysisOptions, VideoAnalyzer
from rules.registry import RuleRegistry


@pytest.fixture()
def analyzer(project_root: Path, monkeypatch) -> VideoAnalyzer:
    from vision.scene_state import SceneState
    monkeypatch.setattr("vision.scene_state.analyze_scene", lambda frames, timestamps, detections, context=None: [
        SceneState(t, None, [], None, None, None) for t in timestamps])
    cfg = PipelineConfig.load(project_root / "configs" / "inference.default.json")
    return VideoAnalyzer(
        cfg=cfg,
        detector=ColorPersonDetector(),
        registry=RuleRegistry.load(),
        project_root=project_root,
    )


def test_departure_without_scene_evidence_is_not_a_violation(analyzer: VideoAnalyzer, violation_clip: Path) -> None:
    # Layer A remains fail-closed. The curator-authorized B layer is tested
    # separately: departure plus a client no longer requires a scene verdict.
    analyzer.cfg.hybrid.enabled = False
    result = analyzer.analyze(violation_clip, options=AnalysisOptions(job_id="test"))
    assert result.verdict == "no_violation"
    assert result.events == []


def test_hybrid_does_not_add_detector_calls(analyzer: VideoAnalyzer, violation_clip: Path):
    import json
    analyzer.cfg.hybrid.enabled = False
    a = analyzer.analyze(violation_clip, AnalysisOptions(keep_trace=True))
    analyzer.cfg.hybrid.enabled = True
    b = analyzer.analyze(violation_clip, AnalysisOptions(keep_trace=True))
    assert a.quality['inference_calls'] == b.quality['inference_calls']
    assert b.quality['hybrid']['detector_calls_added'] == 0
    assert any(e.meta.get('layer') == 'B' for e in b.events)
    assert json.dumps(b.quality)


def test_positive_scene_produces_three_real_evidence_frames(analyzer, compliant_clip, monkeypatch):
    from vision.scene_state import DeskItem, SceneState
    monkeypatch.setattr("vision.scene_state.analyze_scene", lambda frames, timestamps, detections, context=None: [
        SceneState(t, None, [DeskItem("cup", (0.3, 0.3, 0.4, 0.4), 1.0)], None, None, None) for t in timestamps])
    result = analyzer.analyze(compliant_clip, AnalysisOptions(keep_trace=True))
    assert result.events[0].event_code == "PERSONAL_ITEMS"
    event = result.events[0]
    frames = event.meta["evidence"]
    assert len(frames) == 3
    assert all(is_valid_jpeg(f.jpeg_bytes) for f in frames)
    assert event.peak_sec == event.start_sec
    assert "пункт 12, часть первая" in event.hypothesis


def test_no_false_positive_when_cashier_stays(analyzer: VideoAnalyzer, compliant_clip: Path) -> None:
    result = analyzer.analyze(compliant_clip, options=AnalysisOptions(job_id="test"))
    assert result.verdict == "no_violation"
    assert result.events == []


def test_empty_workplace_is_not_an_event(analyzer: VideoAnalyzer, idle_clip: Path) -> None:
    result = analyzer.analyze(idle_clip, options=AnalysisOptions(job_id="test"))
    assert result.verdict == "no_violation"


def test_events_never_use_unapproved_rules(analyzer: VideoAnalyzer, violation_clip: Path) -> None:
    registry = RuleRegistry.load()
    result = analyzer.analyze(violation_clip, options=AnalysisOptions(job_id="test"))
    for event in result.events:
        rule = registry.get(event.rule_id)
        assert rule is not None and rule.approved, f"{event.rule_id} не утверждён"
        assert event.event_label == registry.for_event(event.event_code).event_label
