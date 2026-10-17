"""Acceptance contract and fail-closed behaviour, using local videos only."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app import main
from app.db import JobStore
from app.schemas import DoneJob, JobResponse
from app.worker import JobManager
from support import ColorPersonDetector, make_clip
from vision.config import PipelineConfig
from vision.job_runner import _serialise_event
from vision.pipeline import VideoAnalyzer

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
def small_mov(tmp_path_factory):
    return make_clip(tmp_path_factory.mktemp("acceptance") / "small.mov", duration=1, fps=5)


def upload(api, path, headers=AUTH, filename=None):
    with path.open("rb") as f:
        return api.post("/api/v1/analyze", headers=headers,
                        files={"file": (filename or path.name, f, "video/quicktime")})


def wait_terminal(api, job_id, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = api.get(f"/api/v1/jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        TypeAdapter(JobResponse).validate_python(body)
        if body["status"] != "processing":
            return body
        time.sleep(0.05)
    pytest.fail("Worker did not finish")


def test_at01_accepts_mov_immediately(api, small_mov):
    result = upload(api, small_mov)
    assert result.status_code == 202
    assert set(result.json()) == {"job_id"}
    job_id = result.json()["job_id"]
    assert main.store.get(job_id).size_bytes == small_mov.stat().st_size
    assert api.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json() == {
        "job_id": job_id, "status": "processing",
    }


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
@pytest.mark.parametrize("method,path", [("post", "/api/v1/analyze"), ("get", "/api/v1/jobs/id")])
def test_at02_auth_even_before_multipart_parsing(api, headers, method, path):
    assert api.request(method, path, headers=headers).status_code == 401


def test_at03_unknown_id(api):
    assert api.get("/api/v1/jobs/unknown", headers=AUTH).status_code == 404


@pytest.mark.parametrize("filename", ["clip.txt", "clip.mp4", "clip.qt", "clip"])
def test_at04_rejects_non_mov(api, small_mov, filename):
    assert upload(api, small_mov, filename=filename).status_code == 415
    assert main.store.queue_length() == 0


def test_real_child_pipeline_done(api, small_mov):
    main.manager.start()
    job_id = upload(api, small_mov).json()["job_id"]
    body = wait_terminal(api, job_id)
    assert body["status"] == "done", body
    DoneJob.model_validate(body)
    assert set(body) == {"job_id", "status", "verdict", "violations"}


def test_broken_mov_is_failed_never_no_violation(api):
    main.manager.start()
    response = api.post("/api/v1/analyze", headers=AUTH,
                        files={"file": ("broken.mov", b"not a video", "video/quicktime")})
    assert response.status_code == 202
    body = wait_terminal(api, response.json()["job_id"])
    assert body["status"] == "failed"
    assert "verdict" not in body


def test_empty_mov_rejected(api):
    response = api.post("/api/v1/analyze", headers=AUTH,
                        files={"file": ("empty.mov", b"", "video/quicktime")})
    assert response.status_code == 422
    assert main.store.queue_length() == 0


def test_truncated_container_never_yields_no_violation(api, small_mov, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to construct a faststart corrupted fixture")
    faststart = tmp_path / "faststart.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(small_mov), "-c", "copy",
                    "-movflags", "+faststart", str(faststart)], check=True)
    data = faststart.read_bytes()
    faststart.write_bytes(data[:int(len(data) * 0.8)])
    main.manager.start()
    response = upload(api, faststart)
    assert response.status_code == 202
    body = wait_terminal(api, response.json()["job_id"])
    assert body["status"] == "failed"
    assert "verdict" not in body


def test_input_validation_opencv_fallback(small_mov, monkeypatch):
    from app import job_entry
    original = shutil.which
    monkeypatch.setattr(job_entry.shutil, "which", lambda name: None if name == "ffmpeg" else original(name))
    job_entry.validate_input(str(small_mov))


def test_upload_limit_file_bytes(api, monkeypatch):
    monkeypatch.setattr(main.settings, "max_upload_bytes", 100)
    response = api.post("/api/v1/analyze", headers=AUTH,
                        files={"file": ("big.mov", b"x" * 101)})
    assert response.status_code == 413
    assert main.store.queue_length() == 0
    assert list(main.settings.uploads_dir.iterdir()) == []


def test_upload_limit_before_parsing(api):
    response = api.post("/api/v1/analyze", headers={**AUTH, "Content-Length": str(101 * 1024**2)},
                        content=b"x")
    assert response.status_code == 413


def test_chunked_upload_limit_without_content_length(api, monkeypatch):
    monkeypatch.setattr(main.settings, "max_upload_bytes", 100)
    def chunks():
        yield b"--abc\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.mov\"\r\n\r\n"
        yield b"x" * 70000
        yield b"\r\n--abc--\r\n"
    response = api.post("/api/v1/analyze", headers={**AUTH, "Content-Type": "multipart/form-data; boundary=abc"},
                        content=chunks())
    assert response.status_code == 413


@pytest.fixture
def evidence_job(api, violation_clip, project_root):
    result = VideoAnalyzer(cfg=PipelineConfig.load(project_root / "configs/inference.default.json"),
                           detector=ColorPersonDetector(), project_root=project_root).analyze(violation_clip)
    assert result.events
    job_id = main.store.create_job(violation_clip.name, violation_clip.stat().st_size, "")
    folder = main.settings.evidence_dir / job_id
    folder.mkdir()
    events = [_serialise_event(job_id, i, event, folder) for i, event in enumerate(result.events)]
    main.store.mark_processing(job_id)
    main.store.finish(job_id, {"ok": True, "verdict": result.verdict,
                               "duration_sec": result.meta.duration_sec, "events": events})
    return job_id, result


def test_done_schema_and_real_jpeg(api, evidence_job):
    job_id, result = evidence_job
    response = api.get(f"/api/v1/jobs/{job_id}", headers=AUTH)
    body = response.json()
    DoneJob.model_validate(body)
    assert body["verdict"] == "violation"
    original_frames = result.events[0].meta["evidence"]
    for frame, original in zip(body["violations"][0]["evidence_frames"], original_frames, strict=True):
        jpeg = base64.b64decode(frame["image_base64"], validate=True)
        assert jpeg == original.jpeg_bytes  # Actual frame extracted from the input clip, not a placeholder.
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
        assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) is not None
        assert 0 <= frame["timestamp_sec"] <= result.meta.duration_sec


@pytest.mark.parametrize("damage", ["missing", "out_of_bounds", "invalid_jpeg", "empty"])
def test_invalid_evidence_never_becomes_no_violation(api, evidence_job, damage):
    job_id, _ = evidence_job
    job = main.store.get(job_id)
    frame = job.events[0]["evidence"][0]
    if damage == "missing":
        from pathlib import Path
        Path(frame["path"]).unlink()
    elif damage == "invalid_jpeg":
        from pathlib import Path
        Path(frame["path"]).write_bytes(b"\xff\xd8invalid\xff\xd9")
    else:
        if damage == "out_of_bounds":
            frame["timestamp_sec"] = job.duration_sec + 10
        else:
            job.events[0]["evidence"] = []
        with main.store._connect() as conn:
            conn.execute("UPDATE jobs SET events_json=? WHERE job_id=?", (json.dumps(job.events), job_id))
    body = api.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json()
    assert body["status"] == "failed"
    assert "verdict" not in body


@pytest.mark.parametrize("verdict,violations", [("violation", []), ("unknown", []), ("no_violation", [{}])])
def test_invalid_verdict_invariant(verdict, violations):
    with pytest.raises(ValidationError):
        DoneJob(job_id="id", status="done", verdict=verdict, violations=violations)


def test_no_extra_fields_in_done():
    with pytest.raises(ValidationError):
        DoneJob(job_id="id", status="done", verdict="no_violation", violations=[], progress=1)


@pytest.mark.parametrize("missing", ["job_id", "status", "verdict", "violations"])
def test_done_requires_every_contract_field(missing):
    body = {"job_id": "id", "status": "done", "verdict": "no_violation", "violations": []}
    body.pop(missing)
    with pytest.raises(ValidationError):
        DoneJob.model_validate(body)


def test_nonterminal_envelopes_have_no_extra_required_fields():
    for status in ("processing", "failed"):
        TypeAdapter(JobResponse).validate_python({"job_id": "id", "status": status})


def test_watchdog_expires_queue_and_processing(api, monkeypatch):
    monkeypatch.setattr(main.settings, "job_timeout_sec", 0.2)
    main.manager.start()
    queued = main.store.create_job("queued.mov", 0, "")
    running = main.store.create_job("processing.mov", 0, "")
    with main.store._connect() as conn:
        conn.execute("UPDATE jobs SET created_at=?, status='processing'", (time.time() - 1,))
        conn.execute("UPDATE jobs SET status='queued' WHERE job_id=?", (queued,))
    time.sleep(0.3)
    assert main.store.get(queued).status == "failed"
    assert main.store.get(running).status == "failed"


def test_get_expires_even_without_worker(api):
    job_id = main.store.create_job("clip.mov", 0, "")
    with main.store._connect() as conn:
        conn.execute("UPDATE jobs SET created_at=? WHERE job_id=?", (time.time() - 281, job_id))
    assert api.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json()["status"] == "failed"


def test_late_result_cannot_overwrite_timeout(api):
    job_id = main.store.create_job("clip.mov", 0, "")
    main.store.mark_processing(job_id)
    main.store.fail(job_id, "JOB_TIMEOUT", "timeout")
    main.store.finish(job_id, {"ok": True, "verdict": "no_violation", "events": [], "duration_sec": 1})
    assert main.store.get(job_id).status == "failed"


def test_hung_child_is_killed(api, small_mov, monkeypatch):
    monkeypatch.setattr(main.settings, "job_timeout_sec", 0.3)
    original_popen = subprocess.Popen
    children = []
    def hanging_child(*args, **kwargs):
        proc = original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(proc)
        return proc
    monkeypatch.setattr("app.worker.subprocess.Popen", hanging_child)
    main.manager.start()
    job_id = upload(api, small_mov).json()["job_id"]
    assert wait_terminal(api, job_id)["status"] == "failed"
    main.manager.stop()
    assert children and children[0].poll() is not None


def test_restart_fails_interrupted_job(api):
    job_id = main.store.create_job("clip.mov", 0, "")
    main.store.mark_processing(job_id)
    main.manager.start()
    assert main.store.get(job_id).status == "failed"


def test_ui_no_jury_key_and_separate_login(api, small_mov, monkeypatch):
    page = api.get("/").text
    assert KEY not in page
    response = api.post("/ui/analyze", files={"file": ("small.mov", small_mov.read_bytes())},
                        follow_redirects=False)
    assert response.status_code == 303
    assert api.get(response.headers["location"]).status_code == 200
    monkeypatch.setattr(main.settings, "ui_key", "separate-ui-key")
    assert api.post("/ui/login", data={"api_key": KEY}, follow_redirects=False).status_code == 401
    assert api.get(response.headers["location"]).status_code == 401
    assert api.post("/ui/login", data={"api_key": "separate-ui-key"}, follow_redirects=False).status_code == 303
    assert api.get(response.headers["location"]).status_code == 200


def test_ui_video_range_for_seeking(api, small_mov):
    response = api.post("/ui/analyze", files={"file": ("small.mov", small_mov.read_bytes())},
                        follow_redirects=False)
    video = api.get(response.headers["location"] + "/video", headers={"Range": "bytes=0-99"})
    assert video.status_code == 206
    assert len(video.content) == 100


def test_ui_violation_has_frames_seek_and_normative_source(api, evidence_job):
    job_id, _ = evidence_job
    page = api.get(f"/ui/jobs/{job_id}").text
    assert "Обнаружены признаки возможных нарушений" in page
    assert "class=seek data-t=" in page
    assert "<video id=player" in page
    assert "p.currentTime=t" in page
    assert "/ui/evidence/" in page
    assert "https://adilet.zan.kz/rus/docs/V1900019680" in page
    assert KEY not in page


def test_ui_cookie_never_authenticates_jury_api(api, monkeypatch):
    monkeypatch.setattr(main.settings, "ui_key", "ui-only-key")
    assert api.post("/ui/login", data={"api_key": "ui-only-key"}, follow_redirects=False).status_code == 303
    assert api.get("/api/v1/jobs/unknown").status_code == 401
