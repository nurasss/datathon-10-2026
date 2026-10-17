# ForteVision — API

Публичный HTTP-контракт для жюри. Всё в этом файле проверено живыми запросами 2026-10-17;
ответы в примерах — реальные, из прогонов на этом Mac.

## Базовый адрес

```
BASE_URL = https://<ВАШ-ДОМЕН>.ngrok-free.app
```

Плейсхолдер `<ВАШ-ДОМЕН>` заменяется на статический домен ngrok из
`~/.config/forte-vision/forte-vision.env` (`NGROK_DOMAIN`). Фактический адрес всегда можно узнать:

```sh
/Users/nuras/Desktop/datathon/fv-infra/infra/tunnel/public_url.sh
```

## Аутентификация

Один заголовок на всех эндпоинтах, кроме `/api/v1/health` и `/api/v1/rules`:

```
X-API-Key: <ключ жюри из FV_API_KEYS>
```

Ключ не передаётся в query-параметрах и не печатается в логах сервиса.

---

## 1. Отправка видео

```sh
curl -sS -X POST "$BASE_URL/api/v1/analyze" \
  -H "X-API-Key: $FV_API_KEY" \
  -F "file=@/путь/видео.mov"
```

Обязательно: поле формы **`file`**, расширение **`.mov`**, размер **≤ 100 МБ**.

Успех — `202 Accepted`:

```json
{"job_id":"db9cfa97755d"}
```

`job_id` — 12 шестнадцатеричных символов. Больше ничего в ответе нет: анализ идёт в фоне,
результат забирается опросом (см. ниже). Файл можно считать принятым только после этого ответа.

## 2. Опрос результата

```sh
curl -sS "$BASE_URL/api/v1/jobs/$JOB_ID" -H "X-API-Key: $FV_API_KEY"
```

Три возможных тела, всегда `200`:

```json
{"job_id":"db9cfa97755d","status":"processing"}
```

```json
{"job_id":"db9cfa97755d","status":"done","verdict":"no_violation","violations":[]}
```

```json
{
  "job_id":"190f11d36813",
  "status":"done",
  "verdict":"violation",
  "violations":[
    {
      "type":"Предполагаемое оставление рабочего места",
      "timecode_sec": 88.5,
      "description":"краткое наблюдение и осторожная формулировка гипотезы",
      "evidence_frames":[
        {"timestamp_sec": 88.5, "image_base64":"/9j/4AAQ…"}
      ]
    }
  ]
}
```

```json
{"job_id":"87dc7dca7740","status":"failed","error":"не удалось декодировать кадры (DECODE_FAILED)"}
```

Правила разбора:

* `verdict` — ровно `violation` или `no_violation`;
* `no_violation` ⇒ `violations` пустой; `violation` ⇒ непустой (одно или несколько);
* в каждом нарушении 1–3 кадра; `image_base64` — настоящий JPEG (`data:image/jpeg;base64,` префикса нет);
  метки времени не выходят за длительность видео;
* `failed` — это окончательный ответ, а не временный: повторять опрос бессмысленно, надо отправлять заново;
* поле `error` есть только у `failed` (человекочитаемая причина на русском, код — в скобках);
* сервис может отдать `done` при первом же опросе, если клип короткий, — опрос от этого не меняется.

`processing` покрывает и «в очереди», и «в работе» (внутренний статус `queued` наружу не выходит).

### Разумные тайминги опроса

| Что | Значение |
|---|---|
| Бюджет сервиса на задачу | 280 с от **момента приёма файла** (включая ожидание в очереди) |
| Лимит жюри | 300 с |
| Измерено (короткий клип 11.6 с) | ~15 с |
| Измерено (клип 142.5 с, 28.7 МБ) | 94 с под нагрузкой (load average ~14) |

Опрашивать раз в 1–2 с; таймаут на одной стороне — 300 с.

---

## 3. Служебные эндпоинты

| Метод | Путь | Ключ | Что отдаёт |
|---|---|---|---|
| GET | `/api/v1/health` | нет | `{"status":"ok","service":"ForteVision","version":"1.0.0","queue_length":0,"worker_busy":false,"uptime_sec":432.5}` |
| GET | `/api/v1/rules` | нет | реестр правовых правил (публичный справочник, только `approved`) |
| GET | `/api/v1/jobs/{job_id}/details` | да | то же, что `/jobs/{id}`, плюс `video_duration_sec` и ссылка на правило для каждого нарушения |
| GET | `/api/v1/jobs/{job_id}/video` | да | исходное видео (поддерживает `Range`, `Accept-Ranges: bytes`) |
| GET | `/api/v1/stats` | да | счётчики задач, длина очереди, занятость воркера, последняя ошибка |

`/api/v1/health` удобно использовать как liveness-пробу: `200` + `"status":"ok"`.

---

## 4. Ошибки

| Код | Тело | Когда |
|---|---|---|
| `401` | `{"detail":"Неверный или отсутствующий X-API-Key"}` | нет заголовка `X-API-Key` или ключ неверный (проверяется до чтения тела) |
| `404` | `{"detail":"Задача не найдена"}` | неизвестный/устаревший `job_id` |
| `413` | `{"detail":"Превышен лимит загрузки (100 МБ + multipart overhead)"}` | `Content-Length` больше лимита; тело не принимается (`sent=0B`) |
| `413` | `{"detail":"Превышен лимит загрузки"}` | тело превысило лимит при chunked-загрузке без `Content-Length` |
| `415` | `{"detail":"Ожидается файл .mov"}` | расширение не `.mov` |
| `422` | `{"detail":"Пустой файл"}` | файл нулевого размера |
| `429` | `{"detail":"Очередь обработки заполнена, повторите запрос позже"}` + `Retry-After: 30` | в очереди уже `FV_MAX_QUEUE` (8) задач; повторить через 30 с |

Все пять кодов зафиксированы живыми запросами 2026-10-17.

---

## 5. Пример полного цикла (bash + python для JSON)

```sh
BASE_URL=https://<ВАШ-ДОМЕН>.ngrok-free.app
KEY=<ключ жюри>

JOB=$(curl -sS -X POST "$BASE_URL/api/v1/analyze" -H "X-API-Key: $KEY" \
        -F "file=@video.mov" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
echo "job_id=$JOB"

for i in $(seq 1 300); do
  curl -sS "$BASE_URL/api/v1/jobs/$JOB" -H "X-API-Key: $KEY" -o /tmp/job.json
  ST=$(python3 -c 'import json; print(json.load(open("/tmp/job.json"))["status"])')
  [ "$ST" = "done" ] && break
  [ "$ST" = "failed" ] && { echo "FAILED: $(cat /tmp/job.json)"; exit 1; }
  sleep 1
done
python3 -m json.tool /tmp/job.json | head -40
```

Проверка, что кадр-доказательство действительно картинка:

```sh
python3 - <<'PY'
import base64, json, io
doc = json.load(open("/tmp/job.json"))
for v in doc.get("violations", []):
    for fr in v["evidence_frames"]:
        blob = base64.b64decode(fr["image_base64"])
        assert blob[:2] == b"\xff\xd8" and blob[-2:] == b"\xff\xd9", "не JPEG"
        open(f"frame_{fr['timestamp_sec']}.jpg", "wb").write(blob)   # открывается любым просмотрщиком
PY
```

Есть готовая независимая проверка (не импортирует код приложения):

```sh
/Users/nuras/Desktop/datathon/fv-infra/infra/healthcheck.sh --validate-file /tmp/job.json
```

---

## 6. Ограничения, о которых стоит знать клиенту

* Только `.mov`. Другие контейнеры (`.mp4` и т. п.) отклоняются с `415` — это требование контракта.
* Файл больше 100 МБ не принимается (`413`), пустой — `422`.
* Задача с истёкшим бюджетом отвечает `failed` с `(JOB_TIMEOUT)`; `job_id` при этом больше не оживёт,
  нужен новый POST.
* Результат живёт 72 часа (`FV_RETENTION_HOURS`), потом удаляется вместе с кадрами; `GET` вернёт `404`.
* Порядок нарушений и кадров в ответе не гарантирован — сортировать нужно на стороне клиента
  (по `timecode_sec`).
* `verdict` относится к видео целиком, `violations[]` детализирует эпизоды; пустой список при
  `violation` невозможен.
* Сервис возвращает только признаки нарушений по правилам, у которых в реестре статус `approved`;
  всё остальное (в т. ч. нереализованные классы) не появляется в ответе вообще.
