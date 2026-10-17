"""ForteVision API + web UI.

Public contract:
    POST /api/v1/analyze        multipart ``file`` + ``X-API-Key`` -> 202 job_id
    GET  /api/v1/jobs/{job_id}  -> status in {processing, done, failed}

The browser UI is separate: optional FV_UI_KEY + signed cookie, never the jury key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.config
import time
import uuid
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import ui
from .config import Settings, load_settings
from .db import Job, JobStore
from .schemas import (
    AnalyzeAccepted,
    FailedJob,
    FailedJobDetails,
    HealthResponse,
    JobDetailsResponse,
    JobResponse,
    ProcessingJob,
    ProcessingJobDetails,
)
from .results import done_details_response, done_response
from .security import SESSION_COOKIE, check_api_key, create_session, verify_session
from .worker import JobManager
from .middleware import UploadGuard

VERSION = "1.0.0"
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "default"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
logging.config.dictConfig(LOGGING_CONFIG)
log = logging.getLogger("fortevision.api")

settings: Settings = load_settings()
store = JobStore(settings.db_path)
manager = JobManager(settings, store)
STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup()
    try:
        yield
    finally:
        _shutdown()


app = FastAPI(
    title="ForteVision — признаки нарушений кассовой дисциплины",
    version=VERSION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(UploadGuard, settings=settings)


# --------------------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------------------


def _startup() -> None:
    settings.ensure_dirs()
    if not settings.api_keys:
        log.warning(
            "FV_API_KEYS is empty: public API endpoints are closed. "
            "Set FV_API_KEYS to enable them."
        )
    if settings.worker_enabled:
        manager.start()
        log.info("worker started (data_dir=%s)", settings.data_dir)
    else:
        log.warning("worker disabled by configuration (FV_WORKER_ENABLED=false)")


def _shutdown() -> None:
    manager.stop()


# --------------------------------------------------------------------------------------
# auth helpers
# --------------------------------------------------------------------------------------


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> str:
    key_id = check_api_key(x_api_key, settings.api_keys)
    if key_id is None:
        raise HTTPException(status_code=401, detail="Неверный или отсутствующий X-API-Key")
    return key_id


def current_session(request: Request) -> Optional[str]:
    if not settings.ui_key:
        return "ui"
    return verify_session(settings.secret, request.cookies.get(SESSION_COOKIE))


def require_session(request: Request) -> str:
    key_id = current_session(request)
    if key_id is None:
        raise HTTPException(status_code=401, detail="Требуется вход в веб-интерфейс")
    return key_id


# --------------------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------------------


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=VERSION,
        queue_length=store.queue_length(),
        worker_busy=manager.current_job is not None,
        uptime_sec=round(time.time() - STARTED_AT, 1),
    )


@app.post("/api/v1/analyze", response_model=AnalyzeAccepted, status_code=202)
async def analyze(
    request: Request,
    file: UploadFile = File(..., description="Видеофайл .mov"),
    _key: str = Depends(require_api_key),
) -> AnalyzeAccepted:
    filename = file.filename or "upload.mov"
    suffix = Path(filename).suffix.lower()
    if suffix != ".mov":
        raise HTTPException(status_code=415, detail="Ожидается файл .mov")
    if store.queue_length() >= settings.max_queue:
        raise HTTPException(
            status_code=429,
            detail="Очередь обработки заполнена, повторите запрос позже",
            headers={"Retry-After": "30"},
        )

    job_id = uuid.uuid4().hex[:12]
    target = settings.uploads_dir / f"{job_id}.mov"
    size = 0
    digest = hashlib.sha256()
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл превышает {settings.max_upload_bytes // (1024 * 1024)} МБ",
                    )
                out.write(chunk)
                digest.update(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Не удалось сохранить файл") from exc
    finally:
        await file.close()

    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Пустой файл")

    # Publish only a completely written upload. Probing/decoding is child-process work.
    if store.queue_length() >= settings.max_queue:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=429, detail="Очередь обработки заполнена")
    store.create_job(filename, size, digest.hexdigest(), client_ip=_client_ip(request), job_id=job_id)

    return AnalyzeAccepted(job_id=job_id)


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, _key: str = Depends(require_api_key)) -> JobResponse:
    store.expire(settings.job_timeout_sec)
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return _job_response(job)


@app.get("/api/v1/jobs/{job_id}/details", response_model=JobDetailsResponse)
def get_job_details(job_id: str, _key: str = Depends(require_api_key)) -> JobDetailsResponse:
    store.expire(settings.job_timeout_sec)
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return _job_details_response(job)


@app.get("/api/v1/jobs/{job_id}/video")
def get_job_video(job_id: str, _key: str = Depends(require_api_key)) -> Response:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    path = settings.uploads_dir / f"{job_id}.mov"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл недоступен")
    return FileResponse(
        path,
        media_type="video/quicktime",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
    )



@app.get("/api/v1/rules")
def rules() -> dict:
    registry = ui.load_registry()
    return {
        "registry_version": registry.version,
        "verified_on": registry.verified_on,
        "policy": ui.REGISTRY_POLICY,
        "rules": registry.as_public_list(),
    }


@app.get("/api/v1/stats")
def stats(_key: str = Depends(require_api_key)) -> dict:
    return {
        "jobs": store.stats(),
        "queue_length": store.queue_length(),
        "worker_busy": manager.current_job is not None,
        "processed": manager.total_processed,
        "last_error": manager.last_error,
    }


def _job_response(job: Job) -> JobResponse:
    if job.status == "done":
        try:
            return done_response(job, settings.evidence_dir)
        except (ValueError, KeyError, TypeError, OSError):
            log.exception("job %s has invalid result/evidence", job.job_id)
            store.fail(job.job_id, "INVALID_RESULT", "результат или evidence повреждены")
            return FailedJob(job_id=job.job_id, status="failed", error="Некорректный результат анализа (INVALID_RESULT)")
    if job.status == "failed":
        return FailedJob(
            job_id=job.job_id,
            status="failed",
            error=_safe_error(job),
        )
    return ProcessingJob(job_id=job.job_id, status="processing")


def _job_details_response(job: Job) -> JobDetailsResponse:
    if job.status == "done":
        try:
            registry = ui.load_registry()
            return done_details_response(job, settings.evidence_dir, registry)
        except (ValueError, KeyError, TypeError, OSError):
            log.exception("job %s has invalid result/evidence", job.job_id)
            store.fail(job.job_id, "INVALID_RESULT", "результат или evidence повреждены")
            return FailedJobDetails(
                job_id=job.job_id,
                status="failed",
                error="Некорректный результат анализа (INVALID_RESULT)",
                video_duration_sec=job.duration_sec,
            )
    if job.status == "failed":
        return FailedJobDetails(
            job_id=job.job_id,
            status="failed",
            error=_safe_error(job),
            video_duration_sec=job.duration_sec,
        )
    return ProcessingJobDetails(
        job_id=job.job_id,
        status="processing",
        video_duration_sec=job.duration_sec,
    )



def _safe_error(job: Job) -> str:
    mapping = {
        "PROBE_FAILED": "файл не распознан как видео",
        "UNSUPPORTED_VIDEO": "видеодорожка не декодируется",
        "DECODE_FAILED": "не удалось декодировать кадры",
        "JOB_TIMEOUT": "превышен лимит времени обработки",
        "WORKER_CRASHED": "процесс анализа завершился аварийно",
        "OUT_OF_MEMORY": "недостаточно памяти для обработки",
        "EMPTY_FILE": "пустой файл",
        "UPLOAD_REJECTED": "файл отклонён",
    }
    base = mapping.get(job.error_code or "", "техническая ошибка обработки")
    return f"{base} ({job.error_code})" if job.error_code else base


# --------------------------------------------------------------------------------------
# web UI
# --------------------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    key_id = current_session(request)
    return HTMLResponse(ui.page_index(settings, key_id is not None, store, manager))


@app.post("/ui/login")
def ui_login(api_key: str = Form(...)) -> Response:
    key_id = check_api_key(api_key, [settings.ui_key] if settings.ui_key else [])
    if key_id is None:
        return HTMLResponse(ui.page_login_error(), status_code=401)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session(settings.secret, key_id),
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )
    return response


@app.get("/ui/logout")
def ui_logout() -> Response:
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.post("/ui/analyze")
async def ui_analyze(
    request: Request,
    file: UploadFile = File(...),
) -> Response:
    if current_session(request) is None:
        return RedirectResponse(url="/", status_code=303)
    try:
        accepted = await analyze(request, file=file, _key="ui")
    except HTTPException as exc:
        return HTMLResponse(ui.page_error(str(exc.detail), exec_status(exc)), status_code=exec_status(exc))
    return RedirectResponse(url=f"/ui/jobs/{accepted.job_id}", status_code=303)


def exec_status(exc: HTTPException) -> int:
    return int(exc.status_code or 400)


@app.get("/ui/jobs/{job_id}", response_class=HTMLResponse)
def ui_job(job_id: str, request: Request) -> HTMLResponse:
    if current_session(request) is None:
        return HTMLResponse(ui.page_login_required(), status_code=401)
    store.expire(settings.job_timeout_sec)
    job = store.get(job_id)
    if job is None:
        return HTMLResponse(ui.page_not_found(job_id), status_code=404)
    _job_response(job)
    return HTMLResponse(ui.page_job(settings, store, manager, store.get(job_id)))


@app.get("/ui/jobs/{job_id}/json", response_class=JSONResponse)
def ui_job_json(job_id: str, request: Request) -> JSONResponse:
    if current_session(request) is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    store.expire(settings.job_timeout_sec)
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return JSONResponse(json.loads(_job_response(job).model_dump_json(exclude_none=True)))


@app.get("/ui/jobs/{job_id}/video")
def ui_job_video(job_id: str, request: Request) -> Response:
    if current_session(request) is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    path = settings.uploads_dir / f"{job_id}.mov"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл недоступен")
    return FileResponse(path, media_type="video/quicktime", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/ui/evidence/{job_id}/{name}")
def ui_evidence(job_id: str, name: str, request: Request) -> Response:
    if current_session(request) is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    safe = Path(name).name
    path = settings.evidence_dir / job_id / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Кадр недоступен")
    return Response(content=path.read_bytes(), media_type="image/jpeg")


@app.get("/ui/rules", response_class=HTMLResponse)
def ui_rules() -> HTMLResponse:
    return HTMLResponse(ui.page_rules())


@app.get("/ui/health", response_class=HTMLResponse)
def ui_health() -> HTMLResponse:
    return HTMLResponse(ui.page_health(settings, store, manager, VERSION, STARTED_AT))


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
