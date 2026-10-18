# ForteVision — данные для сдачи (Datathon 26, ForteBank)

## Адреса
- **Веб-интерфейс:** https://craig-taxaceous-marylouise.ngrok-free.dev
  (бесплатный ngrok при первом заходе показывает страницу-предупреждение — нажать «Visit Site»)
- **API:** https://craig-taxaceous-marylouise.ngrok-free.dev/api/v1/analyze (POST, поле `file`, .mov)
  и https://craig-taxaceous-marylouise.ngrok-free.dev/api/v1/jobs/{job_id} (GET)
- **Ключи** (не публиковать, передать только жюри) — в `~/.config/forte-vision/forte-vision.env`:
  - `FV_API_KEYS` — ключ API для заголовка `X-API-Key`;
  - `FV_UI_KEY` — ключ входа в веб-интерфейс.

```bash
grep -E "^FV_(API_KEYS|UI_KEY)=" ~/.config/forte-vision/forte-vision.env
```

## Пример запроса
```bash
curl -X POST https://craig-taxaceous-marylouise.ngrok-free.dev/api/v1/analyze \
     -H "X-API-Key: <ключ>" -F "file=@video.mov"
curl https://craig-taxaceous-marylouise.ngrok-free.dev/api/v1/jobs/<job_id> -H "X-API-Key: <ключ>"
```

## Код и документы
- GitHub: https://github.com/nurasss/datathon-10-2026 — ветка `backend` (сервис), ветка `frontend` (README, UI-прототип).
- Документы для жюри (§8 кейса): `docs/submission/` — RUNBOOK (запуск), API, ARCHITECTURE (архитектура и модели),
  TYPES (типы нарушений, метрики, ограничения и известные ошибки), NORMS (нормативные источники и принцип сопоставления).

## Пока идёт проверка жюри
- Mac включён, на зарядке, крышка открыта, интернет стабилен.
- Агенты GPT / Gemini / DeepSeek остановлены (нагрузка CPU замедляет обработку).
- Сервис, туннель, caffeinate и сторож работают через launchd сами; логи: `~/Library/Logs/forte-vision/`.
- Быстрая проверка, что всё живо:
```bash
~/fortevision/infra/infra/healthcheck.sh --public
```
- Откат версии, если что-то пошло не так:
```bash
/Users/nuras/Desktop/datathon/forte-vision/infra/deploy.sh --rollback
```

## После хакатона
- Сменить authtoken ngrok (он был в чате) и удалить тестовые ключи.
