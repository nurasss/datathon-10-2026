#!/bin/bash
# ForteVision — выкат версии из ветки/каталога в прод-каталог (~/fortevision/app).
#
# Зачем отдельный скрипт: агенты launchd работают из ~/fortevision/app, потому что macOS (TCC)
# не даёт демонам доступ к ~/Desktop. Выкат запускает человек из Terminal (у терминала доступ есть).
#
# Что делает по шагам:
#   0. проверки: источник существует и похож на приложение; цель безопасна; агент загружен;
#   1. --dry-run: только показывает план (и diff rsync -n), ничего не меняет;
#   2. снимок текущей версии в ~/fortevision/app.prev (код, без .venv/data/artifacts);
#   3. rsync источника в ~/fortevision/app (без .git, data/, artifacts/, __pycache__, .venv, .env);
#      rsync идёт с --delete, но исключённое (в т.ч. data/, .venv, .deploy-stamp) не удаляется;
#   4. синхронизация зависимостей: если apps/api/requirements.txt изменился —
#      uv pip install --python <app>/.venv/bin/python -r <app>/apps/api/requirements.txt;
#   5. launchctl kickstart -k gui/$UID/com.fortevision.api и ожидание /api/v1/health;
#   6. healthcheck.sh --public (короткий ролик, по умолчанию Х-20);
#   7. штамп .deploy-stamp (ветка/коммит/дата) и подсказка по откату.
#
# Откат:  infra/deploy.sh --rollback     (восстанавливает код из ~/fortevision/app.prev)
#
# Использование:
#   infra/deploy.sh --branch integ                 # выкатить ветку (git archive, без рабочего дерева)
#   infra/deploy.sh --from /путь/к/каталогу        # выкатить из каталога/worktree
#   infra/deploy.sh --branch integ --dry-run       # ничего не менять, показать план и diff
#   infra/deploy.sh --rollback [--dry-run]         # вернуть предыдущую версию
#
# Флаги: --repo DIR, --app-dir DIR, --file FILE, --skip-deps, --no-restart, --no-healthcheck,
#        --allow-unsafe-target (для тестов вне $HOME)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=macos/lib.sh
. "$SCRIPT_DIR/macos/lib.sh"

BRANCH=""; FROM_DIR=""; ROLLBACK=0; DRY_RUN=0
SKIP_DEPS=0; DO_RESTART=1; DO_HEALTHCHECK=1; ALLOW_UNSAFE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="$2"; shift ;;
    --from) FROM_DIR="$2"; shift ;;
    --repo) FV_DEPLOY_REPO="$2"; shift ;;
    --app-dir) FV_DEPLOY_APP_DIR="$2"; shift ;;
    --file) HEALTHCHECK_FILE_OVERRIDE="$2"; shift ;;
    --rollback) ROLLBACK=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    --no-restart) DO_RESTART=0 ;;
    --no-healthcheck) DO_HEALTHCHECK=0 ;;
    --allow-unsafe-target) ALLOW_UNSAFE=1 ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fv_err "Неизвестный флаг: $1 (см. --help)"; exit 2 ;;
  esac
  shift
done

fv_load_env || true
fv_defaults

APP_DIR="${FV_DEPLOY_APP_DIR:-$HOME/fortevision/app}"
PREV_DIR="${FV_DEPLOY_PREV_DIR:-${APP_DIR}.prev}"
REPO="${FV_DEPLOY_REPO:-/Users/nuras/Desktop/datathon/forte-vision}"
AGENT_LABEL="${FV_DEPLOY_AGENT_LABEL:-com.fortevision.api}"
DOMAIN="$(fv_gui_domain)"
HEALTH_URL="$FV_BACKEND_LOCAL_URL/api/v1/health"
WAIT_SEC="${FV_DEPLOY_WAIT_SEC:-120}"
HC_FILE="${HEALTHCHECK_FILE_OVERRIDE:-${FV_HEALTHCHECK_FILE:-/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov}}"
UV_BIN="${FV_DEPLOY_UV:-$(for c in "$HOME/.local/bin/uv" /usr/local/bin/uv /opt/homebrew/bin/uv; do [[ -x "$c" ]] && { printf '%s' "$c"; break; }; done)}"
[[ -n "$UV_BIN" ]] || UV_BIN="$(command -v uv 2>/dev/null || true)"

STAMP_FILE="$APP_DIR/.deploy-stamp"
TS="$(date '+%Y-%m-%d %H:%M:%S %z')"
failures=0
gate_fail() { failures=$((failures + 1)); fv_err "  ✗ $1"; }
step_run()  { if [[ $DRY_RUN -eq 1 ]]; then fv_info "  [dry-run] $*"; else "$@"; fi; }

# Исключения: код едет, состояние — нет. Исключённое rsync с --delete НЕ удаляет.
RSYNC_EXCLUDES=(
  --exclude '.git/'
  --exclude 'data/'
  --exclude 'artifacts/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.venv/'
  --exclude '.env'
  --exclude '.env.*'
  --exclude '.pytest_cache/'
  --exclude '.mypy_cache/'
  --exclude '.DS_Store'
  --exclude 'node_modules/'
  --exclude '.tools/'
  --exclude 'data_raw/'
  --exclude 'uploads/'
  --exclude 'evidence/'
  --exclude 'reports/'
  --exclude '/*.log'
  --exclude '/.deploy-stamp'
)

printf '%sForteVision deploy%s\n' "$C_BOLD" "$C_OFF"
fv_info "время        : $TS"
fv_info "источник     : ${BRANCH:+ветка $BRANCH из $REPO}${FROM_DIR:+каталог $FROM_DIR}"
fv_info "цель         : $APP_DIR"
fv_info "предыдущая   : $PREV_DIR"
fv_info "агент        : $DOMAIN/$AGENT_LABEL"
fv_info "режим        : $([[ $DRY_RUN -eq 1 ]] && echo 'DRY-RUN (ничего не меняется)' || echo 'ВЫКАТ')$([[ $ROLLBACK -eq 1 ]] && echo ' + ОТКАТ')"
fv_info "зависимости  : $([[ $SKIP_DEPS -eq 1 ]] && echo 'пропускаю (--skip-deps)' || echo "uv: ${UV_BIN:-НЕ НАЙДЕН}")"

if [[ $ROLLBACK -eq 0 && $DRY_RUN -eq 0 && -n "${FV_ENV_FILE:-}" && -f "$FV_ENV_FILE" ]]; then
  perms="$(stat -f '%Lp' "$FV_ENV_FILE" 2>/dev/null || echo '?')"
  [[ "$perms" == "600" ]] || fv_warn "  ! права на $FV_ENV_FILE: $perms (ожидалось 600)"
fi

# =======================================================================================
fv_step "1. Проверки"
# =======================================================================================
if [[ $ROLLBACK -eq 0 ]]; then
  if [[ -z "$BRANCH" && -z "$FROM_DIR" ]]; then
    gate_fail "укажи источник: --branch <ветка> или --from <каталог> (см. --help)"
  fi
  if [[ -n "$BRANCH" && -n "$FROM_DIR" ]]; then
    gate_fail "--branch и --from взаимоисключающие"
  fi
fi

# Цель: только внутри домашнего каталога (страховка от «rsync --delete не туда»).
case "$APP_DIR" in
  "$HOME"/*) : ;;
  *) [[ $ALLOW_UNSAFE -eq 1 ]] || gate_fail "цель вне \$HOME ($APP_DIR) — это опасно с rsync --delete; добавь --allow-unsafe-target, если это тест" ;;
esac
[[ "$APP_DIR" != "$HOME" && "$APP_DIR" != "/" ]] || gate_fail "недопустимая цель: $APP_DIR"

# Свободное место: полный диск уже ломал прод (sqlite «unable to open database file» при WAL),
# поэтому выкат и установка зависимостей без запаса не начинаются.
MIN_FREE_MB="${FV_DEPLOY_MIN_FREE_MB:-1000}"
probe_dir="$APP_DIR"
while [[ ! -d "$probe_dir" && "$probe_dir" != "/" ]]; do probe_dir="$(dirname "$probe_dir")"; done
free_mb="$(df -Pk "$probe_dir" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024}')"
if [[ -n "$free_mb" ]]; then
  if (( free_mb < MIN_FREE_MB )); then
    gate_fail "на диске всего ${free_mb} МБ свободно (< ${MIN_FREE_MB} МБ): сначала почисти диск, потом выкат"
  elif (( free_mb < 2 * MIN_FREE_MB )); then
    fv_warn "  ! свободного места мало: ${free_mb} МБ (запас < $((2 * MIN_FREE_MB)) МБ)"
  else
    fv_info "  ✓ свободного места на диске: ${free_mb} МБ"
  fi
fi

STAGING=""
cleanup() { [[ -n "$STAGING" && -d "$STAGING" ]] && rm -rf "$STAGING"; }
trap cleanup EXIT

SRC=""
if [[ $ROLLBACK -eq 0 && $failures -eq 0 ]]; then
  if [[ -n "$BRANCH" ]]; then
    if [[ ! -d "$REPO/.git" && ! -f "$REPO/.git" ]]; then
      gate_fail "не git-репозиторий: $REPO"
    else
      COMMIT="$(git -C "$REPO" rev-parse --verify "${BRANCH}^{commit}" 2>/dev/null || true)"
      if [[ -z "$COMMIT" ]]; then
        gate_fail "ветка '$BRANCH' не найдена в $REPO (git rev-parse)"
      else
        # Ветку распаковываем и в dry-run: иначе diff/хеш зависимостей считались бы по рабочему
        # дереву репозитория (другая ветка) и план был бы неверным. Временный каталог удаляется trap'ом.
        STAGING="$(mktemp -d "${TMPDIR:-/tmp}${TMPDIR:+/}fv-deploy.XXXXXX" 2>/dev/null || mktemp -d "${TMPDIR:-/tmp}/fv-deploy.XXXXXX")"
        if git -C "$REPO" archive --format=tar "$BRANCH" | tar -xf - -C "$STAGING"; then
          SRC="$STAGING"
          fv_info "  ✓ git archive: $BRANCH @ ${COMMIT:0:7}"
        else
          gate_fail "не удалось распаковать git archive ветки $BRANCH"
        fi
      fi
    fi
  elif [[ -n "$FROM_DIR" ]]; then
    if [[ -d "$FROM_DIR" ]]; then
      SRC="$(cd "$FROM_DIR" && pwd)"
      if git -C "$SRC" rev-parse --verify HEAD >/dev/null 2>&1; then
        COMMIT="$(git -C "$SRC" rev-parse HEAD)"
      fi
    else
      gate_fail "каталог-источник не найден: $FROM_DIR"
    fi
  fi
fi

SRC_CHECK="${SRC:-$REPO}"
[[ $ROLLBACK -eq 1 ]] && SRC_CHECK="$PREV_DIR"   # при откате источник зависимостей — app.prev
if [[ $ROLLBACK -eq 0 && $failures -eq 0 ]]; then
  [[ -f "$SRC_CHECK/scripts/run_server.sh" ]] || gate_fail "источник не похож на приложение: нет scripts/run_server.sh ($SRC_CHECK)"
  [[ -f "$SRC_CHECK/apps/api/app/main.py" ]] || gate_fail "источник не похож на приложение: нет apps/api/app/main.py ($SRC_CHECK)"
fi
if [[ $ROLLBACK -eq 1 ]]; then
  SRC_DESC="откат из $PREV_DIR"
  [[ -f "$PREV_DIR/apps/api/app/main.py" ]] || gate_fail "нет предыдущей версии для отката: $PREV_DIR/apps/api/app/main.py"
elif [[ -n "$BRANCH" ]]; then
  SRC_DESC="ветка $BRANCH @ ${COMMIT:0:7}"
elif [[ -n "$FROM_DIR" ]]; then
  SRC_DESC="каталог $SRC${COMMIT:+ @ ${COMMIT:0:7}}"
fi
if [[ -e "$APP_DIR" && ! -d "$APP_DIR" ]]; then
  gate_fail "цель существует и это не каталог: $APP_DIR"
fi
if [[ $ROLLBACK -eq 0 && -d "$APP_DIR" ]]; then
  [[ -f "$APP_DIR/apps/api/app/main.py" ]] || fv_warn "  ! в цели нет apps/api/app/main.py — это похоже на первый выкат в пустой каталог"
  [[ -d "$APP_DIR/.venv" ]] || fv_warn "  ! в цели нет .venv — зависимости некуда ставить (--skip-deps?)"
fi
if [[ $DO_RESTART -eq 1 ]]; then
  if fv_is_loaded "$AGENT_LABEL"; then
    fv_info "  ✓ агент $AGENT_LABEL загружен"
  else
    gate_fail "агент $AGENT_LABEL не загружен: launchctl bootstrap $DOMAIN ~/Library/LaunchAgents/$AGENT_LABEL.plist"
  fi
fi

if [[ $failures -gt 0 ]]; then
  fv_err ""
  fv_err "Проверки не пройдены ($failures) — ничего не делаю."
  exit 1
fi

# =======================================================================================
fv_step "2. Что изменится (rsync --itemize-changes)"
# =======================================================================================
RSYNC_LOG="${FV_LOG_DIR:-/tmp}/deploy-$(date '+%Y%m%d-%H%M%S').log"
mkdir -p "${FV_LOG_DIR:-/tmp}" 2>/dev/null || true
if [[ $ROLLBACK -eq 1 ]]; then
  RSYNC_SRC="$PREV_DIR/"
else
  RSYNC_SRC="${SRC:-$SRC_CHECK}/"
fi
{
  printf '# deploy %s\n# источник: %s\n# цель: %s\n' "$TS" "$SRC_DESC" "$APP_DIR"
} > "$RSYNC_LOG"

if [[ ! -d "$APP_DIR" ]]; then
  fv_info "  цель не существует — будет создана"
  step_run mkdir -p "$APP_DIR"
fi

if [[ $ROLLBACK -eq 1 ]]; then
  fv_info "  откат: rsync $PREV_DIR/ → $APP_DIR/"
else
  fv_info "  источник: $SRC_DESC"
fi
if rsync -a --delete --itemize-changes --dry-run "${RSYNC_EXCLUDES[@]}" "$RSYNC_SRC" "$APP_DIR/" >> "$RSYNC_LOG" 2>&1; then
  changed="$(grep -cE '^(>|<|c|[*]deleting)' "$RSYNC_LOG" || true)"
  fv_info "  изменений: $changed (полный список: $RSYNC_LOG)"
  if (( changed > 0 )); then
    grep -E '^(>|<|c|[*]deleting)' "$RSYNC_LOG" | head -15 | sed 's/^/    /'
    (( changed > 15 )) && fv_info "    … и ещё $((changed - 15))"
  fi
else
  gate_fail "rsync --dry-run не смог прочитать источник (TCC? запусти из Terminal, а не из launchd)"
fi

# =======================================================================================
fv_step "3. Снимок текущей версии и выкат"
# =======================================================================================
if [[ $ROLLBACK -eq 1 ]]; then
  fv_info "  снимок не делаю — это откат"
else
  if [[ -d "$APP_DIR" && -f "$APP_DIR/apps/api/app/main.py" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      fv_info "  [dry-run] rsync $APP_DIR/ → $PREV_DIR/  (снимок предыдущей версии)"
    elif rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$APP_DIR/" "$PREV_DIR/" >/dev/null 2>&1; then
      fv_info "  ✓ предыдущая версия сохранена: $PREV_DIR"
    else
      gate_fail "не удалось сохранить снимок в $PREV_DIR"
    fi
  else
    fv_warn "  ! снимок не сделал: в $APP_DIR нет приложения (похоже на первый выкат)"
  fi
fi

if [[ $DRY_RUN -eq 1 ]]; then
  fv_info "  [dry-run] rsync $RSYNC_SRC → $APP_DIR/ (реальный выкат, исключения те же)"
else
  rsync_rc=0
  rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$RSYNC_SRC" "$APP_DIR/" >> "$RSYNC_LOG" 2>&1 || rsync_rc=$?
  if [[ $rsync_rc -eq 0 || $rsync_rc -eq 24 ]]; then
    fv_info "  ✓ код выкачан в $APP_DIR (rsync rc=$rsync_rc$( [[ $rsync_rc -eq 24 ]] && echo ' — часть файлов исчезла по ходу, это норма' ))"
  else
    gate_fail "rsync упал (rc=$rsync_rc) — смотри $RSYNC_LOG"
  fi
fi

if [[ $failures -gt 0 ]]; then
  fv_err ""
  fv_err "Выкат прерван после ошибок — цель могла остаться в промежуточном состоянии."
  fv_info "  откат: $SCRIPT_DIR/deploy.sh --rollback"
  exit 1
fi

# =======================================================================================
fv_step "4. Зависимости (apps/api/requirements.txt)"
# =======================================================================================
REQ_SRC="${SRC_CHECK}/apps/api/requirements.txt"
REQ_DST="$APP_DIR/apps/api/requirements.txt"
HASH_FILE="$APP_DIR/.venv/.requirements.sha256"
VENV_PY="$APP_DIR/.venv/bin/python"
DEPS_ACTION="не требуется"

if [[ $SKIP_DEPS -eq 1 ]]; then
  DEPS_ACTION="пропущены (--skip-deps)"
  fv_info "  $DEPS_ACTION"
else
  if [[ ! -f "$REQ_SRC" ]]; then
    gate_fail "нет $REQ_SRC"
  elif [[ ! -x "$VENV_PY" ]]; then
    gate_fail "нет интерпретатора $VENV_PY (создай .venv или --skip-deps)"
  else
    new_hash="$(shasum -a 256 "$REQ_SRC" | awk '{print $1}')"
    old_hash="$(cat "$HASH_FILE" 2>/dev/null || true)"
    if [[ "$new_hash" == "$old_hash" ]]; then
      fv_info "  requirements не менялся (sha256 ${new_hash:0:12}…)"
      DEPS_ACTION="актуальны"
    else
      DEPS_ACTION="обновление через uv"
      fv_info "  requirements изменился: ${old_hash:0:12}${old_hash:+…} → ${new_hash:0:12}…"
      if [[ -z "$UV_BIN" ]]; then
        gate_fail "uv не найден (искал ~/.local/bin/uv, /usr/local/bin/uv, PATH)"
      elif [[ $DRY_RUN -eq 1 ]]; then
        fv_info "  [dry-run] $UV_BIN pip install --python $VENV_PY -r <app>/apps/api/requirements.txt"
      else
        mkdir -p "$APP_DIR/apps/api"
        fv_info "  $UV_BIN pip install --python $VENV_PY -r $REQ_DST"
        install_log="${RSYNC_LOG%.log}.deps.log"
        if "$UV_BIN" pip install --python "$VENV_PY" -r "$REQ_DST" > "$install_log" 2>&1; then
          printf '%s' "$new_hash" > "$HASH_FILE"
          fv_info "  ✓ зависимости установлены (лог: $install_log)"
        else
          gate_fail "uv pip install упал — смотри $install_log"
        fi
      fi
    fi
  fi
fi

# =======================================================================================
fv_step "5. Перезапуск агента и health"
# =======================================================================================
if [[ $DO_RESTART -eq 0 ]]; then
  fv_info "  пропущено (--no-restart)"
elif [[ $DRY_RUN -eq 1 ]]; then
  fv_info "  [dry-run] launchctl kickstart -k $DOMAIN/$AGENT_LABEL"
  fv_info "  [dry-run] ждать 200 на $HEALTH_URL (до ${WAIT_SEC} с)"
else
  if launchctl kickstart -k "$DOMAIN/$AGENT_LABEL"; then
    fv_info "  ✓ kickstart отправлен"
    if fv_wait_http_ok "$HEALTH_URL" "$WAIT_SEC"; then
      fv_info "  ✓ $HEALTH_URL → $(curl -s --max-time 5 "$HEALTH_URL" | head -c 200)"
    else
      gate_fail "бэкенд не поднялся за ${WAIT_SEC} с — смотри tail -50 ~/Library/Logs/forte-vision/api.err.log"
    fi
  else
    gate_fail "launchctl kickstart не удался"
  fi
fi

# =======================================================================================
fv_step "6. Публичная проверка (healthcheck --public)"
# =======================================================================================
PUBLIC_URL="$("$FV_INFRA_ROOT/infra/tunnel/public_url.sh" 2>/dev/null || true)"
fv_info "  публичный адрес: ${PUBLIC_URL:-<не определён>}"
if [[ $DO_HEALTHCHECK -eq 0 || $DO_RESTART -eq 0 ]]; then
  fv_info "  пропущено"
elif [[ $DRY_RUN -eq 1 ]]; then
  fv_info "  [dry-run] $FV_INFRA_ROOT/infra/healthcheck.sh --public --file '$HC_FILE'"
else
  if "$FV_INFRA_ROOT/infra/healthcheck.sh" --public --file "$HC_FILE"; then
    fv_info "  ✓ публичная проверка: PASS"
  else
    gate_fail "публичная проверка: FAIL (вывод выше)"
  fi
fi

# =======================================================================================
fv_step "7. Штамп и итог"
# =======================================================================================
if [[ $DRY_RUN -eq 0 && $failures -eq 0 ]]; then
  {
    printf 'deployed_at=%s\n' "$TS"
    printf 'action=%s\n' "$([[ $ROLLBACK -eq 1 ]] && echo rollback || echo deploy)"
    printf 'source=%s\n' "$SRC_DESC"
    printf 'deps=%s\n' "$DEPS_ACTION"
    printf 'by=%s\n' "${USER:-$(id -un)}"
  } > "$STAMP_FILE"
  fv_info "  штамп: $STAMP_FILE"
  cat "$STAMP_FILE" | sed 's/^/    /'
else
  fv_info "  штамп не пишется ($([[ $DRY_RUN -eq 1 ]] && echo dry-run || echo 'были ошибки'))"
fi

fv_info ""
fv_info "  откат:  $SCRIPT_DIR/deploy.sh --rollback"
fv_info "  логи:   $RSYNC_LOG${install_log:+, ${install_log}}"
if [[ $failures -gt 0 ]]; then
  fv_err ""
  fv_err "ВЫКАТ НЕ ЗАВЕРШЁН: проблем $failures."
  exit 1
fi
fv_ok ""
if [[ $DRY_RUN -eq 1 ]]; then
  fv_ok "DRY-RUN завершён: план показан, ничего не изменено."
else
  if [[ $DO_RESTART -eq 1 && $DO_HEALTHCHECK -eq 1 ]]; then
    tail_note=", публичная проверка пройдена"
  else
    tail_note=", перезапуск/публичная проверка пропущены"
  fi
  fv_ok "ВЫКАТ ЗАВЕРШЁН: код обновлён, зависимости: $DEPS_ACTION$tail_note."
fi
exit 0
