#!/bin/bash
# ForteVision — установка автозапуска на macOS (launchd, пользовательские агенты).
#
# Ставит три агента в ~/Library/LaunchAgents:
#   com.fortevision.api         — бэкенд FastAPI на 127.0.0.1:8000 (KeepAlive)
#   com.fortevision.tunnel      — публичный туннель ngrok/cloudflared (KeepAlive)
#   com.fortevision.caffeinate  — не даёт Mac уснуть (caffeinate -dimsu)
#
# НИЧЕГО не делает молча: сначала печатает план, проверки и только потом ставит.
# Ничего не требует от root: всё в пользовательском домене launchd.
#
# Использование:
#   infra/macos/install.sh                     # полная установка + проверка
#   infra/macos/install.sh --dry-run           # только проверки и рендер plist во временный каталог
#   infra/macos/install.sh --no-load           # создать plist, но не загружать в launchd
#   infra/macos/install.sh --skip-tunnel       # без агента туннеля (например, до получения authtoken)
#   infra/macos/install.sh --allow-dead-upstream
#
# Флаги путей (для тестов): --agents-dir DIR --log-dir DIR --env-file FILE --app-root DIR
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

DRY_RUN=0; DO_LOAD=1; SKIP_TUNNEL=0; DO_HEALTHCHECK=1; ALLOW_DEAD_UPSTREAM=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-load) DO_LOAD=0 ;;
    --skip-tunnel) SKIP_TUNNEL=1 ;;
    --no-healthcheck) DO_HEALTHCHECK=0 ;;
    --allow-dead-upstream) ALLOW_DEAD_UPSTREAM=1 ;;
    --agents-dir) FV_AGENTS_DIR="$2"; shift ;;
    --log-dir) FV_LOG_DIR="$2"; shift ;;
    --env-file) FV_ENV_FILE="$2"; shift ;;
    --app-root) FV_APP_ROOT="$2"; shift ;;
    --infra-root) FV_INFRA_ROOT="$2"; shift ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fv_err "Неизвестный флаг: $1 (см. --help)"; exit 2 ;;
  esac
  shift
done

export FV_INFRA_ROOT FV_ENV_FILE

# Про пути намеренно здесь не печатаю: env-файл вправе переопределить
# FV_APP_ROOT / FV_LOG_DIR / FV_AGENTS_DIR — список путей печатается ниже,
# уже после чтения env-файла (шаг 0).
fv_step "ForteVision: установка автозапуска"
[[ $DRY_RUN -eq 1 ]] && fv_warn "  РЕЖИМ DRY-RUN: система не изменяется"

failures=0
warn_count=0
check_ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
check_warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; warn_count=$((warn_count + 1)); }
check_fail() { printf '  %s✗%s %s\n' "$C_RED" "$C_OFF" "$1"; failures=$((failures + 1)); }

# =======================================================================================
fv_step "0. Env-файл и секреты"
# =======================================================================================
ENV_TEMPLATE="$FV_INFRA_ROOT/infra/env/forte-vision.env.example"
if [[ ! -f "$FV_ENV_FILE" ]]; then
  if [[ ! -f "$ENV_TEMPLATE" ]]; then
    check_fail "нет ни $FV_ENV_FILE, ни шаблона $ENV_TEMPLATE"
  elif [[ $DRY_RUN -eq 1 ]]; then
    check_warn "env-файл отсутствует (в dry-run не создаю): $FV_ENV_FILE"
  else
    mkdir -p "$(dirname "$FV_ENV_FILE")"
    cp "$ENV_TEMPLATE" "$FV_ENV_FILE"
    chmod 600 "$FV_ENV_FILE"
    fv_ok "создан $FV_ENV_FILE из шаблона (chmod 600). Дальше нужно вписать ключи!"
    check_warn "заполни $FV_ENV_FILE: FV_API_KEYS, NGROK_AUTHTOKEN, NGROK_DOMAIN"
  fi
fi

# Порядок принципиален: сначала читаем env-файл (он дополняет уже заданные переменные),
# и только потом заполняем умолчания — иначе значения из файла (например
# FV_TUNNEL_UPSTREAM, FV_LOG_DIR, FV_AGENTS_DIR) не переопределят умолчания.
fv_load_env || true
fv_defaults

fv_info "  infra_root: $FV_INFRA_ROOT"
fv_info "  app_root:   $FV_APP_ROOT"
fv_info "  env_file:   $FV_ENV_FILE"
fv_info "  agents_dir: $FV_AGENTS_DIR"
fv_info "  log_dir:    $FV_LOG_DIR"

if [[ -f "$FV_ENV_FILE" ]]; then
  perms="$(stat -f '%Lp' "$FV_ENV_FILE" 2>/dev/null || echo '?')"
  if [[ "$perms" != "600" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      check_warn "права на env-файл $perms (нужно 600)"
    else
      chmod 600 "$FV_ENV_FILE" && check_ok "права env-файла исправлены на 600 (было $perms)"
    fi
  else
    check_ok "права env-файла: 600"
  fi
fi

if [[ -z "${FV_API_KEYS:-}" || "${FV_API_KEYS:-}" == *ЗАМЕНИ* ]]; then
  check_fail "FV_API_KEYS не заполнен — публичный API будет закрыт, жюри получит 401. Заполни $FV_ENV_FILE"
else
  check_ok "FV_API_KEYS задан ($(printf '%s' "$FV_API_KEYS" | awk -F, '{print NF}') шт., значение не печатаю)"
fi

if [[ -z "${FV_UI_KEY:-}" ]]; then
  check_warn "FV_UI_KEY пуст: веб-UI на публичном адресе открыт без пароля"
else
  check_ok "FV_UI_KEY задан (UI под логином)"
fi

if [[ -z "${FV_SECRET:-}" ]]; then
  if [[ $DRY_RUN -eq 0 && -f "$FV_ENV_FILE" ]]; then
    secret="$(/usr/bin/openssl rand -hex 32 2>/dev/null || true)"
    if [[ -n "$secret" ]]; then
      printf '\n# добавлено install.sh %s\nFV_SECRET=%s\n' "$(date '+%Y-%m-%d')" "$secret" >> "$FV_ENV_FILE"
      FV_SECRET="$secret"; export FV_SECRET
      check_ok "FV_SECRET сгенерирован и дописан в env-файл (сессии UI переживут перезапуск)"
    else
      check_warn "не удалось сгенерировать FV_SECRET"
    fi
  else
    check_warn "FV_SECRET пуст: UI-сессии будут сбрасываться при каждом перезапуске"
  fi
else
  check_ok "FV_SECRET задан"
fi

# =======================================================================================
fv_step "1. Код сервиса и интерпретатор"
# =======================================================================================
if [[ -x "$FV_APP_ROOT/scripts/run_server.sh" ]]; then
  check_ok "scripts/run_server.sh найден"
else
  check_fail "нет $FV_APP_ROOT/scripts/run_server.sh (проверь --app-root / FV_APP_ROOT)"
fi

PY="$(fv_python || true)"
if [[ -n "$PY" && -x "$PY" ]]; then
  pyver="$("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo '?')"
  check_ok "python: $PY ($pyver)"
  if "$PY" -c 'import fastapi, uvicorn, cv2' 2>/dev/null; then
    check_ok "зависимости приложения импортируются (fastapi, uvicorn, cv2)"
  else
    check_fail "в venv нет зависимостей: $PY -m pip install -r $FV_APP_ROOT/apps/api/requirements.txt"
  fi
else
  check_fail "не найден python (ожидался $FV_APP_ROOT/.venv/bin/python)"
fi

for bin in ffmpeg ffprobe; do
  if command -v "$bin" >/dev/null 2>&1 || [[ -x "/usr/local/bin/$bin" ]]; then
    check_ok "$bin: $("/usr/local/bin/$bin" -version 2>/dev/null | head -1 | cut -c1-40)"
  else
    check_warn "$bin не найден: декодирование уйдёт в фолбэк OpenCV, поведение изменится"
  fi
done

HC_FILE="${FV_HEALTHCHECK_FILE:-/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov}"
if [[ -f "$HC_FILE" ]]; then
  check_ok "тестовое видео: $HC_FILE ($(du -m "$HC_FILE" | cut -f1) МБ)"
else
  check_warn "не найдено тестовое видео $HC_FILE (healthcheck не сможет проверить POST)"
fi

# =======================================================================================
fv_step "2. Туннель"
# =======================================================================================
PROVIDER="${FV_TUNNEL_PROVIDER:-ngrok}"
UPSTREAM="${FV_TUNNEL_UPSTREAM:-http://127.0.0.1:3000}"
fv_info "  провайдер: $PROVIDER, upstream: $UPSTREAM"

case "$PROVIDER" in
  ngrok)
    if NGROK_BIN="$(fv_find_bin ngrok)"; then
      check_ok "ngrok найден: $NGROK_BIN ($("$NGROK_BIN" version 2>/dev/null | head -1))"
    else
      check_fail "ngrok не установлен. Человек должен выполнить: brew install ngrok/ngrok/ngrok"
    fi
    if [[ -n "${NGROK_AUTHTOKEN:-}" ]]; then
      check_ok "NGROK_AUTHTOKEN задан (не печатаю)"
    else
      check_fail "NGROK_AUTHTOKEN пуст: получи на https://dashboard.ngrok.com/get-started/your-authtoken"
    fi
    if [[ -n "${NGROK_DOMAIN:-}" ]]; then
      check_ok "NGROK_DOMAIN: $NGROK_DOMAIN (адрес не изменится при перезапуске)"
    else
      check_fail "NGROK_DOMAIN пуст: возьми бесплатный статический домен на https://dashboard.ngrok.com/domains"
    fi
    ;;
  cloudflared-named)
    if CF_BIN="$(fv_find_bin cloudflared)"; then
      check_ok "cloudflared найден: $CF_BIN"
    else
      check_fail "cloudflared не установлен (brew install cloudflared или бинарь в infra/.tools/bin/)"
    fi
    if [[ -n "${CLOUDFLARED_TUNNEL:-}" ]]; then
      check_ok "CLOUDFLARED_TUNNEL: $CLOUDFLARED_TUNNEL"
    else
      check_fail "CLOUDFLARED_TUNNEL пуст (cloudflared tunnel create <имя>)"
    fi
    [[ -n "${CLOUDFLARED_PUBLIC_URL:-}" ]] && check_ok "CLOUDFLARED_PUBLIC_URL: $CLOUDFLARED_PUBLIC_URL" \
      || check_warn "CLOUDFLARED_PUBLIC_URL пуст: healthcheck не сможет проверить публичный адрес"
    ;;
  cloudflared-quick)
    CF_BIN="$(fv_find_bin cloudflared || true)"
    [[ -n "$CF_BIN" ]] && check_ok "cloudflared найден: $CF_BIN" || check_fail "cloudflared не установлен"
    check_warn "cloudflared-quick даёт СЛУЧАЙНЫЙ адрес на каждый перезапуск — только для отладки"
    ;;
  *) check_fail "неизвестный FV_TUNNEL_PROVIDER='$PROVIDER'" ;;
esac

upstream_host="${UPSTREAM#*://}"; upstream_host="${upstream_host%%/*}"
upstream_port="${upstream_host##*:}"
if [[ "$upstream_port" =~ ^[0-9]+$ ]]; then
  if lsof -nP -iTCP:"$upstream_port" -sTCP:LISTEN >/dev/null 2>&1; then
    check_ok "upstream слушает: $UPSTREAM (порт $upstream_port занят слушателем)"
  else
    if [[ $ALLOW_DEAD_UPSTREAM -eq 1 ]]; then
      check_warn "на порту $upstream_port никто не слушает (разрешено флагом)"
    else
      check_fail "на порту $upstream_port никто не слушает — публичный адрес будет отдавать 502!"
      fv_info  "    Варианты: (а) поднять Next.js на 3000;"
      fv_info  "              (б) FV_TUNNEL_UPSTREAM=http://127.0.0.1:8000 (напрямую в FastAPI);"
      fv_info  "              (в) осознанно: --allow-dead-upstream"
    fi
  fi
else
  check_warn "не удалось разобрать порт из upstream '$UPSTREAM'"
fi

# =======================================================================================
fv_step "3. Порты бэкенда и прокси"
# =======================================================================================
if lsof -nP -iTCP:"$FV_BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  owner="$(lsof -nP -iTCP:"$FV_BACKEND_PORT" -sTCP:LISTEN -Fc 2>/dev/null | tail -1 | tr -d 'c')"
  if [[ "$owner" == "python" || "$owner" == "uvicorn" ]]; then
    check_warn "порт $FV_BACKEND_PORT уже занят ($owner) — возможно, бэкенд уже запущен. KeepAlive подхватит только свой процесс."
  else
    check_fail "порт $FV_BACKEND_PORT занят процессом '$owner' — агент не сможет стартовать"
  fi
else
  check_ok "порт $FV_BACKEND_PORT свободен"
fi

# =======================================================================================
fv_step "4. Рендер plist и проверка синтаксиса"
# =======================================================================================
if [[ $DRY_RUN -eq 1 ]]; then
  RENDER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fv-plists.XXXXXX")"
  fv_info "  рендерю во временный каталог: $RENDER_DIR"
else
  RENDER_DIR="$FV_AGENTS_DIR"
fi

render() {  # render <template> <target>
  sed -e "s|@FV_INFRA_ROOT@|$FV_INFRA_ROOT|g" \
      -e "s|@FV_APP_ROOT@|$FV_APP_ROOT|g" \
      -e "s|@FV_ENV_FILE@|$FV_ENV_FILE|g" \
      -e "s|@FV_LOG_DIR@|$FV_LOG_DIR|g" \
      "$1" > "$2"
}

TEMPLATES_DIR="$FV_INFRA_ROOT/infra/macos/launchd"
rendered=()
for t in api tunnel caffeinate; do
  tmpl="$TEMPLATES_DIR/com.fortevision.$t.plist.template"
  out="$RENDER_DIR/com.fortevision.$t.plist"
  if [[ ! -f "$tmpl" ]]; then check_fail "нет шаблона $tmpl"; continue; fi
  if [[ $SKIP_TUNNEL -eq 1 && "$t" == "tunnel" ]]; then
    fv_info "  пропускаю tunnel (--skip-tunnel)"; continue
  fi
  if [[ $DRY_RUN -eq 0 ]]; then mkdir -p "$RENDER_DIR"; fi
  render "$tmpl" "$out" || { check_fail "не удалось отрендерить $tmpl"; continue; }

  if grep -qE '@[A-Z_]+@' "$out"; then
    check_fail "$out contains незаменённые плейсхолдеры"
  fi
  if lint_out="$(plutil -lint "$out" 2>&1)"; then
    check_ok "plutil -lint: $(basename "$out") OK"
  else
    check_fail "plutil -lint: $(basename "$out") — $lint_out"
  fi
  rendered+=("$out")
done

# Путь к env-файлу и скриптам в plist должен существовать, иначе агент будет падать в цикле.
[[ -f "$FV_ENV_FILE" ]] || check_warn "env-файл $FV_ENV_FILE не существует (агент запустится, но без ключей)"
[[ -x "$FV_APP_ROOT/scripts/run_server.sh" ]] || check_warn "run_server.sh не исполняем"

if [[ $DRY_RUN -eq 1 ]]; then
  fv_step "DRY-RUN: содержимое отрендеренных plist"
  for f in "${rendered[@]}"; do
    printf '\n%s--- %s ---%s\n' "$C_DIM" "$f" "$C_OFF"
    grep -vE '^\s*(<!--|-->)' "$f" | sed '/^\s*$/d' | head -40
  done
  fv_info ""
  fv_info "DRY-RUN завершён. Ошибок: $failures, предупреждений: $warn_count"
  [[ $failures -eq 0 ]] || exit 1
  exit 0
fi

if [[ $failures -gt 0 ]]; then
  fv_err ""
  fv_err "Установка прервана: не пройдено проверок — $failures. Исправь их и запусти снова."
  exit 1
fi

# =======================================================================================
fv_step "5. Логи и загрузка агентов в launchd"
# =======================================================================================
mkdir -p "$FV_LOG_DIR" && check_ok "каталог логов: $FV_LOG_DIR"
mkdir -p "$FV_AGENTS_DIR" && check_ok "каталог агентов: $FV_AGENTS_DIR"

if [[ $DO_LOAD -eq 0 ]]; then
  fv_warn "Ключ --no-load: plist созданы, но в launchd НЕ загружены. Загрузить: launchctl bootstrap gui/\$(id -u) <plist>"
  exit 0
fi

DOMAIN="$(fv_gui_domain)"
for f in "${rendered[@]}"; do
  label="$(basename "$f" .plist)"
  launchctl bootout "$DOMAIN/$label" >/dev/null 2>&1 || true   # идемпотентность
  if launchctl bootstrap "$DOMAIN" "$f" 2>/dev/null; then
    launchctl enable "$DOMAIN/$label" >/dev/null 2>&1 || true
    check_ok "загружен: $label"
  else
    check_fail "не удалось загрузить $label (launchctl bootstrap $DOMAIN $f)"
  fi
done

# =======================================================================================
fv_step "6. Проверка, что сервис поднялся"
# =======================================================================================
if fv_wait_http_ok "$FV_BACKEND_LOCAL_URL/api/v1/health" 90; then
  check_ok "бэкенд отвечает: $(curl -s --max-time 5 "$FV_BACKEND_LOCAL_URL/api/v1/health")"
else
  check_fail "бэкенд не ответил за 90 с. Смотри: tail -50 $FV_LOG_DIR/api.err.log"
fi

if [[ $SKIP_TUNNEL -eq 0 ]]; then
  slept=0
  public_url=""
  while (( slept < 60 )); do
    public_url="$("$FV_INFRA_ROOT/infra/tunnel/public_url.sh" 2>/dev/null || true)"
    [[ -n "$public_url" ]] && break
    sleep 2; slept=$((slept + 2))
  done
  if [[ -n "$public_url" ]]; then
    check_ok "публичный адрес: $public_url"
    if fv_wait_http_ok "$public_url/api/v1/health" 45; then
      check_ok "публичный /api/v1/health отвечает: $(curl -s --max-time 5 "$public_url/api/v1/health")"
    else
      check_fail "публичный адрес не отвечает ($public_url). Проверь upstream $UPSTREAM и лог туннеля."
    fi
  else
    check_fail "не удалось определить публичный адрес за 60 с. Смотри $FV_LOG_DIR/tunnel.err.log"
  fi
fi

if command -v pmset >/dev/null 2>&1; then
  if pgrep -f 'caffeinate -dimsu' >/dev/null 2>&1; then
    check_ok "caffeinate -dimsu работает: $(pgrep -fl 'caffeinate -dimsu' | head -1)"
  else
    check_warn "процесс caffeinate -dimsu не найден (агент ещё стартует?)"
  fi
fi

# =======================================================================================
fv_step "7. Функциональная проверка (healthcheck.sh)"
# =======================================================================================
if [[ $DO_HEALTHCHECK -eq 1 ]]; then
  if "$FV_INFRA_ROOT/infra/healthcheck.sh" --local; then
    check_ok "healthcheck --local: PASS"
  else
    check_fail "healthcheck --local: FAIL (подробности выше)"
  fi
else
  fv_info "  пропущено (--no-healthcheck)"
fi

# =======================================================================================
fv_step "Итог"
# =======================================================================================
fv_info "  Ошибок: $failures, предупреждений: $warn_count"
fv_info ""
fv_info "  Что дальше руками:"
fv_info "    1) проверить публичный адрес:  $FV_INFRA_ROOT/infra/healthcheck.sh --public"
fv_info "    2) открыть UI в браузере        (для ngrok сначала может показаться страница-заглушка)"
fv_info "    3) статус агентов:              launchctl print $DOMAIN/com.fortevision.api | head -30"
fv_info "    4) логи:                        tail -f $FV_LOG_DIR/api.err.log $FV_LOG_DIR/tunnel.err.log"
fv_info "    5) снять автозапуск:            $FV_INFRA_ROOT/infra/macos/uninstall.sh"
if [[ $failures -gt 0 ]]; then
  fv_err ""
  fv_err "УСТАНОВКА ЗАВЕРШЕНА С ОШИБКАМИ ($failures). Сервис может быть не готов к приёму видео."
  exit 1
fi
fv_ok ""
fv_ok "ГОТОВО: автозапуск установлен, проверки пройдены."
exit 0
