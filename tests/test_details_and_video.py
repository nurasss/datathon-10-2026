"""Tests for extended /details and range-streaming /video endpoints."""

from __future__ import annotations

import base64
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import JobStore
from app.worker import JobManager
from support import make_clip

KEY = "test-only-api-key"
AUTH = {"X-API-Key": KEY}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "api_keys", [KEY])
    monkeypatch.setattr(main.settings, "ui_key", "")
    monkeypatch.setattr(main.settings, "worker_enabled", False)
    monkeypatch.setattr(main.settings, "worker_poll_sec", 0.05)
    main.settings.ensure_dirs()
    db = JobStore(main.settings.db_path)
    monkeypatch.setattr(main, "store", db)
    monkeypatch.setattr(main, "manager", JobManager(main.settings, db))
    with TestClient(main.app) as client:
        yield client
    main.manager.stop()


@pytest.fixture(scope="session")
def sample_mov(tmp_path_factory):
    return make_clip(tmp_path_factory.mktemp("details_test") / "sample.mov", duration=2, fps=5)


def _create_jpeg(path: Path) -> None:
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.tobytes())


def test_details_auth_and_not_found(api):
    # Missing key
    r = api.get("/api/v1/jobs/nonexistent/details")
    assert r.status_code == 401

    # Invalid key
    r = api.get("/api/v1/jobs/nonexistent/details", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401

    # Not found
    r = api.get("/api/v1/jobs/nonexistent/details", headers=AUTH)
    assert r.status_code == 404


def test_details_processing_and_failed(api):
    # Processing job
    main.store.create_job("test.mov", 1000, "sha", job_id="job_proc")
    r = api.get("/api/v1/jobs/job_proc/details", headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == "job_proc"
    assert data["status"] == "processing"
    assert data["video_duration_sec"] is None

    # Failed job
    main.store.create_job("test.mov", 1000, "sha", job_id="job_fail")
    main.store.fail("job_fail", "JOB_TIMEOUT", "timed out")
    r = api.get("/api/v1/jobs/job_fail/details", headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == "job_fail"
    assert data["status"] == "failed"
    assert "JOB_TIMEOUT" in data["error"]


def test_details_done_with_violations(api):
    job_id = "job_done_viol"
    main.store.create_job("test.mov", 5000, "sha", job_id=job_id)
    main.store.mark_processing(job_id)

    ev_path = main.settings.evidence_dir / job_id / "frame0.jpg"
    _create_jpeg(ev_path)

    events = [
        {
            "event_id": f"{job_id}-e1",
            "type": "Предполагаемое оставление рабочего места",
            "event_code": "EV01",
            "timecode_sec": 5.2,
            "description": "Рабочее место не занято",
            "rule_id": "KZ-NB231-P22",
            "evidence": [{"timestamp_sec": 5.0, "path": str(ev_path)}],
        }
    ]

    main.store.finish(
        job_id=job_id,
        payload={
            "ok": True,
            "verdict": "violation",
            "duration_sec": 12.5,
            "events": events,
        },
    )

    # Standard /jobs endpoint
    r_std = api.get(f"/api/v1/jobs/{job_id}", headers=AUTH)
    assert r_std.status_code == 200
    std_data = r_std.json()
    assert "video_duration_sec" not in std_data
    assert "rule" not in std_data["violations"][0]

    # Extended /details endpoint
    r = api.get(f"/api/v1/jobs/{job_id}/details", headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == job_id
    assert data["status"] == "done"
    assert data["verdict"] == "violation"
    assert data["video_duration_sec"] == 12.5
    assert len(data["violations"]) == 1

    viol = data["violations"][0]
    assert viol["type"] == "Предполагаемое оставление рабочего места"
    assert viol["timecode_sec"] == 5.2
    assert viol["description"] == "Рабочее место не занято"
    assert len(viol["evidence_frames"]) == 1

    rule = viol["rule"]
    assert rule["act_number"].startswith("Постановление Правления")
    assert rule["paragraph"] == "пункт 22"
    assert "Кассовому работнику не допускается покидать рабочее место" in rule["quote"]
    assert "adilet.zan.kz" in rule["source_url"]
    assert len(rule["act_name"]) > 10


def test_details_done_no_violation(api):
    job_id = "job_done_clean"
    main.store.create_job("test.mov", 5000, "sha", job_id=job_id)
    main.store.mark_processing(job_id)
    main.store.finish(
        job_id=job_id,
        payload={
            "ok": True,
            "verdict": "no_violation",
            "duration_sec": 20.0,
            "events": [],
        },
    )

    r = api.get(f"/api/v1/jobs/{job_id}/details", headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done"
    assert data["verdict"] == "no_violation"
    assert data["violations"] == []
    assert data["video_duration_sec"] == 20.0



def test_video_endpoint_and_range_scrubbing(api, sample_mov):
    job_id = "job_video_test"
    main.store.create_job("sample.mov", sample_mov.stat().st_size, "sha", job_id=job_id)

    # Missing file returns 404
    r = api.get(f"/api/v1/jobs/{job_id}/video", headers=AUTH)
    assert r.status_code == 404

    # Copy clip to uploads dir
    dest = main.settings.uploads_dir / f"{job_id}.mov"
    dest.write_bytes(sample_mov.read_bytes())
    full_bytes = dest.read_bytes()
    total_size = len(full_bytes)
    assert total_size > 0

    # Auth check
    r_unauth = api.get(f"/api/v1/jobs/{job_id}/video")
    assert r_unauth.status_code == 401

    # Full video GET
    r_full = api.get(f"/api/v1/jobs/{job_id}/video", headers=AUTH)
    assert r_full.status_code == 200
    assert r_full.headers["accept-ranges"] == "bytes"
    assert r_full.headers["content-type"].startswith("video/quicktime")
    assert len(r_full.content) == total_size
    assert r_full.content == full_bytes

    # Range request for scrubbing (first 50 bytes)
    r_part1 = api.get(
        f"/api/v1/jobs/{job_id}/video",
        headers={**AUTH, "Range": "bytes=0-49"},
    )
    assert r_part1.status_code == 206
    assert r_part1.headers["content-range"] == f"bytes 0-49/{total_size}"
    assert len(r_part1.content) == 50
    assert r_part1.content == full_bytes[:50]

    # Range request mid-stream (bytes 50-99)
    r_part2 = api.get(
        f"/api/v1/jobs/{job_id}/video",
        headers={**AUTH, "Range": "bytes=50-99"},
    )
    assert r_part2.status_code == 206
    assert r_part2.headers["content-range"] == f"bytes 50-99/{total_size}"
    assert len(r_part2.content) == 50
    assert r_part2.content == full_bytes[50:100]
