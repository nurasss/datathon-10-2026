#!/bin/bash
# ForteVision infra — общие переменные и функции.
# Подключается через:  . "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# Ничего не выполняет при подключении, кроме вычисления путей.

# --- путь этого файла / worktree инфраструктуры -------------------------------------------------
FV_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # .../fv-infra/infra/macos
FV_INFRA_ROOT="${FV_INFRA_ROOT:-$(cd "$FV_LIB_DIR/../.." && pwd)}"   # .../fv-infra
export FV_INFRA_ROOT

# --- файл окружения ----------------------------------------------------------------------------
# Задан рано намеренно: без него нечего читать. Переопределяется флагом --env-file.
FV_ENV_FILE="${FV_ENV_FILE:-$HOME/.config/forte-vision/forte-vision.env}"

# --- метки launchd -----------------------------------------------------------------------------
FV_LABEL_API="com.fortevision.api"
FV_LABEL_TUNNEL="com.fortevision.tunnel"
FV_LABEL_CAFFEINATE="com.fortevision.caffeinate"
FV_LABEL_WATCHDOG="com.fortevision.watchdog"
# Внимание: uninstall.sh снимает ВСЕ метки из этого списка. install.sh (macos)
# ставит только api/tunnel/caffeinate; сторожа ставит infra/watchdog/install.sh.
FV_LABELS=("$FV_LABEL_API" "$FV_LABEL_TUNNEL" "$FV_LABEL_CAFFEINATE" "$FV_LABEL_WATCHDOG")

# --- цвета в выводе (только если терминал) ------------------------------------------------------
if [[ -t 1 && "${NO_COLOR:-}" == "" ]]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BOLD=""; C_DIM=""; C_OFF=""
fi

fv_info()  { printf '%s\n' "$*"; }
fv_ok()    { printf '%s%s%s\n' "$C_GREEN" "$*" "$C_OFF"; }
fv_warn()  { printf '%s%s%s\n' "$C_YELLOW" "$*" "$C_OFF" >&2; }
fv_err()   { printf '%s%s%s\n' "$C_RED" "$*" "$C_OFF" >&2; }
fv_step()  { printf '\n%s==> %s%s\n' "$C_BOLD" "$*" "$C_OFF"; }

# fv_defaults — значения по умолчанию для ВСЕГО, что может задать env-файл.
# Вызывать СТРОГО после fv_load_env, иначе файл не сможет переопределить значение.
# Идемпотентна: повторный вызов ничего не портит, уже заданное не трогает.
fv_defaults() {
  FV_APP_ROOT="${FV_APP_ROOT:-$FV_INFRA_ROOT/../forte-vision}"
  FV_APP_ROOT="$(cd "$FV_APP_ROOT" 2>/dev/null && pwd || printf '%s' "$FV_APP_ROOT")"
  FV_LOG_DIR="${FV_LOG_DIR:-$HOME/Library/Logs/forte-vision}"
  FV_AGENTS_DIR="${FV_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
  FV_TOOLS_DIR="${FV_TOOLS_DIR:-$FV_INFRA_ROOT/infra/.tools}"
  FV_BACKEND_PORT="${FV_BACKEND_PORT:-8000}"
  FV_BACKEND_LOCAL_URL="${FV_BACKEND_LOCAL_URL:-http://127.0.0.1:${FV_BACKEND_PORT}}"
  FV_TUNNEL_UPSTREAM="${FV_TUNNEL_UPSTREAM:-http://127.0.0.1:3000}"
  FV_NEXTJS_LOCAL_URL="${FV_NEXTJS_LOCAL_URL:-http://127.0.0.1:3000}"
  export FV_APP_ROOT FV_LOG_DIR FV_AGENTS_DIR FV_TOOLS_DIR \
         FV_BACKEND_PORT FV_BACKEND_LOCAL_URL FV_TUNNEL_UPSTREAM FV_NEXTJS_LOCAL_URL
}

# load_env — прочитать файл окружения.
# Переменные, уже заданные в окружении (вручную, через launchd или в тесте), НЕ перетираются:
# приоритет у явного окружения, файл только дополняет. Формат строк: KEY=value, без `export` и кавычек.
fv_load_env() {
  local file="${1:-$FV_ENV_FILE}" line key val
  [[ -f "$file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    val="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    case "$key" in
      ''|*[!A-Za-z0-9_]*) continue ;;   # не похоже на имя переменной — пропускаем
    esac
    if [[ -z "${!key+x}" ]]; then     # задаём только то, что ещё не задано
      export "$key=$val"
    fi
  done < "$file"
  return 0
}

# fv_python — интерпретатор приложения
fv_python() {
  if [[ -n "${FV_PYTHON:-}" && -x "$FV_PYTHON" ]]; then
    printf '%s' "$FV_PYTHON"; return 0
  fi
  local venv_py="$FV_APP_ROOT/.venv/bin/python"
  if [[ -x "$venv_py" ]]; then printf '%s' "$venv_py"; return 0; fi
  if command -v python3 >/dev/null 2>&1; then command -v python3; return 0; fi
  return 1
}

# fv_find_bin <name> — искать бинарь сначала в infra/.tools/bin, потом в PATH, потом в brew-путях
fv_find_bin() {
  local name="$1" candidate
  for candidate in "$FV_TOOLS_DIR/bin/$name" "/usr/local/bin/$name" "/opt/homebrew/bin/$name"; do
    if [[ -x "$candidate" ]]; then printf '%s' "$candidate"; return 0; fi
  done
  if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return 0; fi
  return 1
}

# fv_http_code <url> [timeout] — HTTP-код или 000 (ровно три символа).
# curl печатает 000 и при ошибке соединения, поэтому раньше получалось "000000"
# (код + fallback) — нормализуем, иначе сравнение с "000" не срабатывает.
fv_http_code() {
  local url="$1" timeout="${2:-5}" code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$timeout" "$url" 2>/dev/null)"
  code="${code:0:3}"
  printf '%s' "${code:-000}"
}

# fv_wait_http_ok <url> <seconds> — ждать, пока GET вернёт 2xx
fv_wait_http_ok() {
  local url="$1" seconds="${2:-60}" waited=0 code
  while (( waited < seconds )); do
    code="$(fv_http_code "$url" 3)"
    [[ "$code" == 2* ]] && return 0
    sleep 1; waited=$((waited + 1))
  done
  return 1
}

# fv_gui_domain — домен launchd текущего пользователя (gui/501)
fv_gui_domain() { printf 'gui/%s' "$(id -u)"; }

fv_is_loaded() {  # fv_is_loaded <label>
  launchctl print "$(fv_gui_domain)/$1" >/dev/null 2>&1
}
