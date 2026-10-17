"""Application settings (environment driven; no secrets in the repository)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """Runtime configuration of the service."""

    #: directory that holds the SQLite DB, uploads and evidence frames
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("FV_DATA_DIR", "data")))
    #: project root (defaults to the repository root two levels above this file)
    project_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("FV_PROJECT_ROOT", Path(__file__).resolve().parents[3])
        )
    )
    inference_config: str = os.environ.get(
        "FV_INFERENCE_CONFIG",
        str(Path(__file__).resolve().parents[3] / "configs" / "inference.default.json"),
    )
    #: comma separated list of accepted API keys
    api_keys: list[str] = field(
        default_factory=lambda: [
            k.strip() for k in os.environ.get("FV_API_KEYS", "").split(",") if k.strip()
        ]
    )
    #: secret used to sign the browser session cookie
    secret: str = field(default_factory=lambda: os.environ.get("FV_SECRET") or secrets.token_hex(16))
    max_upload_bytes: int = _int("FV_MAX_UPLOAD_MB", 512) * 1024 * 1024
    max_queue: int = _int("FV_MAX_QUEUE", 8)
    job_timeout_sec: float = _float("FV_JOB_TIMEOUT_SEC", 280.0)
    max_duration_sec: float = _float("FV_MAX_DURATION_SEC", 900.0)
    retention_hours: float = _float("FV_RETENTION_HOURS", 72.0)
    public_base_url: str = os.environ.get("FV_PUBLIC_BASE_URL", "")
    service_name: str = os.environ.get("FV_SERVICE_NAME", "ForteVision")
    require_api_key_for_ui: bool = _bool("FV_UI_REQUIRES_KEY", True)
    #: in-process worker (single job at a time — matches the 2 vCPU target box)
    worker_enabled: bool = _bool("FV_WORKER_ENABLED", True)
    worker_poll_sec: float = _float("FV_WORKER_POLL_SEC", 1.0)
    #: hard safety valve: never start more than N analyses in parallel
    worker_concurrency: int = _int("FV_WORKER_CONCURRENCY", 1)

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.evidence_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
