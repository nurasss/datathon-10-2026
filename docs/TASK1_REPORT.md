# A. Результат аудита

| Модуль | Состояние baseline | Решение |
|---|---|---|
| `apps/api`: загрузка | Env-ключ и фоновый процесс есть; задача видна worker до окончания записи; `.qt` принимается; лимит 512 MiB; синхронный ffprobe в POST | Публикация задачи после записи, только `.mov`, лимит 100 MiB и потоковый multipart guard; проверка/анализ в subprocess |
| `apps/api`: jobs / worker / SQLite | Таймаут только активного анализа; очередь и рестарт могут продлевать processing; поздний результат может перезаписать ошибку | Watchdog 280 с с принятия файла, очередь включена, проверка на GET, завершение process group, защита terminal state; прерванный анализ при рестарте → failed |
| `apps/api`: схема / evidence | Нет инварианта verdict/events, границ 1–3 кадров; отсутствующие evidence молча удаляются, возможен ложный no_violation | Строгие Pydantic-модели, JPEG decode, временные границы, fail-closed адаптер до done и при чтении |
| `apps/api`: UI | Есть server-rendered HTML, таймкоды и нормы; вход по ключу жюри; весь video в памяти, Range фактически не работает | Отдельный FV_UI_KEY/cookie либо публичные UI-эндпоинты; FileResponse с Range; ключ жюри не в JS |
| `packages/vision` | Рабочий CPU NanoDet + EV01/EV02 + настоящие кадры; четыре синтетических теста проходят | Алгоритмы, конфигурация и веса не изменены; подключён как есть, заглушки нет; проверка повреждённого входа добавлена в API entrypoint |
| `packages/vision/models` | Две небольшие модели, MediaPipe не используется; нет локального файла лицензии | Обе сверены по SHA256 с OpenCV Zoo, Apache-2.0; добавлены provenance/license, неиспользуемая модель сохранена |
| `packages/rules` | Два approved-типа, остальные draft/reference; нормы берутся из JSON, не генерируются | Сохранён; юридическая актуальность заново не проверялась |
| `infra` | Dockerfile есть; compose привязан к чужой external network, отсутствующему service.env; нет ports | Compose упрощён, порт 8000, env-секреты, лимит 100 MiB; Docker не запускался |
| `tests` | Только 4 pipeline-теста, API/таймауты/auth не покрыты | Всего 45 тестов: AT-01…04, worker, битый/усечённый MOV, схема/JPEG, watchdog/kill, UI/Range |
| `scripts` | Есть dev-инструменты; нет единого запуска и HTTP-замера | Добавлены run_server.sh и acceptance.py; все четыре dev-скрипта дают --help с exit 0 |
| Зависимости / лишнее | Пины requirements не совпадают с .venv; pydantic-settings не используется; есть лишние предустановленные пакеты | Прямые пины приведены к проверенной среде; лишние пакеты не удалялись из окружения пользователя; полный license inventory |
| Git / документация | Нет коммитов, .gitignore, README | Первым действием baseline 5f4ad60; окружение/данные/видео/кэши исключены, README и инструкции добавлены |

# B. Git

```text
$ git log --oneline
5f4ad60 baseline: initial state
```

```text
$ git diff --stat 5f4ad60
 .dockerignore                                 |  10 +
 .env.example                                  |   6 +
 .gitignore                                    |   8 +
 README.md                                     | 102 ++++++++
 apps/api/app/config.py                        |  11 +-
 apps/api/app/db.py                            |  36 ++-
 apps/api/app/job_entry.py                     |  43 ++++
 apps/api/app/main.py                          | 203 ++++------------
 apps/api/app/middleware.py                    |  48 ++++
 apps/api/app/results.py                       |  39 +++
 apps/api/app/schemas.py                       | 100 ++++----
 apps/api/app/security.py                      |   2 +-
 apps/api/app/ui.py                            |  14 +-
 apps/api/app/worker.py                        |  61 +++--
 apps/api/requirements.txt                     |  19 +-
 docs/DEPENDENCIES.md                          |  84 +++++++
 docs/TASK1_REPORT.md                          | 215 ++++++++++++++++
 infra/docker-compose.yml                      |  38 +--
 packages/vision/models/LICENSE-APACHE-2.0.txt | 201 +++++++++++++++
 packages/vision/models/README.md              |  19 ++
 scripts/acceptance.py                         | 138 +++++++++++
 scripts/run_server.sh                         |  18 ++
 tests/test_contract.py                        | 338 ++++++++++++++++++++++++++
 23 files changed, 1473 insertions(+), 280 deletions(-)
```

Изменения после baseline оставлены в рабочем дереве, второй коммит не создавался.
Новые файлы отмечены `git add -N`, чтобы `git diff --stat 5f4ad60` включал их.
Детекция не изменена:

```text
$ git diff --numstat 5f4ad60 -- packages/vision/vision packages/rules configs/inference.default.json
```

# C. Полный вывод pytest -q

```text
$ .venv/bin/python -m pytest -q
.............................................                            [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /Users/nuras/Desktop/datathon/forte-vision/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
45 passed, 1 warning in 11.15s
```

# D. Реальные curl и ответы

Выполнены `scripts/acceptance.py`. Ключ замаскирован; base64 — первые 40 символов.
Полная стенограмма всех запросов: `artifacts/acceptance-final/curl.txt`.
Ниже `-w` записан с экранированными переводами строк, эквивалентно аргументу curl в стенограмме.

```text
AT-01
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' -F 'file=@/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov' http://127.0.0.1:8000/api/v1/analyze
{"job_id":"a82f130c3067"}
HTTP 202

AT-02: без ключа
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -F 'file=@/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov' http://127.0.0.1:8000/api/v1/analyze
{"detail":"Неверный или отсутствующий X-API-Key"}
HTTP 401
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' http://127.0.0.1:8000/api/v1/jobs/a82f130c3067
{"detail":"Неверный или отсутствующий X-API-Key"}
HTTP 401

AT-02: неверный ключ
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: wrong-key' -F 'file=@/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov' http://127.0.0.1:8000/api/v1/analyze
{"detail":"Неверный или отсутствующий X-API-Key"}
HTTP 401
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: wrong-key' http://127.0.0.1:8000/api/v1/jobs/a82f130c3067
{"detail":"Неверный или отсутствующий X-API-Key"}
HTTP 401

AT-03
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' http://127.0.0.1:8000/api/v1/jobs/unknown-job-id
{"detail":"Задача не найдена"}
HTTP 404

AT-04
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' -F 'file=@/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov;filename=not-video.txt' http://127.0.0.1:8000/api/v1/analyze
{"detail":"Ожидается файл .mov"}
HTTP 415

DONE, Х-20
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' http://127.0.0.1:8000/api/v1/jobs/a82f130c3067
{"job_id":"a82f130c3067","status":"done","verdict":"no_violation","violations":[]}
HTTP 200

Х-16
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' -F 'file=@/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-16.mov' http://127.0.0.1:8000/api/v1/analyze
{"job_id":"190f11d36813"}
HTTP 202
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' http://127.0.0.1:8000/api/v1/jobs/190f11d36813
```

```json
{
  "job_id": "190f11d36813",
  "status": "done",
  "verdict": "violation",
  "violations": [
    {
      "type": "Предполагаемое оставление рабочего места",
      "timecode_sec": 9.33,
      "description": "Рабочее место кассового работника остаётся не занятым 3.0 с (с 9.3 с по 12.3 с), при этом клиент находится у кассы (клиент виден у кассы с 0.0 с). Видео показывает оставление рабочего места при незавершённом, по видимым признакам, обслуживании клиента. Это может указывать на несоблюдение требования: Постановление Правления Национального Банка Республики Казахстан от 29 ноября 2019 года № 231 (зарегистрировано в Министерстве юстиции Республики Казахстан 3 декабря 2019 года № 19680), пункт 22: «Кассовому работнику не допускается покидать рабочее место до окончания обслуживания клиента.» По записи не устанавливается, завершено ли обслуживание документально, кто именно является кассовым работником и была ли служебная необходимость отойти. Результат — подсказка для проверки человеком.",
      "evidence_frames": [
        {"timestamp_sec": 8.53, "image_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQE…"},
        {"timestamp_sec": 9.33, "image_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQE…"},
        {"timestamp_sec": 10.93, "image_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQE…"}
      ]
    }
  ]
}
```

```text
HTTP 200

Х-14
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' -F 'file=@/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-14.mov' http://127.0.0.1:8000/api/v1/analyze
{"job_id":"febb445d3388"}
HTTP 202
$ curl -sS --max-time 30 -w '\nHTTP %{http_code}\n' -H 'X-API-Key: ***MASKED***' http://127.0.0.1:8000/api/v1/jobs/febb445d3388
{"job_id":"febb445d3388","status":"done","verdict":"no_violation","violations":[]}
HTTP 200
```

Полные ответы проверены DoneJob. Все три JPEG Х-16 декодируются; побайтово совпали
с повторно извлечёнными и закодированными кадрами исходного загруженного видео.
Все timestamps/timecodes находятся в пределах длительности.

# E. Время обработки и Mac

Последовательный прогон через API, с проверкой целостности видео, CPU-only,
от начала POST до первого наблюдённого done; интервал опроса 0,2 с.

| Файл | Длительность видео, с | Ответ POST, с | POST → done, с | Вердикт |
|---|---:|---:|---:|---|
| Х-20.mov, самый короткий | 11.633333 | 0.165 | 13.949 | no_violation |
| Х-16.mov, верхний медианный из 42 | 22.733021 | 0.101 | 22.744 | violation |
| Х-14.mov, самый длинный | 142.533021 | 0.631 | 75.070 | no_violation |

```text
$ sysctl -n machdep.cpu.brand_string hw.ncpu hw.memsize
Intel(R) Core(TM) i5-7360U CPU @ 2.30GHz
4
8589934592
```

# F. Зависимости и лицензии

Полный инвентарь **всех 36 pip-пакетов** с версиями и лицензиями:
[`docs/DEPENDENCIES.md`](DEPENDENCIES.md). Пины прямых зависимостей совпадают с
проверенным окружением; `pip check`: `No broken requirements found.`

| Веса | Лицензия | Размер | Использование |
|---|---|---:|---|
| packages/vision/models/nanodet.onnx | Apache-2.0 | 3800954 байт | Основной CPU-детектор |
| packages/vision/models/person_mediapipe.onnx | Apache-2.0 | 11990159 байт | Не используется |

SHA256 сверены с OpenCV Zoo; источники, хэши и текст Apache-2.0 сохранены в
`packages/vision/models/README.md` и `LICENSE-APACHE-2.0.txt`.

Важно: не все транзитивные компоненты MIT/Apache/BSD. `certifi` — MPL-2.0,
`typing_extensions` — PSF-2.0; OpenCV wheel включает LGPL FFmpeg и другие notices;
системные ffmpeg/ffprobe 9.0.2-tessus собраны с `--enable-gpl --enable-version3`,
GPL-3.0-or-later. Эти лицензии разрешают коммерческое использование, но требуют
соблюдения своих условий распространения. AGPL и CC-BY-NC в проверенном inventory нет.

# G. Что не сделано / известные проблемы

- Детекция/пороги не улучшались; точность по open_labels.xlsx и всем 42 роликам не оценивалась.
  Х-14 реально получил no_violation — это результат исходного алгоритма, не заглушки.
- Публичный туннель/HTTPS, Docker и проверка живого UI в браузере не выполнялись.
  UI проверен HTTP-тестами (контент, вход, таймкоды, нормы, кадры, Range).
- Watchdog/kill проверены с укороченным таймаутом и искусственно зависшим subprocess;
  отдельного ожидания полного 280-секундного зависания не было.
- Нет изоляции владельцев задач между UI-пользователями, полноценного rate limiting,
  автозапуска macOS и нагрузочного теста. Большая очередь может завершаться failed по 280 с.
- Юридическая актуальность исходного реестра заново не проверялась.
- Один non-failing deprecation warning Starlette/httpx; httpx2 не устанавливался.
- Если допустимы исключительно Apache/MIT/BSD, а не любые коммерческие лицензии,
  необходима отдельная проверка/замена транзитивных и нативных компонентов (см. F).
- Сервер оставлен на `http://localhost:8000`, запуск — `scripts/run_server.sh`,
  инструкции — `README.md`. Случайные локальные ключи в игнорируемом `.env` (права 0600);
  перед передачей жюри выставить согласованный FV_API_KEYS. Лог — `data/server.log`.
