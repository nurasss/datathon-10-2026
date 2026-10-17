#!/bin/bash
# ForteVision — снятие автозапуска macOS.
#
# Выгружает агенты из launchd и удаляет plist из ~/Library/LaunchAgents.
# По умолчанию НЕ трогает: логи, env-файл с ключами, данные сервиса (uploads/evidence/jobs.db).
#
# Использование:
#   infra/macos/uninstall.sh                 # выгрузить агенты + удалить plist
#   infra/macos/uninstall.sh --keep-plists   # только выгрузить
#   infra/macos/uninstall.sh --purge-logs    # ещё и удалить логи
#   infra/macos/uninstall.sh --purge-env     # ещё и удалить env-файл (ключи!)
#   infra/macos/uninstall.sh --dry-run
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

KEEP_PLISTS=0; PURGE_LOGS=0; PURGE_ENV=0; DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-plists) KEEP_PLISTS=1 ;;
    --purge-logs) PURGE_LOGS=1 ;;
    --purge-env) PURGE_ENV=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --agents-dir) FV_AGENTS_DIR="$2"; shift ;;
    --log-dir) FV_LOG_DIR="$2"; shift ;;
    --env-file) FV_ENV_FILE="$2"; shift ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fv_err "Неизвестный флаг: $1"; exit 2 ;;
  esac
  shift
done

export FV_INFRA_ROOT FV_ENV_FILE

# Env-файл нужно прочитать ДО первого обращения к путям: FV_AGENTS_DIR/FV_LOG_DIR
# задаются умолчаниями (set -u иначе упадёт), а env-файл вправе их переопределить.
# Порядок: файл → умолчания (см. lib.sh: fv_defaults вызывать строго после fv_load_env).
fv_load_env || true
fv_defaults

run() { if [[ $DRY_RUN -eq 1 ]]; then fv_info "  [dry-run] $*"; else "$@"; fi; }

DOMAIN="$(fv_gui_domain)"
fv_step "ForteVision: снятие автозапуска (домен $DOMAIN)"
fv_info "  env_file:   $FV_ENV_FILE$([[ -f "$FV_ENV_FILE" ]] && printf '' || printf ' (нет файла)')"
fv_info "  agents_dir: $FV_AGENTS_DIR"
fv_info "  log_dir:    $FV_LOG_DIR"
[[ $DRY_RUN -eq 1 ]] && fv_warn "  РЕЖИМ DRY-RUN"

for label in "${FV_LABELS[@]}"; do
  if fv_is_loaded "$label"; then
    if run launchctl bootout "$DOMAIN/$label" 2>/dev/null; then
      fv_ok "выгружен: $label"
    else
      fv_warn "не удалось выгрузить $label (возможно, уже выгружен)"
    fi
  else
    fv_info "не загружен: $label"
  fi
  plist="$FV_AGENTS_DIR/$label.plist"
  if [[ -f "$plist" && $KEEP_PLISTS -eq 0 ]]; then
    run rm -f "$plist" && fv_ok "удалён: $plist"
  elif [[ -f "$plist" ]]; then
    fv_info "оставлен (--keep-plists): $plist"
  fi
done

# Подчистить процессы, если что-то осталось (например, ngrok пережил bootout).
for pat in "infra/macos/run_api.sh" "infra/tunnel/run_tunnel.sh" "caffeinate -dimsu"; do
  if pgrep -f "$pat" >/dev/null 2>&1; then
    fv_warn "остался процесс по шаблону '$pat': $(pgrep -fl "$pat" | head -2 | tr '\n' ' ')"
    fv_info "  при необходимости: pkill -f '$pat'"
  fi
done

if [[ $PURGE_LOGS -eq 1 ]]; then
  [[ -d "$FV_LOG_DIR" ]] && run rm -rf "$FV_LOG_DIR" && fv_ok "удалены логи: $FV_LOG_DIR"
fi
if [[ $PURGE_ENV -eq 1 ]]; then
  if [[ -f "$FV_ENV_FILE" ]]; then
    run rm -f "$FV_ENV_FILE" && fv_ok "удалён env-файл: $FV_ENV_FILE (ключи удалены)"
  fi
fi

fv_info ""
fv_info "Данные сервиса (uploads/, evidence/, jobs.db) НЕ тронуты — они лежат в \$FV_DATA_DIR."
fv_ok "Готово."
