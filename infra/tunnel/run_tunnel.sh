#!/bin/bash
# ForteVision — запуск публичного туннеля.
#
# Запускается либо вручную:
#     infra/tunnel/run_tunnel.sh                 # берёт настройки из ~/.config/forte-vision/forte-vision.env
#     FV_TUNNEL_PROVIDER=cloudflared-quick infra/tunnel/run_tunnel.sh
# либо из launchd (см. infra/macos/launchd/com.fortevision.tunnel.plist.template).
#
# Провайдеры:
#   ngrok             — основной. Бесплатный статический домен => адрес не меняется при перезапуске.
#   cloudflared-named — постоянный домен Cloudflare, если он есть у команды.
#   cloudflared-quick — отладка: случайный *.trycloudflare.com на каждый запуск (адрес МЕНЯЕТСЯ).
#
# Скрипт не регистрирует аккаунты и не хранит секретов: всё берётся из env.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../macos/lib.sh
. "$SCRIPT_DIR/../macos/lib.sh"

fv_load_env || true
fv_defaults

FV_TUNNEL_PROVIDER="${FV_TUNNEL_PROVIDER:-ngrok}"
FV_TUNNEL_UPSTREAM="${FV_TUNNEL_UPSTREAM:-http://127.0.0.1:3000}"

upstream_host="${FV_TUNNEL_UPSTREAM#*://}"
upstream_host="${upstream_host%%/*}"

fv_info "provider      : $FV_TUNNEL_PROVIDER"
fv_info "upstream      : $FV_TUNNEL_UPSTREAM"

# Предупреждаем, если upstream не отвечает: туннель поднимется, но жюри получит 502.
upstream_code="$(fv_http_code "$FV_TUNNEL_UPSTREAM/api/v1/health" 3)"
case "$upstream_code" in
  2*) fv_ok    "upstream check: HTTP $upstream_code ($FV_TUNNEL_UPSTREAM/api/v1/health)" ;;
  401|403) fv_ok "upstream check: HTTP $upstream_code (сервис жив, требует ключ)" ;;
  000) fv_warn "upstream check: НЕ ОТВЕЧАЕТ ($FV_TUNNEL_UPSTREAM). Туннель поднимется, но публичный адрес будет отдавать 502."
       fv_warn "Подними прокси/бэкенд или укажи FV_TUNNEL_UPSTREAM=http://127.0.0.1:8000" ;;
  *)   fv_warn "upstream check: HTTP $upstream_code ($FV_TUNNEL_UPSTREAM/api/v1/health)" ;;
esac

case "$FV_TUNNEL_PROVIDER" in

# ---------------------------------------------------------------------------------------
# ngrok — бесплатный статический домен (адрес постоянный)
# ---------------------------------------------------------------------------------------
ngrok)
  NGROK_BIN="$(fv_find_bin ngrok || true)"
  if [[ -z "$NGROK_BIN" ]]; then
    fv_err "ngrok не найден."
    fv_err "Установи любым из способов (нужна явная команда человека):"
    fv_err "  brew install ngrok/ngrok/ngrok"
    fv_err "  или скачай бинарь в $FV_TOOLS_DIR/bin/ngrok с https://ngrok.com/download"
    exit 1
  fi
  if [[ -z "${NGROK_AUTHTOKEN:-}" ]]; then
    fv_err "NGROK_AUTHTOKEN пуст. Получи его в кабинете ngrok и положи в $FV_ENV_FILE"
    fv_err "  https://dashboard.ngrok.com/get-started/your-authtoken"
    exit 1
  fi
  if [[ -z "${NGROK_DOMAIN:-}" ]]; then
    fv_warn "NGROK_DOMAIN пуст: ngrok выдаст СЛУЧАЙНЫЙ адрес, который изменится при перезапуске."
    fv_warn "Для стабильного адреса возьми бесплатный домен: https://dashboard.ngrok.com/domains"
    tunnel_url_args=()
  else
    # Принимаем и "team.ngrok-free.app", и "https://team.ngrok-free.app".
    domain="${NGROK_DOMAIN#http://}"; domain="${domain#https://}"; domain="${domain%%/*}"
    tunnel_url_args=(--url "https://$domain")
    fv_ok "статический домен: https://$domain"
  fi
  fv_info "log           : ~/Library/Logs/forte-vision/tunnel.out.log (если запущен через launchd)"
  fv_info "локальный API : http://${NGROK_WEB_ADDR:-127.0.0.1:4040}/api/tunnels"
  # exec — чтобы launchd видел именно процесс ngrok (KeepAlive перезапускает только его).
  # ${arr[@]+...} — идиома для bash 3.2 (macOS): пустой массив + set -u иначе даёт "unbound variable".
  exec "$NGROK_BIN" http "$FV_TUNNEL_UPSTREAM" \
      --authtoken "$NGROK_AUTHTOKEN" \
      ${tunnel_url_args[@]+"${tunnel_url_args[@]}"} \
      --log stdout --log-format logfmt --log-level info
  ;;

# ---------------------------------------------------------------------------------------
# cloudflared named tunnel — постоянный домен, который человек создал заранее
# ---------------------------------------------------------------------------------------
cloudflared-named)
  CF_BIN="$(fv_find_bin cloudflared || true)"
  if [[ -z "$CF_BIN" ]]; then
    fv_err "cloudflared не найден (brew install cloudflared или бинарь в $FV_TOOLS_DIR/bin/cloudflared)."
    exit 1
  fi
  if [[ -z "${CLOUDFLARED_TUNNEL:-}" ]]; then
    fv_err "CLOUDFLARED_TUNNEL пуст. Создай туннель: cloudflared tunnel create forte-vision"
    exit 1
  fi
  cf_args=(tunnel --no-autoupdate)
  [[ -n "${CLOUDFLARED_CONFIG:-}" ]] && cf_args+=(--config "$CLOUDFLARED_CONFIG")
  fv_info "named tunnel  : $CLOUDFLARED_TUNNEL"
  exec "$CF_BIN" "${cf_args[@]}" run "$CLOUDFLARED_TUNNEL"
  ;;

# ---------------------------------------------------------------------------------------
# cloudflared quick tunnel — без аккаунта, но адрес случайный (только отладка)
# ---------------------------------------------------------------------------------------
cloudflared-quick)
  CF_BIN="$(fv_find_bin cloudflared || true)"
  if [[ -z "$CF_BIN" ]]; then
    fv_err "cloudflared не найден (brew install cloudflared или бинарь в $FV_TOOLS_DIR/bin/cloudflared)."
    exit 1
  fi
  fv_warn "cloudflared-quick: адрес случайный и меняется при каждом перезапуске. Не для сдачи."
  exec "$CF_BIN" tunnel --no-autoupdate --url "$FV_TUNNEL_UPSTREAM" \
      --metrics "127.0.0.1:${FV_CF_METRICS_PORT:-4041}"
  ;;

*)
  fv_err "Неизвестный FV_TUNNEL_PROVIDER='$FV_TUNNEL_PROVIDER' (ожидается ngrok | cloudflared-named | cloudflared-quick)"
  exit 1
  ;;
esac
