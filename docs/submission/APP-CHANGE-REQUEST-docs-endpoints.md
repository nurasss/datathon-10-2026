# Заявка на изменение в `apps/` — закрыть служебные эндпоинты (`/api/docs`, `/api/openapi.json`, `/redoc`)

**Кому:** участнику, который имеет право менять `apps/api/` (я — инфраструктурная ветка `feat/infra`,
код приложения не трогал).
**Зачем:** п. 4 задания — публичный адрес не должен отдавать служебную документацию и схему API.

## Факт (проверено вживую 2026-10-17)

Запущен сервер продовым способом (`scripts/run_server.sh`), запросы без ключа:

```
/docs                  404      ← закрыт (переехал)
/openapi.json          404      ← закрыт (переехал)
/redoc                 200      ← ОТКРЫТ
/api/docs              200      ← ОТКРЫТ
/api/openapi.json      200      ← ОТКРЫТ
/api/redoc             404
```

Проверено и через публичный туннель — те же коды видны снаружи.
Причина: в `main.py` заданы только `docs_url="/api/docs"` и `openapi_url="/api/openapi.json"`,
а `redoc_url` не задан, поэтому FastAPI подставляет свой дефолт `/redoc`.

## Изменение 1 (обязательное): `apps/api/app/main.py`

В начало файла (к уже существующим импортам — `os` там ещё не импортируется, потребуется добавить):

```python
import os  # если ещё не импортирован
```

Перед созданием приложения (там же, где `VERSION = "1.0.0"`):

```python
def _docs_enabled() -> bool:
    return os.environ.get("FV_ENABLE_DOCS", "0").strip().lower() in {"1", "true", "yes", "on"}
```

Заменить блок создания приложения:

```python
app = FastAPI(
    title="ForteVision — признаки нарушений кассовой дисциплины",
    version=VERSION,
    docs_url="/api/docs" if _docs_enabled() else None,
    redoc_url="/api/redoc" if _docs_enabled() else None,
    openapi_url="/api/openapi.json" if _docs_enabled() else None,
    lifespan=lifespan,
)
```

Поведение: по умолчанию (переменная не задана) все три эндпоинта дают `404`; в разработке
достаточно `FV_ENABLE_DOCS=1` — документация снова доступна по `/api/docs` и `/api/redoc`.

## Изменение 2 (обязательное, иначе в UI появится ссылка в никуда): `apps/api/app/ui.py`

Строка ~103, в `_header()`:

```python
        "<a href='/ui/health'>Статус</a><a href='/api/docs'>API</a>"
```

Вариант А (просто убрать битую ссылку):

```python
        "<a href='/ui/health'>Статус</a>"
```

Вариант Б (показывать ссылку только когда документация включена) — аккуратнее, если
в разработке она полезна:

```python
    api_link = "<a href='/api/docs'>API</a>" if os.environ.get("FV_ENABLE_DOCS", "0") in {"1", "true", "yes", "on"} else ""
    nav = (
        "<a href='/'>Загрузка</a><a href='/ui/rules'>Нормативная база</a>"
        f"<a href='/ui/health'>Статус</a>{api_link}"
    )
```

## Что НЕ надо делать

* Не блокировать эндпоинты через `.gitignore`, reverse-proxy-правила или nginx-костыли —
  в схеме развёртывания прокси это FastAPI + туннель, лишний слой только добавит точек отказа.
* Не отключать `openapi_url` только лишь затем, чтобы «спрятать» схему: без него FastAPI не сможет
  построить и `/api/docs`, поэтому выключаются все три параметра вместе (см. изменение 1).

## Проверка после правки

```sh
# локально
/Users/nuras/Desktop/datathon/fv-infra/infra/healthcheck.sh --local --strict-docs
# ожидаемо: [6/6] ... закрыты ... и итоговый PASS

# публично (когда поднят туннель)
/Users/nuras/Desktop/datathon/fv-infra/infra/healthcheck.sh --public --strict-docs
```

Без правки `healthcheck.sh` печатает `[6/6] служебные эндпоинты ОТКРЫТЫ: /redoc /api/docs /api/openapi.json`
(предупреждение, не блокирует общий результат; с `--strict-docs` — уже падение).

## Регрессия, которую стоит прогнать

```sh
cd /Users/nuras/Desktop/datathon/forte-vision && .venv/bin/python -m pytest tests/ -q
```

Тесты в `tests/` на служебные эндпоинты не ссылаются (проверено grep'ом по `openapi|redoc|api/docs`),
но `FV_API_KEYS` для прогона должен быть задан.
