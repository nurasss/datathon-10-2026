#!/bin/bash
# ForteVision — установка сторожа com.fortevision.watchdog (launchd, пользовательский агент).
#
# Сторож каждые 3 минуты проверяет сервис (health=200; jobs/<несуществующий>=404) и после двух
# неудач подряд перезапускает com.fortevision.api. Код проверок — infra/watchdog/watchdog.sh.
#
# Использование (из Terminal; сначала синхронизировать инфраструктуру — RUNBOOK §2.2):
#   ~/fortevision/infra/infra/watchdog/install.sh              # установить и загрузить
#   ~/fortevision/infra/infra/watchdog/install.sh --dry-run    # только проверки + рендер plist
#   ~/fortevision/infra/infra/watchdog/install.sh --no-load    # создать plist, не грузить в launchd
#   ~/fortevision/infra/infra/watchdog/install.sh --uninstall  # выгрузить и удалить plist
#
# Флаги путей (для тестов): --agents-dir DIR --log-dir DIR --env-file FILE --infra-root DIR
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../macos/lib.sh
. "$SCRIPT_DIR/../macos/lib.sh"

DRY_RUN=0; DO_LOAD=1; UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-load) DO_LOAD=0 ;;
    --uninstall) UNINSTALL=1 ;;
    --agents-dir) FV_AGENTS_DIR="$2"; shift ;;
    --log-dir) FV_LOG_DIR="$2"; shift ;;
    --env-file) FV_ENV_FILE="$2"; shift ;;
    --infra-root) FV_INFRA_ROOT="$2"; shift ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fv_err "Неизвестный флаг: $1 (см. --help)"; exit 2 ;;
  esac
  shift
done

export FV_INFRA_ROOT FV_ENV_FILE
# Порядок как везде: сначала env-файл, потом умолчания (см. lib.sh).
fv_load_env || true
fv_defaults

LABEL="$FV_LABEL_WATCHDOG"
PLIST="$FV_AGENTS_DIR/$LABEL.plist"
TEMPLATE="$FV_INFRA_ROOT/infra/macos/launchd/$LABEL.plist.template"
WATCHDOG="$FV_INFRA_ROOT/infra/watchdog/watchdog.sh"
DOMAIN="$(fv_gui_domain)"

failures=0
check_ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
check_warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
check_fail() { printf '  %s✗%s %s\n' "$C_RED" "$C_OFF" "$1"; failures=$((failures + 1)); }

fv_step "ForteVision: сторож сервиса ($LABEL)"
fv_info "  infra_root: $FV_INFRA_ROOT"
fv_info "  env_file:   $FV_ENV_FILE"
fv_info "  agents_dir: $FV_AGENTS_DIR"
fv_info "  log_dir:    $FV_LOG_DIR"
[[ $DRY_RUN -eq 1 ]] && fv_warn "  РЕЖИМ DRY-RUN: система не изменяется"

if [[ $UNINSTALL -eq 1 ]]; then
  if fv_is_loaded "$LABEL"; then
    if [[ $DRY_RUN -eq 1 ]]; then
      fv_info "  [dry-run] launchctl bootout $DOMAIN/$LABEL"
    elif launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null; then
      fv_ok "выгружен: $LABEL"
    else
      check_fail "не удалось выгрузить $LABEL"
    fi
  else
    fv_info "не загружен: $LABEL"
  fi
  if [[ -f "$PLIST" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      fv_info "  [dry-run] rm $PLIST"
    else
      rm -f "$PLIST" && fv_ok "удалён: $PLIST"
    fi
  fi
  fv_ok "Готово (лог и счётчик не тронуты: $FV_LOG_DIR/watchdog.{log,state})"
  exit 0
fi

# --- проверки ------------------------------------------------------------------
[[ -f "$FV_ENV_FILE" ]] && check_ok "env-файл найден (ключи не печатаю)" \
  || check_fail "нет env-файла $FV_ENV_FILE"
if [[ -n "${FV_API_KEYS:-}" ]]; then
  check_ok "FV_API_KEYS задан (первым ключом проверяется ответ 404)"
else
  check_fail "FV_API_KEYS пуст — проверка jobs/<несуществующий> работать не сможет"
fi
[[ -x "$WATCHDOG" ]] && check_ok "watchdog.sh исполняем" || check_fail "нет исполняемого $WATCHDOG"
[[ -f "$TEMPLATE" ]] && check_ok "шаблон plist найден" || check_fail "нет шаблона $TEMPLATE"
if fv_is_loaded "$FV_LABEL_API"; then
  check_ok "агент api загружен (сторожу есть кого перезапускать)"
else
  check_warn "агент $FV_LABEL_API не загружен — перезапуск будет указывать в пустоту"
fi

# --- рендер plist ---------------------------------------------------------------
if [[ $DRY_RUN -eq 1 ]]; then
  RENDER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fv-watchdog.XXXXXX")"
  fv_info "  рендерю во временный каталог: $RENDER_DIR"
else
  RENDER_DIR="$FV_AGENTS_DIR"; mkdir -p "$RENDER_DIR"
fi

render() {
  sed -e "s|@FV_INFRA_ROOT@|$FV_INFRA_ROOT|g" \
      -e "s|@FV_ENV_FILE@|$FV_ENV_FILE|g" \
      -e "s|@FV_LOG_DIR@|$FV_LOG_DIR|g" \
      "$1" > "$2"
}

OUT="$RENDER_DIR/$LABEL.plist"
if render "$TEMPLATE" "$OUT"; then
  if grep -qE '@[A-Z_]+@' "$OUT"; then
    check_fail "в $OUT остались незаменённые плейсхолдеры"
  fi
  if lint_out="$(plutil -lint "$OUT" 2>&1)"; then
    check_ok "plutil -lint: $(basename "$OUT") OK"
  else
    check_fail "plutil -lint: $(basename "$OUT") — $lint_out"
  fi
else
  check_fail "не удалось отрендерить $TEMPLATE"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  fv_step "DRY-RUN: содержимое отрендеренного plist"
  grep -vE '^\s*(<!--|-->)' "$OUT" 2>/dev/null | sed '/^\s*$/d' | head -40
  fv_info ""
  fv_info "DRY-RUN завершён. Ошибок: $failures"
  [[ $failures -eq 0 ]] || exit 1
  exit 0
fi

if (( failures > 0 )); then
  fv_err ""
  fv_err "Установка прервана: не пройдено проверок — $failures. Исправь и запусти снова."
  exit 1
fi

# --- загрузка в launchd ----------------------------------------------------------
fv_step "Загрузка агента"
mkdir -p "$FV_LOG_DIR" || true
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true   # идемпотентность
if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then
  launchctl enable "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  check_ok "загружен: $LABEL"
else
  check_fail "не удалось загрузить (launchctl bootstrap $DOMAIN $PLIST)"
  exit 1
fi

if [[ $DO_LOAD -eq 0 ]]; then
  fv_warn "--no-load: plist создан, но в launchd не загружен. Загрузить: launchctl bootstrap $DOMAIN $PLIST"
  exit 0
fi

# --- немедленная проверка --------------------------------------------------------
sleep 2
fv_step "Первая проверка сторожа (не ждать 3 минуты)"
if bash "$WATCHDOG"; then
  check_ok "сторож: обе проверки пройдены"
else
  check_warn "сторож: первая проверка с неудачей — смотрите хвост лога ниже (перезапуск будет после второй)"
fi

fv_step "Состояние агента"
launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E 'state =|last exit code' | sed 's/^/  /' || true
tail -5 "$FV_LOG_DIR/watchdog.log" 2>/dev/null | sed 's/^/  /' || true

if (( failures > 0 )); then
  fv_err ""
  fv_err "Установка завершена с ошибками ($failures)."
  exit 1
fi
fv_ok ""
fv_ok "ГОТОВО: сторож установлен и загружен."
exit 0
