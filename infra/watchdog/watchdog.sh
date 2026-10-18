#!/bin/bash
# ForteVision — сторож сервиса для launchd (ставится infra/watchdog/install.sh).
#
# Запускается каждые 3 минуты (StartInterval=180) и проверяет живой сервис:
#   1) GET /api/v1/health                    — ожидается 200;
#   2) GET /api/v1/jobs/<несуществующий id>  — ожидается 404 (500 или таймаут = регрессия).
# Вторая проверка идёт с ключом жюри (первый из FV_API_KEYS); сам ключ нигде не печатается.
#
# После FV_WATCHDOG_THRESHOLD (по умолчанию 2) неудач подряд:
#   launchctl kickstart -k gui/<uid>/com.fortevision.api, запись в лог, счётчик сбрасывается.
#
# Лог растёт только на событиях (сбой / восстановление / перезапуск), без рутинных «ОК».
#   Лог:     ~/Library/Logs/forte-vision/watchdog.log
#   Счётчик: ~/Library/Logs/forte-vision/watchdog.state
#
# Переопределяемые переменные (окружение / env-файл / тесты):
#   FV_WATCHDOG_URL            базовый URL (по умолчанию FV_BACKEND_LOCAL_URL)
#   FV_WATCHDOG_THRESHOLD      неудач подряд до перезапуска (2)
#   FV_WATCHDOG_LOG            файл лога
#   FV_WATCHDOG_STATE          файл счётчика
#   FV_WATCHDOG_KICKSTART_CMD  команда перезапуска (в тестах подменяется, например на echo)
#   FV_WATCHDOG_PROBE_ID       id для проверки 404 (watchdog-probe-nonexistent)
#   FV_WATCHDOG_TIMEOUT        таймаут одного curl, секунды (10)
#
# Код возврата: 0 — обе проверки прошли; 1 — были неудачи (launchd код не использует).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../macos/lib.sh
. "$SCRIPT_DIR/../macos/lib.sh"

fv_load_env || true
fv_defaults

FV_WATCHDOG_URL="${FV_WATCHDOG_URL:-$FV_BACKEND_LOCAL_URL}"
FV_WATCHDOG_THRESHOLD="${FV_WATCHDOG_THRESHOLD:-2}"
FV_WATCHDOG_LOG="${FV_WATCHDOG_LOG:-$FV_LOG_DIR/watchdog.log}"
FV_WATCHDOG_STATE="${FV_WATCHDOG_STATE:-$FV_LOG_DIR/watchdog.state}"
FV_WATCHDOG_PROBE_ID="${FV_WATCHDOG_PROBE_ID:-watchdog-probe-nonexistent}"
FV_WATCHDOG_TIMEOUT="${FV_WATCHDOG_TIMEOUT:-10}"
FV_WATCHDOG_KICKSTART_CMD="${FV_WATCHDOG_KICKSTART_CMD:-launchctl kickstart -k gui/$(id -u)/$FV_LABEL_API}"

mkdir -p "$(dirname "$FV_WATCHDOG_LOG")" 2>/dev/null || true

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$FV_WATCHDOG_LOG"; }
read_fails() {  # не пустой, не цифра или отсутствие файла -> 0
  local v
  v="$(cat "$FV_WATCHDOG_STATE" 2>/dev/null || true)"
  case "$v" in ''|*[!0-9]*) printf '0' ;; *) printf '%s' "$v" ;; esac
}
write_fails() { printf '%s' "$1" > "$FV_WATCHDOG_STATE" 2>/dev/null || true; }

# --- проверка 1: health -------------------------------------------------------
health_code="$(fv_http_code "$FV_WATCHDOG_URL/api/v1/health" "$FV_WATCHDOG_TIMEOUT")"
health_ok=0; [[ "$health_code" == "200" ]] && health_ok=1

# --- проверка 2: несуществующий job -> 404 ------------------------------------
jobs_code="no-key"; jobs_ok=0
key="${FV_API_KEYS:-}"; key="${key%%,*}"
if [[ -n "$key" ]]; then
  jobs_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$FV_WATCHDOG_TIMEOUT" \
    -H "X-API-Key: $key" "$FV_WATCHDOG_URL/api/v1/jobs/$FV_WATCHDOG_PROBE_ID" 2>/dev/null)"
  jobs_code="${jobs_code:0:3}"; jobs_code="${jobs_code:-000}"
  [[ "$jobs_code" == "404" ]] && jobs_ok=1
fi

# --- решение ------------------------------------------------------------------
if [[ $health_ok -eq 1 && $jobs_ok -eq 1 ]]; then
  prev="$(read_fails)"
  if [[ ! -f "$FV_WATCHDOG_STATE" ]]; then
    log "OK: сторож запущен, проверки пройдены (health=200, jobs=404)"
  elif [[ "$prev" != "0" ]]; then
    log "OK: сервис восстановился после ${prev} неудач(и) подряд (health=200, jobs=404)"
  fi
  write_fails 0
  exit 0
fi

fails=$(( $(read_fails) + 1 ))
if (( fails >= FV_WATCHDOG_THRESHOLD )); then
  log "FAIL ${fails}/${FV_WATCHDOG_THRESHOLD}: health=$health_code, jobs=$jobs_code -> перезапуск: $FV_WATCHDOG_KICKSTART_CMD"
  rc=0
  $FV_WATCHDOG_KICKSTART_CMD >>"$FV_WATCHDOG_LOG" 2>&1 || rc=$?
  if [[ $rc -eq 0 ]]; then
    log "kickstart выполнен, счётчик неудач сброшен (следующая проверка через ~3 мин)"
  else
    log "kickstart НЕ выполнился (rc=$rc) — нужен человек: launchctl print gui/$(id -u)/$FV_LABEL_API"
  fi
  write_fails 0
else
  log "FAIL ${fails}/${FV_WATCHDOG_THRESHOLD}: health=$health_code, jobs=$jobs_code (перезапуск после следующей неудачи)"
  write_fails "$fails"
fi
exit 1
