"""Queue manager: runs one analysis at a time in an isolated child process."""

from __future__ import annotations

import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from dataclasses import replace

from .config import Settings
from .db import Job, JobStore
from .results import done_response

log = logging.getLogger("fortevision.worker")


class JobManager:
    """Single-slot worker with a hard per-job deadline.

    The public contract promises an answer within five minutes; a job that
    exceeds its budget is terminated and reported as ``failed`` — never as a
    fabricated ``no_violation``.
    """

    def __init__(self, settings: Settings, store: JobStore):
        self.settings = settings
        self.store = store
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._watchdog: threading.Thread | None = None
        self._active_jobs: set[str] = set()
        self._lock = threading.Lock()
        self._last_purge = 0.0
        self.total_processed = 0
        self.last_error: str | None = None

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        if self._threads and any(t.is_alive() for t in self._threads):
            return
        self._stop.clear()
        self.store.fail_interrupted()
        self.store.expire(self.settings.job_timeout_sec)
        self._watchdog = threading.Thread(target=self._watch_deadlines, name="fv-watchdog", daemon=True)
        self._watchdog.start()
        concurrency = getattr(self.settings, "worker_concurrency", 1)
        self._threads = []
        for i in range(concurrency):
            t = threading.Thread(target=self._loop, name=f"fv-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5.0)
        self._threads.clear()
        if self._watchdog:
            self._watchdog.join(timeout=2.0)

    def _watch_deadlines(self) -> None:
        while not self._stop.is_set():
            try:
                self.store.expire(self.settings.job_timeout_sec)
            except Exception:
                log.exception("watchdog database error")
            self._stop.wait(0.1)

    @property
    def current_job(self) -> str | None:
        with self._lock:
            return next(iter(self._active_jobs)) if self._active_jobs else None

    # -- main loop -------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.store.expire(self.settings.job_timeout_sec)
                job = self.store.claim_next_queued()
            except Exception as exc:  # noqa: BLE001 - DB hiccups must not kill the worker
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("queue poll failed")
                job = None
            if job is None:
                self._maybe_purge()
                self._stop.wait(self.settings.worker_poll_sec)
                continue
            try:
                with self._lock:
                    self._active_jobs.add(job.job_id)
                self._run_job(job)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("job %s crashed in the worker", job.job_id)
                self.store.fail(job.job_id, "WORKER_ERROR", f"{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._active_jobs.discard(job.job_id)
                self.total_processed += 1

    # -- one job ---------------------------------------------------------------------

    def _run_job(self, job: Job) -> None:
        self.store.expire(self.settings.job_timeout_sec)
        video_path = self.settings.uploads_dir / f"{job.job_id}.mov"
        if not video_path.is_file():
            self.store.fail(job.job_id, "UPLOAD_MISSING", "загруженный файл не найден")
            return
        report_path = self.settings.reports_dir / f"{job.job_id}.json"
        if report_path.exists():
            report_path.unlink()
        evidence_dir = self.settings.evidence_dir / job.job_id
        if evidence_dir.exists():
            shutil.rmtree(evidence_dir, ignore_errors=True)
        log_path = self.settings.reports_dir / f"{job.job_id}.log"

        cmd = [
            sys.executable,
            "-m",
            "app.job_entry",
            "--job-id", job.job_id,
            "--video", str(video_path),
            "--out", str(report_path),
            "--evidence-dir", str(evidence_dir),
            "--db", str(self.settings.db_path),
            "--config", str(self.settings.inference_config),
            "--project-root", str(self.settings.project_root),
        ]
        env = self._child_env()
        started = time.time()
        with open(log_path, "wb") as log_file:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(self.settings.project_root / "apps" / "api"),
                env=env,
                start_new_session=True,
            )
        try:
            while True:
                if time.time() - job.created_at >= self.settings.job_timeout_sec:
                    self.store.fail(
                        job.job_id,
                        "JOB_TIMEOUT",
                        f"превышен лимит обработки {self.settings.job_timeout_sec:.0f} с",
                    )
                    log.warning("job %s timed out", job.job_id)
                    return
                if self._stop.is_set():
                    self.store.fail(job.job_id, "WORKER_STOPPED", "сервер останавливается")
                    return
                if proc.poll() is not None:
                    break
                self._stop.wait(0.1)
        finally:
            if proc.poll() is None:
                self._terminate(proc)

        if proc.returncode != 0 or not report_path.is_file():
            self.store.fail(
                job.job_id,
                "WORKER_CRASHED",
                self._tail(log_path) or "процесс анализа завершился без отчёта",
            )
            return
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.store.fail(job.job_id, "REPORT_UNREADABLE", "отчёт анализа повреждён")
            return
        if payload.get("ok"):
            try:
                candidate = replace(job, status="done", verdict=payload.get("verdict"),
                                    events=payload["events"], duration_sec=payload["duration_sec"])
                done_response(candidate, self.settings.evidence_dir)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.store.fail(job.job_id, "INVALID_RESULT", str(exc)[:400])
                return
        self.store.expire(self.settings.job_timeout_sec)
        self.store.finish(job.job_id, payload, self.settings.job_timeout_sec)
        log.info(
            "job %s finished: ok=%s verdict=%s in %.1fs",
            job.job_id,
            payload.get("ok"),
            payload.get("verdict"),
            time.time() - started,
        )

    def _child_env(self) -> dict[str, str]:
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        root = str(self.settings.project_root)
        extra_paths = [f"{root}/apps/api", f"{root}/packages/vision", f"{root}/packages/rules"]
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = ":".join(extra_paths + ([existing] if existing else []))
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[var] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=1)
        except ProcessLookupError:
            pass

    @staticmethod
    def _tail(path: Path, limit: int = 400) -> str:
        try:
            data = path.read_bytes()[-limit:]
            return data.decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def _maybe_purge(self) -> None:
        now = time.time()
        if now - self._last_purge < 1800:
            return
        self._last_purge = now
        try:
            removed = self.store.purge_older_than(self.settings.retention_hours)
        except Exception:  # noqa: BLE001
            log.exception("purge failed")
            return
        for job_id in removed:
            for path in (
                self.settings.uploads_dir / f"{job_id}.mov",
                self.settings.reports_dir / f"{job_id}.json",
                self.settings.reports_dir / f"{job_id}.log",
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(self.settings.evidence_dir / job_id, ignore_errors=True)
        if removed:
            log.info("purged %d expired job(s)", len(removed))
