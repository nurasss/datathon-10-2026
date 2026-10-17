"""Queue manager: runs one analysis at a time in an isolated child process."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import Settings
from .db import Job, JobStore

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
        self._thread: threading.Thread | None = None
        self._current: str | None = None
        self._lock = threading.Lock()
        self._last_purge = 0.0
        self.total_processed = 0
        self.last_error: str | None = None

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        requeued = self.store.requeue_stale(older_than_sec=self.settings.job_timeout_sec + 60)
        if requeued:
            log.warning("requeued %d stale job(s) after restart", requeued)
        self._thread = threading.Thread(target=self._loop, name="fv-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    @property
    def current_job(self) -> str | None:
        with self._lock:
            return self._current

    # -- main loop -------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.store.next_queued()
            except Exception as exc:  # noqa: BLE001 - DB hiccups must not kill the worker
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("queue poll failed")
                job = None
            if job is None:
                self._maybe_purge()
                self._stop.wait(self.settings.worker_poll_sec)
                continue
            try:
                self._run_job(job)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("job %s crashed in the worker", job.job_id)
                self.store.fail(job.job_id, "WORKER_ERROR", f"{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._current = None
                self.total_processed += 1

    # -- one job ---------------------------------------------------------------------

    def _run_job(self, job: Job) -> None:
        with self._lock:
            self._current = job.job_id
        video_path = self.settings.uploads_dir / f"{job.job_id}.mov"
        if not video_path.is_file():
            self.store.fail(job.job_id, "UPLOAD_MISSING", "загруженный файл не найден")
            return
        self.store.mark_processing(job.job_id)
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
            )
        try:
            while True:
                if proc.poll() is not None:
                    break
                if time.time() - started > self.settings.job_timeout_sec:
                    self._terminate(proc)
                    self.store.fail(
                        job.job_id,
                        "JOB_TIMEOUT",
                        f"превышен лимит обработки {self.settings.job_timeout_sec:.0f} с",
                    )
                    log.warning("job %s timed out", job.job_id)
                    return
                time.sleep(0.4)
        finally:
            if proc.poll() is None:
                self._terminate(proc)

        if not report_path.is_file():
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
        self.store.finish(job.job_id, payload)
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
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:  # noqa: BLE001
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
