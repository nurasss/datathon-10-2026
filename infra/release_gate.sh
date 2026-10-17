#!/bin/bash
# ForteVision — финальная проверка перед сдачей ("release gate").
#
# Что делает (и ничего кроме этого):
#   1. Проверяет ключ жюри: заполнен, достаточной длины, НЕ равен значению из .env.example
#      и не встречается ни в одном файле этой ветки инфраструктуры (иначе это утечка).
#   2. Запускает infra/healthcheck.sh --both --strict-docs на коротком ролике (по умолчанию Х-20):
#      живой POST локально и через публичный адрес, опрос до done, контракт ответа,
#      и требует, чтобы служебные /docs,/redoc,/openapi были ЗАКРЫТЫ.
#   3. Валидирует три сохранённых ответа сервиса по контракту: короткий, длинный и с нарушением
#      (обычно артефакты команды в forte-vision/artifacts/acceptance-final/).
#   4. Валидатор прогоняется на негативных кейсах (healthcheck --selftest), чтобы «PASS» что-то значил.
#   5. Печатает публичный адрес, метку времени и итог PASS/FAIL.
#
# Нагрузочных тестов здесь нет: один короткий ролик через healthcheck, всё остальное — офлайн.
# Скрипт ничего не меняет на машине (read-only проверка).
#
# Использование:
#   infra/release_gate.sh                      # локально + публично (туннель должен быть поднят)
#   infra/release_gate.sh --local              # без туннеля (предварительная проверка)
#   infra/release_gate.sh --file /path/Х-20.mov
#   infra/release_gate.sh --fixtures-dir /path/with/answers
#   infra/release_gate.sh --no-selftest
#
# Три ответа можно задать явно переменными окружения:
#   FV_RELEASE_SHORT_JSON / FV_RELEASE_LONG_JSON / FV_RELEASE_VIOLATION_JSON
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=macos/lib.sh
. "$SCRIPT_DIR/macos/lib.sh"

MODE="both"; CHECK_FILE=""; FIXTURES_DIR=""; RUN_SELFTEST=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) MODE="local" ;;
    --public) MODE="public" ;;
    --both) MODE="both" ;;
    --file) CHECK_FILE="$2"; shift ;;
    --fixtures-dir) FIXTURES_DIR="$2"; shift ;;
    --no-selftest) RUN_SELFTEST=0 ;;
    -h|--help) sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fv_err "Неизвестный флаг: $1 (см. --help)"; exit 2 ;;
  esac
  shift
done

fv_load_env || true
fv_defaults

CHECK_FILE="${CHECK_FILE:-${FV_HEALTHCHECK_FILE:-/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov}}"
FIXTURES_DIR="${FIXTURES_DIR:-${FV_RELEASE_FIXTURES_DIR:-$FV_APP_ROOT/artifacts/acceptance-final}}"
EXAMPLE_ENV="$FV_INFRA_ROOT/infra/env/forte-vision.env.example"

PY="$(fv_python || true)"
[[ -n "$PY" ]] || { fv_err "Не найден python (нужен для разбора JSON)"; exit 2; }

T_START=$(date +%s)
failures=0; checks=0
gate_ok()   { checks=$((checks + 1)); printf '  %s✓%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
gate_warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
gate_fail() { checks=$((checks + 1)); failures=$((failures + 1)); printf '  %s✗%s %s\n' "$C_RED" "$C_OFF" "$1"; }

PUBLIC_URL="$("$SCRIPT_DIR/tunnel/public_url.sh" 2>/dev/null || true)"

printf '%sForteVision release gate%s\n' "$C_BOLD" "$C_OFF"
fv_info "время       : $(date '+%Y-%m-%d %H:%M:%S %z')"
fv_info "режим       : $MODE"
fv_info "ролик       : $CHECK_FILE"
fv_info "ответы      : $FIXTURES_DIR"
fv_info "публичный   : ${PUBLIC_URL:-<не определён>}"
if [[ "$MODE" != "local" && -z "$PUBLIC_URL" ]]; then
  fv_err ""
  fv_err "Публичный адрес не определён, а режим '$MODE' требует туннель."
  fv_err "Подними туннель (launchd-агент или infra/tunnel/run_tunnel.sh) либо запусти с --local."
  exit 2
fi

# =======================================================================================
fv_step "1. Ключ жюри"
# =======================================================================================
KEY="${FV_API_KEYS:-}"; KEY="${KEY%%,*}"
if [[ -z "$KEY" ]]; then
  gate_fail "FV_API_KEYS пуст — жюри получит 401. Заполни $FV_ENV_FILE"
else
  gate_ok "ключ задан (${#KEY} символов, начинается на ${KEY:0:4}…, значение не печатаю)"
  if (( ${#KEY} < 16 )); then
    gate_fail "ключ короче 16 символов — такую строку подобрать тривиально"
  fi
  placeholder="$(grep -E '^FV_API_KEYS=' "$EXAMPLE_ENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
  if [[ -z "$placeholder" ]]; then
    gate_warn "в $EXAMPLE_ENV нет строки FV_API_KEYS — сравнить с шаблоном не удалось"
  elif [[ "$KEY" == "$placeholder" ]]; then
    gate_fail "ключ жюри РАВЕН значению-заглушке из .env.example — такую строку знает любой, кто видел репозиторий"
  else
    gate_ok "ключ не совпадает со значением из .env.example"
  fi
  # Утечка: реальный ключ не должен лежать ни в одном файле этой ветки.
  leaked="$(grep -rl -F "$KEY" --exclude-dir=.git --exclude-dir=.tools "$FV_INFRA_ROOT" 2>/dev/null | head -3)"
  if [[ -n "$leaked" ]]; then
    gate_fail "ключ жюри найден в файлах ветки (утечка!):"
    printf '        %s\n' $leaked
  else
    gate_ok "ключа нет ни в одном файле $FV_INFRA_ROOT (проверено grep'ом)"
  fi
fi

# =======================================================================================
fv_step "2. Три сохранённых ответа (короткий / длинный / с нарушением)"
# =======================================================================================
SHORT_JSON="${FV_RELEASE_SHORT_JSON:-}"; LONG_JSON="${FV_RELEASE_LONG_JSON:-}"; VIOLATION_JSON="${FV_RELEASE_VIOLATION_JSON:-}"

if [[ -z "$SHORT_JSON$LONG_JSON$VIOLATION_JSON" ]]; then
  # Автовыбор ролей по timings.json (там длительности), иначе — по известным именам job_id.
  resolved="$("$PY" - "$FIXTURES_DIR" <<'PY' 2>/dev/null || true
import json, os, sys
d = sys.argv[1]
short = long_ = viol = ""
tp = os.path.join(d, "timings.json")
if os.path.isfile(tp):
    try:
        rows = [r for r in json.load(open(tp, encoding="utf-8")) if r.get("status") == "done"]
        nov = sorted((r for r in rows if r.get("verdict") == "no_violation"),
                     key=lambda r: r.get("duration_sec") or 0)
        vs = [r for r in rows if r.get("verdict") == "violation"]
        if nov:
            short = os.path.join(d, nov[0]["job_id"] + ".json")
        if len(nov) > 1:
            long_ = os.path.join(d, nov[-1]["job_id"] + ".json")
        if vs:
            viol = os.path.join(d, vs[0]["job_id"] + ".json")
    except Exception:
        pass
for role, path in (("SHORT", short), ("LONG", long_), ("VIOLATION", viol)):
    print(f"{role}\t{path}")
PY
)"
  SHORT_JSON="${SHORT_JSON:-$(printf '%s\n' "$resolved" | awk -F'\t' '$1=="SHORT"{print $2}')}"
  LONG_JSON="${LONG_JSON:-$(printf '%s\n' "$resolved" | awk -F'\t' '$1=="LONG"{print $2}')}"
  VIOLATION_JSON="${VIOLATION_JSON:-$(printf '%s\n' "$resolved" | awk -F'\t' '$1=="VIOLATION"{print $2}')}"
fi

validate_one() {  # validate_one <роль> <файл> <ожидаемый verdict>
  local role="$1" file="$2" expect="$3"
  if [[ -z "$file" || ! -f "$file" ]]; then
    gate_fail "$role: файл не найден (${file:-<не задан>})"
    return 1
  fi
  local out rc verdict
  out="$("$SCRIPT_DIR/healthcheck.sh" --validate-file "$file" 2>&1)"; rc=$?
  if [[ $rc -ne 0 ]]; then
    gate_fail "$role: контракт нарушен — ${out%%$'\n'*}"
    return 1
  fi
  verdict="$("$PY" -c 'import json,sys
try: print(json.load(open(sys.argv[1], encoding="utf-8")).get("verdict") or "")
except Exception: print("")' "$file" 2>/dev/null)"
  if [[ "$verdict" != "$expect" ]]; then
    gate_fail "$role: ожидался verdict=$expect, а в файле '$verdict' (не тот артефакт?)"
    return 1
  fi
  gate_ok "$role ($(basename "$file")): $out"
  return 0
}

validate_one "короткий  " "$SHORT_JSON"     "no_violation"
validate_one "длинный   " "$LONG_JSON"      "no_violation"
validate_one "нарушение " "$VIOLATION_JSON" "violation"

# =======================================================================================
fv_step "3. End-to-end через $MODE (с требованием закрытых /docs)"
# =======================================================================================
if [[ ! -f "$CHECK_FILE" ]]; then
  gate_fail "нет файла для проверки: $CHECK_FILE"
else
  fv_info "  запускаю: infra/healthcheck.sh --$MODE --strict-docs --file '$CHECK_FILE'"
  hc_out="$("$SCRIPT_DIR/healthcheck.sh" "--$MODE" --strict-docs --file "$CHECK_FILE" 2>&1)"; hc_rc=$?
  printf '%s\n' "$hc_out"
  if [[ $hc_rc -eq 0 ]]; then
    gate_ok "healthcheck --$MODE --strict-docs: PASS"
  else
    gate_fail "healthcheck --$MODE --strict-docs: FAIL (вывод выше)"
    if printf '%s' "$hc_out" | grep -q 'ОТКРЫТЫ:'; then
      fv_info "  ↑ причина — открытые служебные эндпоинты. Правка (apps/):"
      fv_info "    docs/submission/APP-CHANGE-REQUEST-docs-endpoints.md"
      fv_info "    после правки повторить: $SCRIPT_DIR/release_gate.sh --$MODE"
    fi
  fi
fi

# =======================================================================================
fv_step "4. Самопроверка валидатора (--selftest)"
# =======================================================================================
if [[ $RUN_SELFTEST -eq 1 ]]; then
  if out="$("$SCRIPT_DIR/healthcheck.sh" --selftest 2>&1)"; then
    gate_ok "$(printf '%s' "$out" | tail -1)"
  else
    gate_fail "selftest упал: $(printf '%s' "$out" | tail -1)"
  fi
else
  fv_info "  пропущено (--no-selftest)"
fi

# =======================================================================================
fv_step "Итог release gate"
# =======================================================================================
T_END=$(date +%s); ELAPSED=$((T_END - T_START))
fv_info "  время окончания : $(date '+%Y-%m-%d %H:%M:%S %z')"
fv_info "  заняло          : ${ELAPSED} с"
fv_info "  публичный адрес : ${PUBLIC_URL:-<не определён>}"
fv_info "  проверок        : $checks, провалено: $failures"
if [[ $failures -gt 0 ]]; then
  fv_err ""
  fv_err "НЕ ГОТОВО К СДАЧЕ: провалено проверок — $failures."
  fv_info "  повторить после исправлений: $SCRIPT_DIR/release_gate.sh --$MODE"
  exit 1
fi
fv_ok ""
fv_ok "ГОТОВО К СДАЧЕ: все проверки пройдены."
exit 0
