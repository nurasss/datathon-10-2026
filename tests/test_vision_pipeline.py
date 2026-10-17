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
def analyzer(project_root: Path) -> VideoAnalyzer:
    cfg = PipelineConfig.load(project_root / "configs" / "inference.default.json")
    return VideoAnalyzer(
        cfg=cfg,
        detector=ColorPersonDetector(),
        registry=RuleRegistry.load(),
        project_root=project_root,
    )


def test_cashier_leaves_while_client_waits(analyzer: VideoAnalyzer, violation_clip: Path) -> None:
    result = analyzer.analyze(violation_clip, options=AnalysisOptions(job_id="test"))
    assert result.verdict == "violation"
    assert result.events, "ляпис: событие не найдено"
    event = result.events[0]
    assert event.event_code == "EV01"
    # the cashier disappears at 6 s and returns at 10.5 s
    assert 4.5 <= event.peak_sec <= 8.5, f"таймкод {event.peak_sec} вне ожидаемого окна"
    frames = event.meta.get("evidence") or []
    assert 1 <= len(frames) <= 3
    for frame in frames:
        assert is_valid_jpeg(frame.jpeg_bytes)
        assert 0 <= frame.timestamp_sec <= result.meta.duration_sec + 0.1
    assert "231" in event.hypothesis
    assert "п. 22" in event.hypothesis or "пункт 22" in event.hypothesis


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
        assert event.event_label == rule.event_label
