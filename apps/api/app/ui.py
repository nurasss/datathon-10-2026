"""Server-rendered web UI (no build step, no external assets, no CDN)."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from rules.registry import RuleRegistry

from .config import Settings
from .db import Job, JobStore
from .worker import JobManager

PROJECT_ROOT = Path(__file__).resolve().parents[3]

REGISTRY_POLICY = (
    "В боевую выдачу допускаются только правила со статусом approved: текст пункта скопирован "
    "из публикации и сверен с двумя независимыми источниками. Ссылки и номера пунктов не "
    "генерируются моделью."
)

BASE_CSS = """
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1e222b;--line:#2a2f3a;--text:#e8eaf0;--muted:#9aa3b2;
--accent:#2f7cf6;--ok:#1f9d63;--bad:#d94848;--warn:#d9a441}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:#7fb2ff}
.wrap{max-width:1080px;margin:0 auto;padding:24px}
header.top{border-bottom:1px solid var(--line);background:#12141a}
header.top .wrap{display:flex;align-items:center;gap:16px;padding:14px 24px}
.logo{font-weight:700;letter-spacing:.2px}
.logo span{color:var(--accent)}
nav{margin-left:auto;display:flex;gap:14px;font-size:14px}
h1{font-size:26px;margin:18px 0 6px}
h2{font-size:19px;margin:26px 0 10px}
h3{font-size:16px;margin:18px 0 6px}
p.lead{color:var(--muted);max-width:70ch}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin:14px 0}
.grid{display:grid;gap:14px}
.grid.two{grid-template-columns:1fr 1fr}
@media(max-width:820px){.grid.two{grid-template-columns:1fr}}
.banner{border-radius:12px;padding:16px 18px;margin:16px 0;border:1px solid var(--line);background:var(--panel2)}
.banner.ok{border-color:#1f9d63;background:#122a1f}
.banner.bad{border-color:#d94848;background:#2a1517}
.banner.wait{border-color:#3a4250}
.banner.fail{border-color:#8a6d1f;background:#2a2413}
.banner .verdict{font-size:21px;font-weight:700;margin:0 0 4px}
.badge{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.badge.ok{color:#6ee7a8;border-color:#1f6b47}
.badge.bad{color:#ff9a9a;border-color:#7a2b2b}
button,input[type=submit]{background:var(--accent);color:#fff;border:0;border-radius:9px;padding:10px 16px;font-size:15px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
input[type=file],input[type=text],input[type=password]{background:#0d1017;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:10px;width:100%}
form.stack{display:grid;gap:10px;max-width:560px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:13px}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
pre{background:#0d1017;border:1px solid var(--line);border-radius:9px;padding:12px;overflow:auto}
pre.json{max-height:420px}
.muted{color:var(--muted)}
.small{font-size:13px}
.evidence{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 0}
.evidence figure{margin:0;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#0d1017}
.evidence img{display:block;width:240px;height:auto}
.evidence figcaption{font-size:12px;color:var(--muted);padding:4px 8px}
.bar{height:8px;background:#0d1017;border:1px solid var(--line);border-radius:999px;overflow:hidden;margin:10px 0 4px}
.bar>i{display:block;height:100%;background:var(--accent)}
video{width:100%;max-height:520px;background:#000;border-radius:10px;border:1px solid var(--line)}
.tag{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
ul.limits{margin:8px 0 0 18px;padding:0}
ul.limits li{margin:4px 0}
footer{border-top:1px solid var(--line);margin-top:34px;padding:18px 0 40px;color:var(--muted);font-size:13px}
.row{display:flex;gap:14px;align-items:flex-start}
.row .left{flex:1}
.kv{display:grid;grid-template-columns:200px 1fr;gap:4px 12px;font-size:14px}
.kv div:nth-child(odd){color:var(--muted)}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def load_registry() -> RuleRegistry:
    return RuleRegistry.load(PROJECT_ROOT / "packages" / "rules" / "rules" / "registry.json")


def _head(title: str) -> str:
    return (
        "<!doctype html><html lang=ru><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{BASE_CSS}</style></head><body>"
    )


def _header(authenticated: bool) -> str:
    nav = (
        "<a href='/'>Загрузка</a><a href='/ui/rules'>Нормативная база</a>"
        "<a href='/ui/health'>Статус</a><a href='/api/docs'>API</a>"
    )
    if authenticated:
        nav += "<a href='/ui/logout'>Выйти</a>"
    return (
        "<header class=top><div class=wrap style='padding:14px 24px'>"
        "<div class=logo>Forte<span>Vision</span></div>"
        f"<nav>{nav}</nav></div></header>"
    )


def _footer() -> str:
    return (
        "<footer><div class=wrap>Сервис выявляет <b>визуальные признаки</b> возможных нарушений "
        "кассовой дисциплины и сопоставляет их с открытыми нормативными актами РК. "
        "Вывод не является юридическим заключением; окончательное решение принимает человек. "
        "Обработка полностью локальная: видео не передаётся во внешние AI-сервисы, "
        "лица не распознаются, звук не анализируется.</div></footer>"
    )


# -- pages ------------------------------------------------------------------------------


def page_index(settings: Settings, authenticated: bool, store: JobStore, manager: JobManager) -> str:
    registry = load_registry()
    approved = registry.approved_rules()
    queue = store.queue_length()
    if not authenticated and settings.ui_key:
        form = (
            "<div class=card><h3>Вход в интерфейс</h3>"
            "<p class=small class=muted>Введите отдельный ключ веб-интерфейса (FV_UI_KEY). Ключ проверяется "
            "на сервере и в браузере не сохраняется (используется подписанная сессия).</p>"
            "<form class=stack method=post action='/ui/login'>"
            "<input type=password name=api_key placeholder='Ключ интерфейса' required>"
            "<button type=submit>Войти</button></form></div>"
        )
        upload = ""
    else:
        upload = (
            "<div class=card><h3>Проверить видеозапись</h3>"
            "<form class=stack method=post action='/ui/analyze' enctype='multipart/form-data'>"
            "<input type=file name=file accept='video/quicktime,.mov' required>"
            "<button type=submit>Отправить на анализ</button>"
            "<span class=small class=muted>Один кассовый пост, верхний или боковой ракурс, "
            "размер до 100 МБ. Лимит обработки, включая очередь: 280 секунд.</span>"
            "</form></div>"
        )
        form = ""
    rules_list = "".join(
        f"<li><b>{_esc(rule.event_label)}</b> — {_esc(rule.act_number)}; п. {_esc(rule.paragraph)} "
        f"(<span class='tag'>{_esc(rule.status)}</span>)</li>"
        for rule in approved
    )
    return (
        _head("ForteVision — анализ кассовых видеозаписей")
        + _header(authenticated)
        + "<div class=wrap>"
        + "<h1>Признаки нарушений кассовой дисциплины по видеозаписи</h1>"
        + "<p class=lead>Загрузите .mov-запись кассовой зоны. Сервис разберёт её локально: "
        "найдёт видимые события, определит время, сохранит реальные кадры-доказательства и "
        "сопоставит их с проверенными пунктами нормативных актов РК.</p>"
        + form
        + upload
        + "<div class=grid two>"
        + "<div class=card><h3>Что сервис делает</h3><ul class=limits>"
        "<li>декодирует видео, считает наблюдения по кадрам;</li>"
        "<li>определяет рабочую зону и клиента по визуальным признакам;</li>"
        "<li>применяет темпоральную логику: важна длительность и последовательность, а не один кадр;</li>"
        "<li>выдаёт вердикт по ролику и события с таймкодом и 1–3 кадрами;</li>"
        "<li>ссылается только на утверждённые (approved) пункты реестра норм.</li>"
        "</ul></div>"
        + "<div class=card><h3>Что сервис не делает</h3><ul class=limits>"
        "<li>не распознаёт лица и не идентифицирует людей;</li>"
        "<li>не анализирует звук;</li>"
        "<li>не обращается к облачным AI-сервисам и АБИС банка;</li>"
        "<li>не утверждает, что событие юридически доказано: формулировки осторожные "
        "(«предполагаемое»), решение принимает человек.</li>"
        "</ul></div></div>"
        + "<div class=card><h3>Утверждённые типы событий</h3><ul class=limits>"
        + rules_list
        + "</ul><p class=small class=muted>Полный реестр с цитатами: "
        "<a href='/ui/rules'>Нормативная база</a>. Очередь обработки сейчас: "
        + str(queue)
        + " задач.</p></div>"
        + "<div class=card><h3>API</h3><pre>curl -X POST '"
        + _esc(settings.public_base_url or "https://<host>")
        + "/api/v1/analyze' \\\n  -H 'X-API-Key: $KEY' -F 'file=@clip.mov'\n\ncurl '"
        + _esc(settings.public_base_url or "https://<host>")
        + "/api/v1/jobs/{job_id}' -H 'X-API-Key: $KEY'</pre></div>"
        + "</div>"
        + _footer()
        + "</body></html>"
    )


def page_job(settings: Settings, store: JobStore, manager: JobManager, job: Job) -> str:
    refresh = ""
    body = ""
    if job.status in ("queued", "processing"):
        refresh = "<meta http-equiv=refresh content=3>"
        position = store.queue_position(job.job_id)
        body = (
            "<div class='banner wait'><p class=verdict>Идёт обработка…</p>"
            f"<div class=bar><i style='width:{int((job.progress or 0) * 100)}%'></i></div>"
            f"<p class=small class=muted>Фаза: {_esc(job.phase or 'в очереди')} · "
            f"прогресс {int((job.progress or 0) * 100)}% · "
            + (f"перед задачей в очереди: {position}." if position else "задача обрабатывается.")
            + " Страница обновится автоматически.</p></div>"
        )
    elif job.status == "failed":
        body = (
            "<div class='banner fail'><p class=verdict>Обработка не завершена</p>"
            f"<p class=small>Причина: {_esc(_error_text(job))}. Сервис не выдаёт вердикт при "
            "технической ошибке — это осознанное правило контракта.</p></div>"
        )
    else:
        events = _load_events(job)
        if job.verdict == "violation" and events:
            body = (
                "<div class='banner bad'><p class=verdict>Обнаружены признаки возможных нарушений</p>"
                f"<p class=small>Событий: {len(events)}. Проверьте каждое событие и кадры.</p></div>"
            )
        else:
            body = (
                "<div class='banner ok'><p class=verdict>Нарушение не обнаружено</p>"
                "<p class=small>Визуальных признаков утверждённых типов событий не найдено. "
                "Это не подтверждает отсутствие нарушений, невидимых в кадре.</p></div>"
            )
        body += _events_html(job, events)

    meta = job.meta or {}
    diag = (
        "<div class=card><h3>Диагностика (внутренняя)</h3><div class=kv>"
        f"<div>Файл</div><div>{_esc(job.filename)}</div>"
        f"<div>Размер</div><div>{_fmt_bytes(job.size_bytes)}</div>"
        f"<div>Длительность</div><div>{_fmt_num(job.duration_sec)} с</div>"
        f"<div>Разрешение / FPS</div><div>{_esc(meta.get('width'))}×{_esc(meta.get('height'))} · "
        f"{_fmt_num(meta.get('fps'))}</div>"
        f"<div>Кадров проанализировано</div><div>{_esc(meta.get('frames_analyzed'))} "
        f"(≈{_fmt_num(meta.get('analysis_fps'))} к/с)</div>"
        f"<div>Детектор</div><div>{_esc(meta.get('detector'))}</div>"
        f"<div>Обработка заняла</div><div>{_fmt_num(meta.get('elapsed_sec'))} с</div>"
        f"<div>Предупреждения</div><div>{_esc(', '.join(meta.get('warnings') or []) or '—')}</div>"
        "</div><p class=small class=muted>Эти поля не входят в обязательный контракт и служат "
        "для эксплуатации и разбора инцидентов.</p></div>"
    )
    return (
        _head(f"Задача {job.job_id} — ForteVision").replace("</head>", refresh + "</head>")
        + _header(True)
        + "<div class=wrap>"
        + f"<h1>Задача <code>{_esc(job.job_id)}</code></h1>"
        + body
        + diag
        + "<div class=card><h3>Выгрузки</h3><p class=small>"
        + f"<a href='/ui/jobs/{_esc(job.job_id)}/json'>Машинный JSON (как в API)</a> · "
        + f"<a href='/ui/jobs/{_esc(job.job_id)}/video'>Исходное видео</a></p></div>"
        + "</div>"
        + _video_script(job)
        + _footer()
        + "</body></html>"
    )


def _events_html(job: Job, events: list[dict[str, Any]]) -> str:
    if not events:
        return ""
    registry = load_registry()
    blocks = []
    for event in events:
        rule = registry.get(str(event.get("rule_id") or ""))
        frames = event.get("evidence") or []
        figures = "".join(
            "<figure><img loading=lazy src='/ui/evidence/"
            + _esc(job.job_id)
            + "/"
            + _esc(Path(str(frame.get("path", ""))).name)
            + "'><figcaption>"
            + _fmt_num(frame.get("timestamp_sec"))
            + " с</figcaption></figure>"
            for frame in frames
        )
        rule_html = ""
        if rule is not None:
            rule_html = (
                "<p class=small><b>Норма:</b> " + _esc(rule.act_number) + "; п. " + _esc(rule.paragraph)
                + f" (<span class='tag'>{_esc(rule.status)}</span>) — «"
                + _esc(rule.quoted_text)
                + "»<br><span class=muted>Официальная публикация: "
                + f"<a href='{_esc(rule.source_url)}' rel='noopener' target='_blank'>{_esc(rule.source_url)}</a>"
                + "</span></p>"
            )
            if rule.cannot_infer:
                rule_html += (
                    "<p class='small muted'><b>По записи не устанавливается:</b> "
                    + _esc("; ".join(rule.cannot_infer))
                    + "</p>"
                )
        blocks.append(
            "<div class=card>"
            f"<h3>{_esc(event.get('type'))} <span class=tag>{_esc(event.get('event_code'))}</span></h3>"
            "<p class=small class=muted>"
            f"Таймкод: <a href='#' class=seek data-t='{_esc(event.get('timecode_sec'))}'>"
            f"{_fmt_num(event.get('timecode_sec'))} с</a>"
            f" · начало {_fmt_num(event.get('start_sec'))} с · конец {_fmt_num(event.get('end_sec'))} с"
            f" · видимость: {_esc(event.get('visibility_quality'))}"
            f" · внутренняя уверенность {_fmt_num(event.get('confidence_internal'))}"
            "</p>"
            f"<p>{_esc(event.get('description'))}</p>"
            + rule_html
            + "<div class=evidence>"
            + figures
            + "</div></div>"
        )
    return "".join(blocks)


def _video_script(job: Job) -> str:
    return (
        "<div class=wrap><div class=card><h3>Видео</h3>"
        f"<video id=player controls preload=metadata src='/ui/jobs/{_esc(job.job_id)}/video'></video>"
        "<p class=small class=muted>Клик по таймкоду события перематывает плеер.</p>"
        "<script>document.addEventListener('click',function(e){var a=e.target.closest('.seek');"
        "if(!a)return;e.preventDefault();var t=parseFloat(a.dataset.t||'0');"
        "var p=document.getElementById('player');if(p){p.currentTime=t;p.play&&p.play();"
        "p.scrollIntoView({behavior:'smooth',block:'center'});}});</script>"
        "</div></div>"
    )


def _load_events(job: Job) -> list[dict[str, Any]]:
    return job.events or []


def _error_text(job: Job) -> str:
    mapping = {
        "PROBE_FAILED": "файл не распознан как видео",
        "UNSUPPORTED_VIDEO": "видеодорожка не декодируется",
        "DECODE_FAILED": "не удалось декодировать кадры",
        "JOB_TIMEOUT": "превышен лимит времени обработки",
        "WORKER_CRASHED": "процесс анализа завершился аварийно",
        "OUT_OF_MEMORY": "недостаточно памяти",
    }
    base = mapping.get(job.error_code or "", "техническая ошибка")
    detail = (job.meta or {}).get("traceback") or job.error_message or ""
    return f"{base} ({job.error_code})" + (f": {detail[-300:]}" if detail and job.error_code not in mapping else "")


def page_rules() -> str:
    registry = load_registry()
    rows = []
    for rule in registry.rules:
        badge = "ok" if rule.status == "approved" else ""
        rows.append(
            "<div class=card>"
            f"<h3>{_esc(rule.event_type)} · {_esc(rule.event_label)} "
            f"<span class='badge {badge}'>{_esc(rule.status)}</span></h3>"
            f"<p class=small class=muted>{_esc(rule.act_number)}; п. {_esc(rule.paragraph)} · "
            f"<a href='{_esc(rule.source_url)}' target='_blank' rel='noopener'>{_esc(rule.source_url)}</a></p>"
            f"<blockquote style='margin:8px 0;padding-left:12px;border-left:3px solid var(--line)'>"
            f"{_esc(rule.quoted_text)}</blockquote>"
            f"<p class=small><b>Наблюдаемый признак:</b> {_esc(rule.observable_predicate)}</p>"
            f"<p class=small><b>Условия применения:</b> {_esc(rule.required_context)}</p>"
            f"<p class=small><b>Не выводится по видео:</b> {_esc('; '.join(rule.cannot_infer) or '—')}</p>"
            f"<p class='small muted'>{_esc(rule.revision_note)}<br>{_esc(rule.reviewer)}</p>"
            "</div>"
        )
    return (
        _head("Нормативная база — ForteVision")
        + _header(True)
        + "<div class=wrap><h1>Нормативная база</h1>"
        + f"<p class=lead>{_esc(REGISTRY_POLICY)}</p>"
        + f"<p class=small class=muted>Версия реестра {_esc(registry.version)} · проверено "
        f"{_esc(registry.verified_on)}. Машинная версия: <a href='/api/v1/rules'>/api/v1/rules</a>.</p>"
        + "".join(rows)
        + "</div>"
        + _footer()
        + "</body></html>"
    )


def page_health(
    settings: Settings, store: JobStore, manager: JobManager, version: str, started_at: float
) -> str:
    stats = store.stats()
    uptime = time.time() - started_at
    return (
        _head("Статус — ForteVision")
        + _header(True)
        + "<div class=wrap><h1>Статус сервиса</h1>"
        + "<div class=card><div class=kv>"
        + f"<div>Версия</div><div>{_esc(version)}</div>"
        + f"<div>Аптайм</div><div>{uptime / 3600:.2f} ч</div>"
        + f"<div>Очередь</div><div>{store.queue_length()}</div>"
        + f"<div>Работник занят</div><div>{'да' if manager.current_job else 'нет'}</div>"
        + f"<div>Задач обработано</div><div>{manager.total_processed}</div>"
        + f"<div>Всего задач в БД</div><div>{_esc(stats)}</div>"
        + f"<div>Лимит времени на задачу</div><div>{int(settings.job_timeout_sec)} с</div>"
        + f"<div>Последняя ошибка работника</div><div>{_esc(manager.last_error or '—')}</div>"
        + "</div></div><div class=card><h3>Проверки</h3><ul class=limits>"
        + "<li>JSON-схема ответа: <code>apps/api/app/schemas.py</code>, тесты <code>tests/test_contract.py</code></li>"
        + "<li>Изоляция декодера: ffprobe выполняется отдельным процессом с таймаутом</li>"
        + "<li>Локальный OpenCV DNN, без облачных AI API, распознавания лиц и звука</li>"
        + "</ul></div></div>"
        + _footer()
        + "</body></html>"
    )


def page_login_required() -> str:
    return (
        _head("Требуется вход — ForteVision")
        + _header(False)
        + "<div class=wrap><h1>Требуется вход</h1><div class=card>"
        "Откройте <a href='/'>главную страницу</a> и введите ключ интерфейса.</div></div></body></html>"
    )


def page_login_error() -> str:
    return (
        _head("Неверный ключ — ForteVision")
        + _header(False)
        + "<div class=wrap><h1>Неверный API-ключ</h1><div class=card>"
        "Проверьте ключ и <a href='/'>попробуйте снова</a>.</div></div></body></html>"
    )


def page_not_found(job_id: str) -> str:
    return (
        _head("Задача не найдена — ForteVision")
        + _header(True)
        + "<div class=wrap><h1>Задача не найдена</h1><div class=card>"
        + f"Идентификатор <code>{_esc(job_id)}</code> неизвестен.</div></div></body></html>"
    )


def page_error(message: str, status: int) -> str:
    return (
        _head("Ошибка — ForteVision")
        + _header(True)
        + "<div class=wrap><h1>Файл не принят</h1><div class=card>"
        + f"<p>{_esc(message)}</p><p class=small class=muted>HTTP {status}. "
        "Вернитесь на <a href='/'>страницу загрузки</a>.</p></div></div></body></html>"
    )


# -- formatting -------------------------------------------------------------------------


def _fmt_num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.1f}" if abs(number - round(number)) > 1e-6 else str(int(round(number)))


def _fmt_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"
