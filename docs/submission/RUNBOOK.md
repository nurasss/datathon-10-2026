# ForteVision — RUNBOOK

Как поднять сервис с нуля, проверить его и что делать, если он упал.
Составлено и проверено на этом Mac 2026-10-17. Всё, что помечено «проверено», подтверждено
реальным выводом команд (см. отчёт в конце ветки).

Договорённости ветки:
* **Работает приложение** из `/Users/nuras/Desktop/datathon/forte-vision` (там же `.venv`).
* **Инфраструктура** — из `/Users/nuras/Desktop/datathon/fv-infra` (этот worktree): туннель,
  launchd-агенты, healthcheck. Код в `apps/` и `packages/` не менялся.
* Порт бэкенда **8000**, порт UI (Next.js) — **3000**. Публичный адрес даёт туннель.

---

## 0. Целевое состояние (что должно быть завтра в 12:00)

1. Публичный HTTPS-адрес вида `https://<ваш-домен>.ngrok-free.app`, **постоянный** (не меняется
   при перезапуске агента).
2. По этому адресу `POST /api/v1/analyze` с заголовком `X-API-Key` принимает `.mov` до 100 МБ и
   возвращает `job_id`, а `GET /api/v1/jobs/{job_id}` доводит его до `done` быстрее 300 с.
3. Всё поднимается само после падения/перелогина: launchd `KeepAlive` + `caffeinate -dimsu`
   (Mac не засыпает).

Ключи жюри и токен ngrok знает только человек — они лежат в `~/.config/forte-vision/forte-vision.env`
(права 600, в git не попадают).

---

## 1. Один раз: что делает человек руками

### 1.1 Аккаунт ngrok и постоянный домен (нужно ~5 минут)

1. Зарегистрироваться: <https://dashboard.ngrok.com/signup> (аккаунты не создаёт ни один скрипт).
2. Скопировать authtoken: <https://dashboard.ngrok.com/get-started/your-authtoken>.
3. Взять **бесплатный статический домен**: <https://dashboard.ngrok.com/domains> → «New Domain».
   Получится что-то вида `forte-vision-<случайное>.ngrok-free.app`. Именно он даёт неизменяемый адрес.
4. Установить агент ngrok (это системная установка — выполнять только осознанно):
   ```sh
   brew install ngrok/ngrok/ngrok
   ngrok version
   ```

### 1.2 Ключ жюри и файл окружения

```sh
mkdir -p ~/.config/forte-vision
cp /Users/nuras/Desktop/datathon/fv-infra/infra/env/forte-vision.env.example \
   ~/.config/forte-vision/forte-vision.env
chmod 600 ~/.config/forte-vision/forte-vision.env
open -e ~/.config/forte-vision/forte-vision.env      # или любой редактор
```

Заполнить минимум:

| Переменная | Что писать |
|---|---|
| `FV_API_KEYS` | случайная строка, которую вы отдадите жюри: `openssl rand -hex 24` |
| `FV_UI_KEY` | отдельный пароль для UI (можно `openssl rand -hex 12`); пусто = UI без пароля |
| `FV_SECRET` | `openssl rand -hex 32` (иначе сессии UI слетают при каждом перезапуске) |
| `NGROK_AUTHTOKEN` | из п. 1.1 |
| `NGROK_DOMAIN` | ваш статический домен **без схемы**: `xxx.ngrok-free.app` |
| `FV_TUNNEL_UPSTREAM` | `http://127.0.0.1:3000` (через Next.js) или `http://127.0.0.1:8000` (напрямую) |

`FV_APP_ROOT` и `FV_INFRA_ROOT` уже заполнены правильно; `FV_WORKER_CONCURRENCY=2` — как требует задание.

### 1.3 Установка автозапуска (одна команда)

```sh
/Users/nuras/Desktop/datathon/fv-infra/infra/macos/install.sh
```

Что делает скрипт (ничего скрытого):

* проверяет python/venv/ffmpeg/тестовое видео, наличие ngrok, заполненность ключей и домена;
* проверяет, что порт 8000 свободен, а upstream (`3000` или `8000`) **кто-то слушает** — иначе
  предупредит: публичный адрес будет отдавать 502;
* создаёт `~/Library/Logs/forte-vision/`;
* рендерит и валидирует (`plutil -lint`) три plist и кладёт их в `~/Library/LaunchAgents/`;
* загружает агенты (`launchctl bootstrap gui/$(id -u) …`);
* ждёт запуска бэкенда и туннеля, печатает публичный адрес;
* запускает `infra/healthcheck.sh --local`.

Полезные варианты:

```sh
.../install.sh --dry-run           # ничего не менять: только проверки + показать отрендеренные plist
.../install.sh --skip-tunnel       # пока нет authtoken: поднять только бэкенд и caffeinate
.../install.sh --no-load           # создать plist, но не грузить в launchd
```

Установка идемпотентна: повторный запуск перезагружает агенты.

**Порядок для команды:** сначала поднять Next.js на 3000 (если туннель смотрит на 3000),
потом запускать `install.sh`. Если Next.js ещё не готов — поставьте
`FV_TUNNEL_UPSTREAM=http://127.0.0.1:8000`, так жюри точно достучится до API, а UI можно
подключить позже сменой переменной и `launchctl kickstart -k`.

### 1.4 Проверка, что это работает

```sh
/Users/nuras/Desktop/datathon/fv-infra/infra/healthcheck.sh --both     # локально и через публичный адрес
/Users/nuras/Desktop/datathon/fv-infra/infra/healthcheck.sh --public   # только через публичный адрес
```

Ожидаемый результат: `PASS: все обязательные проверки пройдены (2 адрес(а))`, выходной код 0.

---

## 2. Ежедневное управление

```sh
FV=/Users/nuras/Desktop/datathon/fv-infra

# статус всех трёх агентов (ищите "state = running", "pid = ...")
launchctl print gui/$(id -u)/com.fortevision.api      | head -30
launchctl print gui/$(id -u)/com.fortevision.tunnel   | head -30
launchctl print gui/$(id -u)/com.fortevision.caffeinate | head -20

# логи
tail -f ~/Library/Logs/forte-vision/api.err.log         # бэкенд
tail -f ~/Library/Logs/forte-vision/tunnel.err.log      # туннель
tail -f ~/Library/Logs/forte-vision/api.out.log         # старт, фазы, доступ

# перезапуск (например, после правки env-файла) — LaunchAgent сам поднимет процесс
launchctl kickstart -k gui/$(id -u)/com.fortevision.api
launchctl kickstart -k gui/$(id -u)/com.fortevision.tunnel

# полный стоп / снятие
$FV/infra/macos/uninstall.sh                 # выгрузить и удалить plist (логи и ключи остаются)
$FV/infra/macos/uninstall.sh --keep-plists   # только выгрузить
```

Отдельный локальный запуск для отладки (в терминале, без launchd):

```sh
FV_ENV_FILE=~/.config/forte-vision/forte-vision.env $FV/infra/macos/run_api.sh
FV_ENV_FILE=~/.config/forte-vision/forte-vision.env $FV/infra/tunnel/run_tunnel.sh
```

Посмотреть текущий публичный адрес:

```sh
$FV/infra/tunnel/public_url.sh      # ngrok API → метрики cloudflared → логи → NGROK_DOMAIN
```

---

## 3. Чек-лист перед сдачей (утро, 15 минут)

1. `pmset -g | grep SleepDisabled` — и `pgrep -fl 'caffeinate -dimsu'` (не должен уходить в сон).
2. `launchctl print gui/$(id -u)/com.fortevision.api | grep -E 'state|pid'` — `running`.
3. `$FV/infra/healthcheck.sh --both` — PASS по локальному и публичному адресу.
   Либо сразу финальная команда-сводка: `$FV/infra/release_gate.sh` — она включает
   `healthcheck --both --strict-docs`, валидацию трёх сохранённых ответов, проверку ключа
   и печатает публичный адрес, время и «готово/не готово».
4. **Проверка большого файла** (обязательно, один раз):
   ```sh
   $FV/infra/healthcheck.sh --public --file "/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-14.mov"
   ```
   Это 28.7 МБ, то есть реальный сценарий жюри. Смотрите, что `[3/6]` дал `202`, а `[4/6]` — `done ≤ 300 с`.
5. Открыть публичный адрес в браузере — отвечает (у ngrok может показаться страница-заглушка
   с кнопкой «Visit site»: это его предупреждение для браузеров, API-клиентов оно не касается).
6. Убедиться, что адрес совпадает с тем, который вы отдаёте жюри, и в `.env` ничего не пропало.
7. Убедиться, что Mac не перезагрузится: LaunchAgent поднимается **при входе пользователя в систему**,
   а не до него. Если возможно включить автологин — включить.

---

## 4. Если что-то упало

| Симптом | Причина (наиболее вероятная) | Что делать |
|---|---|---|
| `порт 8000 занят` при установке | второй сервер (dev-запуск) уже висит | найти `lsof -nP -iTCP:8000 -sTCP:LISTEN`, убить лишний, повторить `install.sh` |
| Публичный адрес отдаёт **502/1033** | туннель поднят, но upstream (3000/8000) не слушает | поднять Next.js на 3000 либо поставить `FV_TUNNEL_UPSTREAM=http://127.0.0.1:8000` и `launchctl kickstart -k …tunnel` |
| `/api/v1/analyze` даёт **401 всем** | в env-файле пуст `FV_API_KEYS` или жюри дали другой ключ | сверить ключ в `~/.config/forte-vision/forte-vision.env`, перезапустить агент api |
| Задачи встают в **JOB_TIMEOUT** | ждут очереди дольше бюджета (280 с считается с момента приёма) или клип тяжёлый | поднять `FV_WORKER_CONCURRENCY` до 2 (уже 2 по умолчанию), попросить короткие клипы, смотреть `api.out.log` |
| Бэкенд циклично перезапускается | сломан env-файл или ключи | `tail -50 ~/Library/Logs/forte-vision/api.err.log`; затем `launchctl print gui/$(id -u)/com.fortevision.api` — в конце покажет код выхода и число перезапусков |
| Публичный адрес **изменился** | запущен `cloudflared-quick` или ngrok без статического домена | проверить `FV_TUNNEL_PROVIDER`/`NGROK_DOMAIN`; вернуть ngrok + статический домен, kickstart туннеля |
| Mac ушёл в сон | не загружен агент caffeinate | `launchctl print gui/$(id -u)/com.fortevision.caffeinate`; проверить, что установка прошла без ошибок |
| Задача `failed` с `OUT_OF_MEMORY` | 2 параллельных анализа при нехватке памяти | поставить `FV_WORKER_CONCURRENCY=1`, kickstart; следить за `vm_stat`/Activity Monitor |
| Задача `failed` с `DECODE_FAILED` | файл реально битый (не наша ошибка) | это корректное поведение: жюри получит `failed`, и это правильнее, чем пустой вердикт |

Диагностика «на месте»: реальный `job_id` живёт в БД `$FV_APP_ROOT/data/jobs.db`
(`sqlite3 data/jobs.db "select job_id,status,error_code from jobs order by created_at desc limit 10;"`),
отчёт задачи — `data/reports/<job_id>.json`, лог анализа — `data/reports/<job_id>.log`.

---

## 5. Резервный вариант публикации

| Вариант | Адрес постоянный? | Нужен аккаунт? | Статус |
|---|---|---|---|
| `ngrok` + бесплатный статический домен | **да** | да (человек) | основной; скрипт готов, на машине ngrok пока не установлен |
| `cloudflared` named tunnel + свой домен | **да** | домен в Cloudflare | альтернатива: `FV_TUNNEL_PROVIDER=cloudflared-named`, `CLOUDFLARED_TUNNEL=<имя>`, `CLOUDFLARED_PUBLIC_URL=https://…` |
| `cloudflared` quick tunnel | нет (случайный `*.trycloudflare.com`) | нет | **только отладка**; именно так проверялась вся транспортная часть (см. отчёт) |

---

## 6. Что НЕ проверено вживую (честно)

* Агенты launchd не загружались в систему ни разу: скрипты и plist подготовлены и провалидированы
  (`plutil -lint`), но `launchctl bootstrap` выполняет человек — по инструкции выше.
* Путь ngrok (authtoken + статический домен) не проверен: на машине нет ngrok и нет токена.
  Транспорт «публичный HTTPS → 127.0.0.1:8000» проверен на cloudflared quick tunnel, включая
  загрузку 28.7 МБ (SHA-256 совпал).
* Прокси Next.js (порт 3000) не проверялся: это код другого участника.
* Полный 5-минутный сценарий жюри (много видео подряд) не воспроизводился: это нагрузочный тест,
  его здесь сознательно не запускали.
