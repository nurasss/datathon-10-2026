# ForteVision — ARCHITECTURE

Черновик (участник: infra). Описывает **фактический** код в `/Users/nuras/Desktop/datathon/forte-vision`
(ветка `main` @ `1b5c2a9`) по состоянию на 2026-10-17, а не целевое состояние из ТЗ.
Где я не проверял поведение лично — стоит пометка `TODO` или «не проверено».

Типы нарушений и нормы здесь не описаны: их формулировки лежат в реестре правил
`packages/rules/rules/registry.json` и будут уточняться отдельно.

---

## 1. Что это за система

FastAPI-сервис: принимает видеофайл `.mov` кассовой зоны, асинхронно анализирует его
классическим CV-пайплайном (без внешних API и без облака) и отдаёт результат с
признаками нарушений и кадрами-доказательствами.

Всё работает на одном Mac (Intel i5, 4 логических ядра, 8 ГБ), без Docker и без GPU.
Внешние вызовы только по инфраструктуре: туннель к публичному адресу. Видео наружу не уходит.

Контракт с жюри (не менялся в этой ветке):

| Метод | Путь | Ответ |
|---|---|---|
| POST | `/api/v1/analyze` | multipart поле `file` + заголовок `X-API-Key` → `202 {"job_id": "..."}` |
| GET | `/api/v1/jobs/{job_id}` | `200` + `processing` \| `done` + результат \| `failed` |

Бюджет времени у жюри — 5 минут на видео; сервис держит свой лимит 280 с (`FV_JOB_TIMEOUT_SEC`, жёстко
ограничен сверху в `config.py`), считая от момента приёма файла, **включая время в очереди**.

---

## 2. Компоненты и раскладка

```
forte-vision/
├── apps/api/                    FastAPI-приложение + встроенный worker
│   ├── app/main.py              HTTP-слой: публичный API, UI, самопроверки на жизненном цикле
│   ├── app/config.py            все настройки из переменных окружения (единый Settings)
│   ├── app/db.py                SQLite-хранилище задач (JobStore) + очередь
│   ├── app/worker.py            JobManager: потоки-исполнители и запуск дочерних процессов
│   ├── app/job_entry.py         точка входа дочернего процесса анализа
│   ├── app/results.py           сборка публичного JSON из строки БД + проверка evidence
│   ├── app/schemas.py           pydantic-схемы контракта (extra="forbid", проверка JPEG)
│   ├── app/security.py          X-API-Key (сравнение compare_digest) и подписанная cookie UI
│   ├── app/middleware.py        UploadGuard: 401 до разбора multipart, лимит тела запроса
│   ├── app/ui.py                серверный HTML-интерфейс (без сборки фронтенда)
│   └── requirements.txt         fastapi, uvicorn, python-multipart, pydantic, numpy, opencv-python-headless
├── packages/vision/vision/      CV-пайплайн
│   ├── videoprobe.py            ffprobe (таймаут 30 с) + фолбэк на OpenCV, кадровый гридер
│   ├── pipeline.py              оркестрация: probe → decode → zones → reasoning → evidence → finalize
│   ├── detector.py              NanoDet-Plus-M 1.5x 416 через cv2.dnn (CPU), NMS, фильтры площадей
│   ├── tracker.py               IoU-трекер (iou 0.25, max_age 3, min_hits 2)
│   ├── zones.py                 вывод зон по сетке занятости 48×27 (пост/прилавок)
│   ├── events.py                временная логика: состояния по кадрам, кандидаты событий
│   ├── deskobjects.py           модель-независимый детектор статичных объектов на рабочем столе
│   ├── evidence.py              JPEG ≤960 px, качество 85
│   └── job_runner.py            запись отчёта задачи (atomic write) и прогресса в БД
├── packages/rules/rules/        реестр правовых правил (JSON) + загрузчик
├── configs/inference.default.json   параметры пайплайна (FPS, пороги, зоны, evidence)
├── data/                        РАНТАЙМ: jobs.db, uploads/, evidence/, reports/ (в git не хранится)
└── scripts/run_server.sh        штатный запуск uvicorn (порт 8000, 1 процесс uvicorn)
```

Инфраструктура (эта ветка, worktree `fv-infra`): `infra/**` — туннель, launchd-агенты,
healthcheck; приложение запускается **из** `forte-vision`, потому что там же лежит `.venv`.

---

## 3. Поток обработки одной задачи

1. **Приём.** `UploadGuard` (raw ASGI) проверяет `X-API-Key` для `/api/v1/analyze` и
   `/api/v1/jobs/*` **до** разбора multipart и до чтения тела. Затем границы тела:
   `Content-Length > FV_MAX_UPLOAD_MB + 64 КиБ` → `413`; иначе счётчик прочитанных байт даёт
   тот же `413` даже при chunked-запросе без `Content-Length`.
2. **Валидация и запись.** `main.analyze()`: расширение имени должно быть `.mov` (иначе `415`),
   очередь меньше `FV_MAX_QUEUE` (иначе `429` + `Retry-After: 30`), потоковая запись чанками по 1 МиБ
   с подсчётом SHA-256 в `data/uploads/{job_id}.mov`; пустой файл → `422`; превышение размера → `413`
   (частично записанный файл удаляется). Задача создаётся в БД со статусом `queued`, отдаётся `202`.
3. **Постановка в очередь.** `JobStore.create_job(...)` — INSERT в SQLite. Публично `queued`
   маскируется под `processing` (`Job.public_status`), чтобы контракт жюри видел только три статуса.
4. **Выдача воркером.** `JobManager` (потоки `fv-worker-{i}`, `FV_WORKER_CONCURRENCY` штук, по умолчанию 1)
   атомарно забирает самую старую задачу: `UPDATE ... WHERE job_id = (SELECT ... WHERE status='queued' ORDER BY created_at LIMIT 1) RETURNING *`.
   Плюс поток-сторож `fv-watchdog` раз в 0.1 с вызывает `store.expire(FV_JOB_TIMEOUT_SEC)`.
5. **Дочерний процесс.** На каждую задачу — отдельный процесс:
   `python -m app.job_entry --job-id … --video … --out data/reports/{job_id}.json --evidence-dir data/evidence …`
   (`start_new_session=True`), вывод — в `data/reports/{job_id}.log`. Окружение наследуется от сервера
   (поэтому PATH с `ffprobe`/`ffmpeg` доезжает и сюда), потоки BLAS/OpenMP принудительно равны 1.
6. **Проверка целостности.** `job_entry.validate_input()`: при наличии ffmpeg — полное декодирование
   `ffmpeg -xerror -err_detect explode -f null -` и ненулевой код возврата → `DECODE_FAILED`;
   без ffmpeg — полное декодирование OpenCV. Битый контейнер не доходит до детектора.
7. **Анализ.** `VideoAnalyzer`: `probe` (ffprobe) → `decode` (равномерная выборка, цель 420 кадров,
   2–6 к/с, не шире 960 px) → `zones` (зоны по занятости) → `reasoning` (состояния + кандидаты событий) →
   `evidence` (до 3 кадров на событие, JPEG ≤960 px q85) → `finalize`. Фазы и прогресс пишутся в БД
   (`phase`, `progress`), их показывает UI.
8. **Правила.** Событие превращается в нарушение только если для его кода (`EV01`, `EV02`) в реестре
   есть правило со статусом `approved`
   (`pipeline.py`: `self.registry.for_event("EV01"/"EV02")`). Черновики и справочные правила в вывод
   не попадают никогда.
9. **Отчёт и завершение.** `job_runner` пишет JSON атомарно (`tmp` + `os.replace`) и обновляет строку БД
   (`done` + вердикт + события, либо `failed` + код ошибки). Поздний результат не может перезаписать
   уже истёкшую задачу: `finish(...)` обновляет только строки, которые всё ещё `processing` и не старше таймаута.
10. **Выдача результата.** `GET /api/v1/jobs/{id}` → `results.done_response()` заново читает JPEG-файлы
    evidence с диска, проверяет, что путь не выходит за каталог задачи, что метки времени в границах
    длительности, и отдаёт base64. Если evidence пропал или повреждён — задача помечается `failed`,
    а не отдаётся «пустой» вердикт (fail-closed).

### Тайм-аут, отмена, перезапуск

* Бюджет считается от `created_at` (то есть **включая ожидание в очереди**). При исчерпании — `JOB_TIMEOUT`.
* Дочерний процесс убивается группой: `SIGTERM` процесс-группе, 0.5 с ожидания, затем `SIGKILL`.
* При старте сервера `store.fail_interrupted()` переводит все зависшие `processing`/`queued` в `failed`
  с кодом `WORKER_CRASHED` — после перезапуска ничего не «залипает», но и не продолжается с места падения
  (повторный анализ только новым POST).
* Чистка: когда очередь пуста, не чаще чем раз в 30 минут удаляются терминальные задачи старше
  `FV_RETENTION_HOURS` (72 ч) вместе с файлами и кадрами.

---

## 4. Хранилище

SQLite `data/jobs.db`, WAL, одно соединение на операцию, `busy_timeout=15 с`.

Таблица `jobs`: `job_id` (PK, 12 hex), `created_at`, `updated_at`, `status`
(`queued|processing|done|failed`), `phase`, `progress`, `filename`, `size_bytes`, `sha256`,
`duration_sec`, `verdict`, `events_json`, `error_code`, `error_message`, `meta_json`,
`started_at`, `finished_at`, `client_ip`. Индекс по `(status, created_at)`.

Файлы: `data/uploads/{job_id}.mov`, `data/reports/{job_id}.json` (+ `.log`), `data/evidence/{job_id}/*.jpg`.

---

## 5. Публичный HTTP-контракт

* `POST /api/v1/analyze` → `202 {job_id}`; `401` без/с неверным ключом, `415` не `.mov`,
  `422` пустой файл, `413` превышение размера, `429` переполненная очередь.
* `GET /api/v1/jobs/{job_id}` →
  * `processing`: `{"job_id","status":"processing"}`;
  * `done`: `{"job_id","status":"done","verdict":"violation"|"no_violation","violations":[...]}`,
    где у нарушения `type`, `timecode_sec`, `description`, `evidence_frames` (1–3 кадра
    `{timestamp_sec, image_base64}` с настоящим JPEG); `no_violation` ⇔ пустой список (валидатор схемы);
  * `failed`: `{"job_id","status":"failed","error": "..."} `— человекочитаемая причина на русском
    (код в скобках, например `(JOB_TIMEOUT)`);
  * `404`, если задачи нет, `401` без ключа.
* Дополнительно (не требуется жюри, используется UI): `/api/v1/jobs/{id}/details` (плюс ссылка на
  правовое правило),
  `/api/v1/jobs/{id}/video` (`Accept-Ranges: bytes`), `/api/v1/rules` (публичный, реестр),
  `/api/v1/stats` (по ключу), `/api/v1/health` (публичный).
* Ошибки контракта отдаются как `{"detail": "..."}`.

Коды ошибок анализа, которые может увидеть клиент: `PROBE_FAILED`, `UNSUPPORTED_VIDEO`,
`DECODE_FAILED`, `JOB_TIMEOUT`, `WORKER_CRASHED`, `WORKER_ERROR`, `REPORT_UNREADABLE`,
`INVALID_RESULT`, `UPLOAD_MISSING`, `OUT_OF_MEMORY`, `INTERNAL_ERROR`.

---

## 6. Настройки (все — переменные окружения)

Источник — `apps/api/app/config.py`. Значения по умолчанию важны: `JOB_TIMEOUT_SEC` и
`MAX_UPLOAD_MB` жёстко зажаты сверху (280 с и 100 МБ).

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `FV_API_KEYS` | пусто | ключи жюри через запятую; пусто ⇒ публичный API закрыт (401 всем) |
| `FV_UI_KEY` | пусто | отдельный ключ UI; пусто ⇒ UI без пароля |
| `FV_SECRET` | случайный при старте | подпись cookie UI (если не задан — сессии слетают при перезапуске) |
| `FV_DATA_DIR` | `<root>/data` | каталог jobs.db, uploads, evidence, reports |
| `FV_PROJECT_ROOT` | корень репозитория | от него считаются пути к моделям и конфигу |
| `FV_INFERENCE_CONFIG` | `configs/inference.default.json` | параметры пайплайна |
| `FV_MAX_UPLOAD_MB` | 100 (зажат ≤100) | предел размера файла |
| `FV_MAX_QUEUE` | 8 | очередь до `429` |
| `FV_JOB_TIMEOUT_SEC` | 280 (зажат ≤280) | бюджет задачи от приёма, включая очередь |
| `FV_RETENTION_HOURS` | 72 | срок хранения результатов |
| `FV_WORKER_ENABLED` | true | встроенный воркер |
| `FV_WORKER_CONCURRENCY` | 1 (1..8) | сколько задач параллельно (в проде выставлено 2) |
| `FV_WORKER_POLL_SEC` | 1.0 | период опроса очереди |
| `FV_HOST` | 0.0.0.0 | интерфейс uvicorn (в `scripts/run_server.sh`, порт 8000 жёстко) |
| `FV_PUBLIC_BASE_URL` | пусто | только для примеров curl в UI |
| `FV_SERVICE_NAME` | `ForteVision` | поле в `/api/v1/health` |
| `FV_PYTHON` | — | интерпретатор для `scripts/run_server.sh` |

Инфраструктурные переменные этой ветки (не читаются приложением):
`FV_APP_ROOT`, `FV_INFRA_ROOT`, `FV_ENV_FILE`, `FV_TUNNEL_PROVIDER`, `FV_TUNNEL_UPSTREAM`,
`NGROK_AUTHTOKEN`, `NGROK_DOMAIN`, `CLOUDFLARED_*`, `FV_HEALTHCHECK_*`, `FV_LOG_DIR`.

---

## 7. Модель и вычислительная нагрузка

* Детектор: `packages/vision/models/nanodet.onnx` — NanoDet-Plus-M 1.5x 416 (OpenCV Zoo, Apache-2.0),
  инференс CPU через `cv2.dnn`, letterbox 416, фильтр классов «person», NMS, отсев боксов
  вне диапазона 0.4–75 % площади кадра. Веса — 3.8 МБ, лежат в репозитории.
* Число кадров на анализ ограничено (~420 на клип), поэтому время зависит от длительности слабо.
  Измерено на этом Mac (артефакты команды): короткий клип 11.6 с → ~14 с, 22.7 с → ~23 с,
  142.5 с → ~75 с. Мои прогоны 2026-10-17: короткий клип Х-20 (2.4 МБ) → 13 с и локально, и через
  публичный туннель; Х-14 (28.7 МБ, 142.5 с) через туннель под нагрузкой (load average ~14) → **76 с**
  (первый прогон в тех же условиях — 94 с).
* Память: один процесс анализа — OpenCV+numpy+NanoDet, `TODO` точный пик RSS не измерял;
  при `FV_WORKER_CONCURRENCY=2` на 8 ГБ и параллельно работающих приложениях возможен своп.
  Практический вывод: 2 — компромисс по скорости, при проблемах с памятью ставить 1.

---

## 8. Развёртывание (как это работает в проде)

```
жюри ──HTTPS──> ngrok (статический домен, адрес не меняется)
                    │
                    └──> 127.0.0.1:3000  Next.js (проксирует /api/v1/* на бэкенд)
                              │
                              └──> 127.0.0.1:8000  uvicorn (1 процесс) + встроенный worker
                                                        └── subprocess: app.job_entry (анализ)
```

* Порт 8000 — контракт с прокси, он же «запасной» upstream для туннеля.
* uvicorn запускается **в один процесс** (`--workers 1`). Это архитектурное требование: `JobManager`
  и `JobStore` — синглтоны уровня модуля, второй процесс uvicorn запустил бы второй воркер и задачи
  могли бы обрабатываться дважды.
* Живучесть обеспечивают launchd-агенты (`KeepAlive`) и `caffeinate -dimsu`; подробности — RUNBOOK.md.

---

## 9. Безопасность

* Ключи живут только в окружении: `FV_API_KEYS`, `FV_UI_KEY`, `FV_SECRET`, `NGROK_AUTHTOKEN`.
  В git их нет; `.env` и `*.env` в `.gitignore`.
* Сравнение ключей — `hmac.compare_digest`; в логах ключи не печатаются; UI никогда не отдаёт ключ жюри.
* `/api/v1/analyze` и `/api/v1/jobs/*` защищены до чтения тела запроса (дёшево против мусорных загрузок).
* UI-сессия — подписанная HMAC cookie (HttpOnly, SameSite=Lax, 7 дней)
  и **не** даёт доступа к API жюри.
* Открытые вопросы (не блокируют сдачу, но требуют решения):
  1. **Служебные эндпоинты открыты**: проверено вживую — `/redoc`, `/api/docs`, `/api/openapi.json`
     отдают `200` без ключа (в том числе через публичный туннель). `/docs` и `/openapi.json` — `404`.
     Точное изменение для `apps/api/app/main.py` — в `docs/submission/APP-CHANGE-REQUEST-docs-endpoints.md`.
  2. Нет ограничения частоты запросов: один клиент с валидным ключом может занять очередь.
  3. Нет разделения задач по владельцу: зная `job_id`, любой владелец ключа получит результат.
  4. Если `FV_UI_KEY` пуст, `/ui/*` открыт всем, кто дошёл до адреса, включая загрузку и просмотр видео.
  5. `GET /api/v1/jobs/{id}/video` не вызывает `store.expire()` (расхождение с остальными ручками,
     безвредно, но стоит выровнять).

---

## 10. Ограничения и известные разрывы

* Docker (`infra/Dockerfile`, `infra/docker-compose.yml`) в репозитории есть, но **никем не запускался**;
  поддерживаемый путь — нативный запуск через `scripts/run_server.sh`.
* Реестр правил содержит 2 `approved` правила (EV01, EV02) и 3 недействующих (`draft`/`reference`);
  остальные классы нарушений не реализованы by design.
* Точность на полном наборе из 40 клипов измерена командой отдельно (precision 0.63, recall 0.57, F1 0.60
  по `artifacts/eval/20261017_174122/metrics.json`); этот документ точность не подтверждает и не пересчитывает.
* ТЗ (`docs/TZ_BACKEND.md`, `docs/TZ_FRONTEND.md`) описывает браузерный JSON API `/ui/api/*`,
  которого в коде **нет**: фактический UI — серверный HTML на `/ui/*`. ТЗ — целевое состояние, не факт.
* Идентификатор задачи предсказуемо не связан с клиентом: `client_ip` пишется в БД из
  `X-Forwarded-For` (первое значение), но ни на что не влияет.
* `TODO` (не проверено мной): поведение прокси Next.js на больших multipart-запросах —
  это код другого участника; в моих тестах большая загрузка шла напрямую в 8000.
* Прослеживаемость конфига: `pipeline.py` создаёт детектор как `NanoDetPersonDetector(model_path)`
  без передачи `NanoDetConfig`, поэтому `nms_threshold` (0.55) и `input_size` из
  `configs/inference.default.json` до детектора **не доходят** — используется дефолт класса
  (`nms_threshold=0.5`); `min_score` при этом передаётся в `detect()` на каждом кадре и работает.
  На качество влияет слабо, но это скрытая рассинхронизация конфига и кода. Правка — в зоне `packages/`. 
