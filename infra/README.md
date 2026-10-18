# fv-infra — инфраструктура ForteVision

Тонкий слой вокруг приложения из `../forte-vision`: публикация наружу, автозапуск на macOS,
end-to-end проверка и документы для сдачи. **Код приложения здесь не меняется.**

## Карта файлов

```
infra/
├── env/forte-vision.env.example     единственный файл настроек и секретов (копируется в ~/.config)
├── deploy.sh                        выкат продовой копии (~/fortevision/app): rsync, зависимости, kickstart, откат
├── healthcheck.sh                   проверка «как жюри»: 401, POST, опрос, контракт JSON, JPEG
├── release_gate.sh                  финальная проверка перед сдачей (см. ниже)
├── macos/
│   ├── lib.sh                       общие переменные/функции для всех скриптов
│   ├── run_api.sh                   запуск бэкенда для launchd (PATH, env-файл, продовые дефолты)
│   ├── install.sh                   установка трёх launchd-агентов + проверка
│   ├── uninstall.sh                 снятие агентов (включая сторожа)
│   └── launchd/*.plist.template     шаблоны: api, tunnel, caffeinate, watchdog
├── watchdog/
│   ├── watchdog.sh                  сторож: health=200 и jobs/<несуществующий>=404; после 2 неудач — kickstart api
│   └── install.sh                   установка/снятие агента com.fortevision.watchdog
└── tunnel/
    ├── run_tunnel.sh                ngrok | cloudflared-named | cloudflared-quick
    └── public_url.sh                где сейчас публичный адрес (ngrok API, метрики cloudflared, логи)

docs/submission/
├── RUNBOOK.md                       что человек делает руками: от нуля до проверки
├── API.md                           контракт и примеры curl (адрес-плейсхолдер)
├── ARCHITECTURE.md                  как устроен сервис по факту кода
├── NORMS.md                         акты и дословные нормы из чернового реестра куратора
├── TYPES.md                         восемь типов нарушений + ограничения и метрики
└── APP-CHANGE-REQUEST-docs-endpoints.md   точная правка для apps/api (закрыть /api/docs, /redoc)
```

## Release gate (перед сдачей)

`infra/release_gate.sh` — одна команда, которая говорит «готово / не готово»:

```sh
$B/infra/release_gate.sh            # локально + через публичный адрес, строго к /docs
$B/infra/release_gate.sh --local    # предварительно, без туннеля
```

Проверяет: ключ жюри (заполнен, длинный, не равен заглушке из `.env.example`, не утёк в файлы ветки) →
три сохранённых ответа (короткий/длинный/с нарушением) по контракту → `healthcheck --both --strict-docs`
на коротком ролике → `--selftest` валидатора. Печатает публичный адрес, время и итог. Нагрузочных
тестов внутри нет. Всё read-only.

## Выкат новой версии (deploy.sh)

`infra/deploy.sh` обновляет продовую копию приложения в `~/fortevision/app` (launchd-агенты не могут
читать `~/Desktop` из-за TCC, поэтому прод живёт в домашнем каталоге; запускает выкат **человек из
Terminal**):

```sh
B=/Users/nuras/Desktop/datathon/fv-infra

$B/infra/deploy.sh --branch integ --dry-run   # план и diff, ничего не трогает
$B/infra/deploy.sh --branch integ             # снимок → rsync → зависимости → kickstart → healthcheck --public
$B/infra/deploy.sh --rollback                 # откат из ~/fortevision/app.prev
```

Зависимости обновляются в `~/fortevision/app/.venv` (через `uv pip install -r`) только если изменился
`apps/api/requirements.txt`. Перед выкатом проверяется запас места на диске (минимум 1000 МБ): полный
диск уже ронял SQLite в проде. Живые `data/`, `artifacts/`, `.env` и `.venv` не трогаются, предыдущая
версия кода сохраняется в `app.prev`.

## Быстрые команды

```sh
B=/Users/nuras/Desktop/datathon/fv-infra

$B/infra/macos/install.sh --dry-run          # только проверки, ничего не ставит
$B/infra/macos/install.sh                    # установить и запустить (делает человек)
$B/infra/watchdog/install.sh --dry-run       # сторож: проверки + рендер plist (боевая установка — из ~/fortevision/infra, RUNBOOK §2.2)
$B/infra/healthcheck.sh --both               # проверить локально и через публичный адрес
$B/infra/healthcheck.sh --selftest           # проверить сам валидатор ответов (11 негативных кейсов)
$B/infra/healthcheck.sh --validate-file f.json   # проверить сохранённый ответ по контракту
$B/infra/release_gate.sh                     # финальное «готово/не готово» перед сдачей
$B/infra/macos/uninstall.sh                  # снять автозапуск
```

## Что загружается в launchd

| Агент | Что делает | Политика |
|---|---|---|
| `com.fortevision.api` | `infra/macos/run_api.sh` → `forte-vision/scripts/run_server.sh` (uvicorn, `0.0.0.0:8000`, `FV_WORKER_CONCURRENCY=2`, таймаут 280 с) | `RunAtLoad`, `KeepAlive`, `ThrottleInterval=10` |
| `com.fortevision.tunnel` | `infra/tunnel/run_tunnel.sh` (ngrok со статическим доменом) | то же |
| `com.fortevision.caffeinate` | `/usr/bin/caffeinate -dimsu` | то же; не даёт Mac уснуть |
| `com.fortevision.watchdog` | `infra/watchdog/watchdog.sh` каждые 3 мин: `health=200`, `jobs/<несуществующий>=404`; после 2 неудач подряд — `launchctl kickstart -k` агента api и запись в лог | `RunAtLoad`, `StartInterval=180` |

Логи: `~/Library/Logs/forte-vision/{api,tunnel,caffeinate,watchdog}.{out,err}.log`; у сторожа ещё
`watchdog.log` — только события: сбой, восстановление, перезапуск.

## Локальные инструменты

`infra/.tools/bin/` — сюда можно положить `ngrok` или `cloudflared` бинарём, без установки в систему
(каталог в `.gitignore`). Скрипты ищут бинарь сначала там, потом в `PATH` и в `/usr/local/bin`.
Ничего в системе эта папка не меняет и не требует прав root.

## Границы ответственности

* `ngrok`/`cloudflared`, аккаунт и токены — на стороне человека (см. RUNBOOK §1).
* `apps/` и `packages/` в `forte-vision` — чужая зона; мои замечания к ним оформлены заявкой в
  `docs/submission/APP-CHANGE-REQUEST-docs-endpoints.md`.
* Скрипты идемпотентны: повторный `install.sh` перезагружает агенты, `uninstall.sh` можно запускать
  когда угодно (логи, env-файл и данные сервиса он не удаляет).
