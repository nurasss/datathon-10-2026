"""SQLite persistence for jobs (private metadata; never exposed verbatim)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    status        TEXT NOT NULL,          -- queued | processing | done | failed
    phase         TEXT,
    progress      REAL DEFAULT 0,
    filename      TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,
    duration_sec  REAL,
    verdict       TEXT,
    events_json   TEXT,
    error_code    TEXT,
    error_message TEXT,
    meta_json     TEXT,
    started_at    REAL,
    finished_at   REAL,
    client_ip     TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
"""

TERMINAL = ("done", "failed")


@dataclass
class Job:
    job_id: str
    status: str
    phase: str | None
    progress: float
    filename: str | None
    size_bytes: int | None
    sha256: str | None
    duration_sec: float | None
    verdict: str | None
    events: list[dict[str, Any]]
    error_code: str | None
    error_message: str | None
    meta: dict[str, Any]
    created_at: float
    started_at: float | None
    finished_at: float | None

    @property
    def public_status(self) -> str:
        """Contract status: queued is reported as ``processing``."""

        return "processing" if self.status in ("queued", "processing") else self.status


class JobStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._bootstrap()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- writes ----------------------------------------------------------------------

    def create_job(self, filename: str, size_bytes: int, sha256: str, client_ip: str = "",
                   job_id: str | None = None) -> str:
        job_id = job_id or uuid.uuid4().hex[:12]
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, created_at, updated_at, status, phase, progress, "
                "filename, size_bytes, sha256, client_ip) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, now, now, "queued", "queued", 0.0, filename, size_bytes, sha256, client_ip),
            )
        return job_id

    def mark_processing(self, job_id: str) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='processing', phase='starting', progress=0.02, "
                "started_at=?, updated_at=? WHERE job_id=? AND status='queued'",
                (now, now, job_id),
            )

    def finish(self, job_id: str, payload: dict[str, Any], timeout_sec: float = 280.0) -> None:
        now = time.time()
        status = "done" if payload.get("ok") else "failed"
        events = payload.get("events") or []
        meta = {
            key: payload.get(key)
            for key in (
                "width",
                "height",
                "fps",
                "duration_sec",
                "frames_analyzed",
                "analysis_fps",
                "detector",
                "warnings",
                "quality",
                "timings",
                "elapsed_sec",
                "traceback",
            )
        }
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, phase='finished', progress=1.0, verdict=?, events_json=?, "
                "error_code=?, error_message=?, meta_json=?, updated_at=?, finished_at=?, "
                "duration_sec=COALESCE(?, duration_sec) WHERE job_id=? "
                "AND status='processing' AND created_at > ?",
                (
                    status,
                    payload.get("verdict") if status == "done" else None,
                    json.dumps(events, ensure_ascii=False) if status == "done" else None,
                    payload.get("error_code"),
                    payload.get("error_message"),
                    json.dumps(meta, ensure_ascii=False),
                    now,
                    now,
                    payload.get("duration_sec"),
                    job_id,
                    now - timeout_sec,
                ),
            )

    def fail(self, job_id: str, error_code: str, error_message: str) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed', phase='finished', error_code=?, error_message=?, "
                "updated_at=?, finished_at=? WHERE job_id=?",
                (error_code, error_message, now, now, job_id),
            )

    def expire(self, timeout_sec: float) -> int:
        """Wall-clock budget includes time spent waiting in the queue."""
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='failed', phase='finished', error_code='JOB_TIMEOUT', "
                "error_message='processing deadline exceeded', updated_at=?, finished_at=? "
                "WHERE status IN ('queued','processing') AND created_at <= ?",
                (now, now, now - timeout_sec),
            )
            return cur.rowcount or 0

    def fail_interrupted(self) -> None:
        """Do not extend deadlines or leave a crashed worker's jobs stuck after restart."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed', phase='finished', error_code='WORKER_CRASHED', "
                "updated_at=?, finished_at=? WHERE status='processing'",
                (time.time(), time.time()),
            )

    def purge_older_than(self, hours: float) -> list[str]:
        """Drop finished jobs (and return their ids so files can be removed)."""

        cutoff = time.time() - hours * 3600.0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE status IN ('done','failed') AND updated_at < ?",
                (cutoff,),
            ).fetchall()
            ids = [row["job_id"] for row in rows]
            if ids:
                conn.executemany("DELETE FROM jobs WHERE job_id = ?", [(i,) for i in ids])
        return ids

    # -- reads -----------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._to_job(row) if row else None

    def next_queued(self) -> Optional[Job]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return self._to_job(row) if row else None

    def queue_length(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','processing')"
            ).fetchone()
        return int(row["n"] if row else 0)

    def queue_position(self, job_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status='queued' AND created_at < "
                "(SELECT created_at FROM jobs WHERE job_id = ?)",
                (job_id,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def _to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"],
            status=row["status"],
            phase=row["phase"],
            progress=float(row["progress"] or 0.0),
            filename=row["filename"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            duration_sec=row["duration_sec"],
            verdict=row["verdict"],
            events=json.loads(row["events_json"]) if row["events_json"] else [],
            error_code=row["error_code"],
            error_message=row["error_message"],
            meta=json.loads(row["meta_json"]) if row["meta_json"] else {},
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
