#!/bin/bash
# ForteVision — определить текущий публичный адрес сервиса.
#
# Порядок поиска:
#   1) FV_PUBLIC_BASE_URL из окружения/env-файла (если не плейсхолдер);
#   2) локальный API ngrok (127.0.0.1:4040) — самый надёжный источник, когда ngrok запущен;
#   3) CLOUDFLARED_PUBLIC_URL;
#   4) https://$NGROK_DOMAIN;
#   5) случайный *.trycloudflare.com из логов туннеля.
#
# Печатает адрес без завершающего слэша. Exit 1, если адрес не найден.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../macos/lib.sh
. "$SCRIPT_DIR/../macos/lib.sh"
fv_load_env || true
fv_defaults

emit() { printf '%s' "${1%/}"; exit 0; }

is_placeholder() {
  case "$1" in
    ""|*ЗАМЕНИ*|*replace-with*|*example.com*|*плэйсхолдер*) return 0 ;;
    *) return 1 ;;
  esac
}

# 1) явная настройка
if [[ -n "${FV_PUBLIC_BASE_URL:-}" ]] && ! is_placeholder "$FV_PUBLIC_BASE_URL"; then
  emit "$FV_PUBLIC_BASE_URL"
fi

# 2) локальный API ngrok
NGROK_WEB_ADDR="${NGROK_WEB_ADDR:-127.0.0.1:4040}"
if curl -s --max-time 3 "http://${NGROK_WEB_ADDR}/api/tunnels" -o /tmp/fv_tunnels.json 2>/dev/null; then
  url="$(FV_TMP_TUNNELS=/tmp/fv_tunnels.json "$(fv_python)" - <<'PY' 2>/dev/null
import json, os
try:
    doc = json.load(open(os.environ["FV_TMP_TUNNELS"]))
except Exception:
    raise SystemExit(1)
urls = [t.get("public_url", "") for t in doc.get("tunnels", [])]
pref = [u for u in urls if u.startswith("https://")] or urls
print(pref[0] if pref else "")
PY
)"
  rm -f /tmp/fv_tunnels.json
  if [[ -n "$url" ]]; then emit "$url"; fi
fi

# 3) метрик-эндпоинт cloudflared: /quicktunnel отдаёт {"hostname": "..."} — надёжнее, чем грепать лог
for port in "${FV_CF_METRICS_PORT:-4041}" 4041; do
  if curl -s --max-time 3 "http://127.0.0.1:${port}/quicktunnel" -o /tmp/fv_qt.json 2>/dev/null; then
    url="$(FV_TMP_QT=/tmp/fv_qt.json "$(fv_python)" - <<'PY' 2>/dev/null
import json, os
try:
    host = (json.load(open(os.environ["FV_TMP_QT"])) or {}).get("hostname") or ""
except Exception:
    host = ""
print(f"https://{host}" if host and host.endswith(".trycloudflare.com") else "")
PY
)"
    rm -f /tmp/fv_qt.json
    if [[ -n "$url" ]]; then emit "$url"; fi
  fi
done

# 3) явный публичный адрес для cloudflared named
if [[ -n "${CLOUDFLARED_PUBLIC_URL:-}" ]] && ! is_placeholder "$CLOUDFLARED_PUBLIC_URL"; then
  emit "$CLOUDFLARED_PUBLIC_URL"
fi

# 4) статический домен ngrok
if [[ -n "${NGROK_DOMAIN:-}" ]]; then
  d="${NGROK_DOMAIN#http://}"; d="${d#https://}"; d="${d%%/*}"
  emit "https://$d"
fi

# 6) quick tunnel: адрес печатается cloudflared в лог.
#    Смотрим и в настроенный FV_LOG_DIR, и в канонический каталог логов launchd-агента:
#    stdout туннеля пишет plist (StandardOutPath), и он не обязан совпадать с FV_LOG_DIR.
for f in "${FV_LOG_DIR:-}/tunnel.out.log" "${FV_LOG_DIR:-}/tunnel.err.log" \
         "$HOME/Library/Logs/forte-vision/tunnel.out.log" "$HOME/Library/Logs/forte-vision/tunnel.err.log"; do
  [[ -n "$f" && -f "$f" ]] || continue
  url="$(grep -Eo 'https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com' "$f" 2>/dev/null | tail -1)"
  if [[ -n "$url" ]]; then emit "$url"; fi
done

exit 1
