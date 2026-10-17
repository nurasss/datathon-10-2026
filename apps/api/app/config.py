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
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("FV_DATA_DIR", "data")).resolve())
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
    ui_key: str = field(default_factory=lambda: os.environ.get("FV_UI_KEY", ""))
    #: secret used to sign the browser session cookie
    secret: str = field(default_factory=lambda: os.environ.get("FV_SECRET") or secrets.token_hex(16))
    max_upload_bytes: int = min(100, max(1, _int("FV_MAX_UPLOAD_MB", 100))) * 1024 * 1024
    max_queue: int = _int("FV_MAX_QUEUE", 8)
    job_timeout_sec: float = min(280.0, max(0.1, _float("FV_JOB_TIMEOUT_SEC", 280.0)))
    retention_hours: float = _float("FV_RETENTION_HOURS", 72.0)
    public_base_url: str = os.environ.get("FV_PUBLIC_BASE_URL", "")
    service_name: str = os.environ.get("FV_SERVICE_NAME", "ForteVision")
    #: in-process worker (single job at a time by default — matches the 2 vCPU target box)
    worker_enabled: bool = _bool("FV_WORKER_ENABLED", True)
    worker_concurrency: int = max(1, min(8, _int("FV_WORKER_CONCURRENCY", 1)))
    worker_poll_sec: float = _float("FV_WORKER_POLL_SEC", 1.0)

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
