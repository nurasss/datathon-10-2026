#!/bin/bash
# ForteVision — end-to-end проверка сервиса как «снаружи» (то же, что делает жюри).
#
# Что проверяет на каждом адресе (локальном и публичном):
#   1. GET  /api/v1/health                     — сервис жив
#   2. POST /api/v1/analyze без X-API-Key      — должно быть 401
#   3. POST /api/v1/analyze с ключом и реальным .mov — должно быть 202 + job_id
#   4. GET  /api/v1/jobs/{job_id}              — опрос до статуса done (лимит 300 с, как у жюри)
#   5. Валидация JSON: verdict / violations / evidence_frames (каждый JPEG реально декодируется)
#   6. (мягко) торчат ли /docs, /openapi.json, /redoc — см. --strict-docs
#
# Код возврата: 0 — все обязательные проверки прошли, 1 — что-то упало.
#
# Примеры:
#   infra/healthcheck.sh                       # локально + публично (публичный адрес ищется сам)
#   infra/healthcheck.sh --local               # только локально
#   infra/healthcheck.sh --public              # только через публичный адрес
#   infra/healthcheck.sh --file /path/big.mov  # проверить большой файл (например Х-14, ~29 МБ)
#   infra/healthcheck.sh --strict-docs         # считать открытые /docs падением
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=macos/lib.sh
. "$SCRIPT_DIR/macos/lib.sh"
export LANG="${LANG:-en_US.UTF-8}"

# ---------------------------------------------------------------------------------------
# аргументы
# ---------------------------------------------------------------------------------------
TARGET_MODE="auto"          # auto | local | public | both
CHECK_FILE=""
BASE_OVERRIDE=""
KEY_OVERRIDE=""
TIMEOUT="${FV_HEALTHCHECK_TIMEOUT:-300}"
STRICT_DOCS=0
SELFTEST=0
VALIDATE_FILE=""
UPLOAD_TIMEOUT=600          # на загрузку 30 МБ через домашний канал должно хватать с запасом

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) TARGET_MODE="local" ;;
    --public) TARGET_MODE="public" ;;
    --both) TARGET_MODE="both" ;;
    --file) CHECK_FILE="$2"; shift ;;
    --base) BASE_OVERRIDE="$2"; shift ;;
    --key) KEY_OVERRIDE="$2"; shift ;;
    --timeout) TIMEOUT="$2"; shift ;;
    --strict-docs) STRICT_DOCS=1 ;;
    --selftest) SELFTEST=1 ;;
    --validate-file) VALIDATE_FILE="$2"; shift ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fv_err "Неизвестный флаг: $1 (см. --help)"; exit 2 ;;
  esac
  shift
done

fv_load_env || true
fv_defaults

CHECK_FILE="${CHECK_FILE:-${FV_HEALTHCHECK_FILE:-/Users/nuras/Desktop/datathon/data_raw/open/videos/Х-20.mov}}"
API_KEY="${KEY_OVERRIDE:-${FV_API_KEY:-${FV_API_KEYS%%,*}}}"
PY="$(fv_python || true)"
[[ -n "$PY" ]] || { fv_err "Не найден python для разбора JSON"; exit 2; }

TMPDIR_RUN="$(mktemp -d "${TMPDIR:-/tmp}/fv-healthcheck.XXXXXX")"
cleanup() { rm -rf "$TMPDIR_RUN"; }
trap cleanup EXIT

# ---------------------------------------------------------------------------------------
# адреса
# ---------------------------------------------------------------------------------------
LOCAL_BASE="$FV_BACKEND_LOCAL_URL"
PUBLIC_BASE="${BASE_OVERRIDE:-$("$SCRIPT_DIR/tunnel/public_url.sh" 2>/dev/null || true)}"

case "$TARGET_MODE" in
  local)  BASES=("$LOCAL_BASE") ;;
  public) BASES=("${PUBLIC_BASE:-}") ;;
  both)   BASES=("$LOCAL_BASE" "${PUBLIC_BASE:-}") ;;
  auto)
    if [[ -n "$PUBLIC_BASE" ]]; then BASES=("$LOCAL_BASE" "$PUBLIC_BASE"); else BASES=("$LOCAL_BASE"); fi
    ;;
esac

if [[ -z "$API_KEY" && $SELFTEST -eq 0 && -z "$VALIDATE_FILE" ]]; then
  fv_err "Не задан ключ: FV_API_KEYS пуст и --key не передан"
  exit 2
fi
if [[ $SELFTEST -eq 0 && -z "$VALIDATE_FILE" ]]; then
  if [[ ! -f "$CHECK_FILE" ]]; then
    fv_err "Не найден тестовый файл: $CHECK_FILE"
    exit 2
  fi
fi

FILE_SIZE_MB=$(( ($(stat -f '%z' "$CHECK_FILE" 2>/dev/null || echo 0) + 524287) / 1048576 ))

# ---------------------------------------------------------------------------------------
# независимая валидация ответа (не импортирует код приложения — проверяем контракт)
# ---------------------------------------------------------------------------------------
validate_done_json() {  # validate_done_json <json-file>  -> печатает OK/WARN/FAIL
"$PY" - "$1" <<'PY'
import base64, json, sys

path = sys.argv[1]
problems, warnings = [], []

try:
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    doc = json.loads(raw)
except Exception as exc:  # noqa: BLE001
    print(f"FAIL не JSON ({type(exc).__name__}); первые 200 байт: {raw[:200]!r}" if 'raw' in dir() else f"FAIL не JSON ({type(exc).__name__})")
    raise SystemExit(1)

if not isinstance(doc, dict):
    print("FAIL корень ответа не объект"); raise SystemExit(1)

status = doc.get("status")
if status == "failed":
    print(f"FAIL задача упала: error={doc.get('error')!r}"); raise SystemExit(1)
if status == "processing":
    print("FAIL задача всё ещё processing после опроса"); raise SystemExit(1)
if status != "done":
    print(f"FAIL неожиданный status={status!r}"); raise SystemExit(1)

extra = set(doc) - {"job_id", "status", "verdict", "violations"}
if extra:
    warnings.append(f"лишние поля в ответе: {sorted(extra)}")

verdict = doc.get("verdict")
if verdict not in ("violation", "no_violation"):
    print(f"FAIL verdict={verdict!r} (ожидается violation|no_violation)"); raise SystemExit(1)

violations = doc.get("violations")
if not isinstance(violations, list):
    print(f"FAIL violations не список: {type(violations).__name__}"); raise SystemExit(1)
if verdict == "no_violation" and violations:
    print("FAIL verdict=no_violation, но violations непустой"); raise SystemExit(1)
if verdict == "violation" and not violations:
    print("FAIL verdict=violation, но violations пустой"); raise SystemExit(1)

frames_total, frames_ok, bytes_total = 0, 0, 0
try:
    import numpy as np
    import cv2
    have_cv2 = True
except Exception:
    have_cv2 = False

for i, v in enumerate(violations, 1):
    if not isinstance(v, dict):
        print(f"FAIL violations[{i-1}] не объект"); raise SystemExit(1)
    vtype = v.get("type")
    if not isinstance(vtype, str) or not vtype.strip():
        print(f"FAIL violations[{i-1}].type пустой"); raise SystemExit(1)
    tc = v.get("timecode_sec")
    if not isinstance(tc, (int, float)) or tc < 0:
        print(f"FAIL violations[{i-1}].timecode_sec={tc!r}"); raise SystemExit(1)
    desc = v.get("description")
    if not isinstance(desc, str) or not desc.strip():
        print(f"FAIL violations[{i-1}].description пустой"); raise SystemExit(1)
    frames = v.get("evidence_frames")
    if not isinstance(frames, list) or not (1 <= len(frames) <= 3):
        print(f"FAIL violations[{i-1}].evidence_frames: {type(frames).__name__} len={len(frames) if isinstance(frames, list) else 'n/a'} (ожидается 1..3)")
        raise SystemExit(1)
    for j, fr in enumerate(frames, 1):
        frames_total += 1
        if not isinstance(fr, dict):
            print(f"FAIL evidence_frames[{j-1}] не объект"); raise SystemExit(1)
        ts = fr.get("timestamp_sec")
        if not isinstance(ts, (int, float)) or ts < 0:
            print(f"FAIL evidence_frames[{j-1}].timestamp_sec={ts!r}"); raise SystemExit(1)
        b64 = fr.get("image_base64")
        if not isinstance(b64, str) or not b64:
            print(f"FAIL evidence_frames[{j-1}].image_base64 пустой"); raise SystemExit(1)
        try:
            blob = base64.b64decode(b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL evidence_frames[{j-1}]: base64 не декодируется ({exc})"); raise SystemExit(1)
        bytes_total += len(blob)
        # Контракт: это именно JPEG (как в schemas.py приложения) — проверяем маркеры и декодирование.
        if not (blob[:2] == b"\xff\xd8" and blob[-2:] == b"\xff\xd9"):
            print(f"FAIL evidence_frames[{j-1}]: не JPEG (нет SOI/EOI), {len(blob)} байт"); raise SystemExit(1)
        if have_cv2:
            img = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                print(f"FAIL evidence_frames[{j-1}]: JPEG не декодируется cv2"); raise SystemExit(1)
            if img.size == 0:
                print(f"FAIL evidence_frames[{j-1}]: cv2 вернул пустое изображение"); raise SystemExit(1)
            frames_ok += 1
        else:
            frames_ok += 1

decoder = "cv2" if have_cv2 else "struct"
msg = (f"OK verdict={verdict} violations={len(violations)} "
       f"frames={frames_ok}/{frames_total} jpeg_decoded={decoder} evidence_kb={bytes_total//1024}")
for w in warnings:
    msg += f"\nWARN {w}"
print(msg)
PY
}

# ---------------------------------------------------------------------------------------
# --validate-file: проверить сохранённый ответ задачи по контракту (офлайн, без сети)
# ---------------------------------------------------------------------------------------
if [[ -n "$VALIDATE_FILE" ]]; then
  if [[ ! -f "$VALIDATE_FILE" ]]; then
    fv_err "Файл не найден: $VALIDATE_FILE"; exit 2
  fi
  fv_info "Проверяю сохранённый ответ: $VALIDATE_FILE"
  if out="$(validate_done_json "$VALIDATE_FILE" 2>&1)"; then
    fv_ok "$out"
    exit 0
  else
    fv_err "$out"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------------------
# --selftest: доказательство, что валидатор умеет ПАДАТЬ (негативные проверки, без сети)
# ---------------------------------------------------------------------------------------
if [[ $SELFTEST -eq 1 ]]; then
  printf '%sForteVision healthcheck — selftest валидатора%s  (без сети, без CPU)\n\n' "$C_BOLD" "$C_OFF"
  FIXDIR="$TMPDIR_RUN/selftest"; mkdir -p "$FIXDIR"

  if ! "$PY" - "$FIXDIR" <<'PY'
import base64, json, os, sys

out = sys.argv[1]
try:
    import numpy as np
    import cv2
except Exception as exc:  # noqa: BLE001
    print(f"selftest требует numpy+cv2: {exc}", file=sys.stderr)
    raise SystemExit(2)

img = np.zeros((48, 64, 3), dtype=np.uint8)
img[:] = (40, 40, 200)
cv2.rectangle(img, (8, 8), (30, 40), (240, 240, 240), -1)
ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
if not ok:
    print("не удалось закодировать JPEG", file=sys.stderr); raise SystemExit(2)
jpeg = buf.tobytes()
_, png = cv2.imencode(".png", img)

good = {"job_id": "selftest0001", "status": "done", "verdict": "violation",
        "violations": [{"type": "Предполагаемое оставление рабочего места",
                        "timecode_sec": 4.5,
                        "description": "наблюдение: касса без сотрудника; гипотеза: нарушение",
                        "evidence_frames": [{"timestamp_sec": 4.5,
                                             "image_base64": base64.b64encode(jpeg).decode()}]}]}

def dump(name, doc):
    with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)

def mutant(mod):
    doc = json.loads(json.dumps(good)); mod(doc); return doc

dump("good.json", good)
dump("failed.json", {"job_id": "selftest0002", "status": "failed", "error": "проба"})
dump("nokey_violations.json", mutant(lambda d: d.pop("violations")))
dump("mismatch.json", mutant(lambda d: d.update(violations=[])))
dump("no_violation_with_events.json", mutant(lambda d: d.update(verdict="no_violation")))

# сменить формат картинки: PNG вместо JPEG (ловится проверкой маркеров SOI/EOI)
dump("not_jpeg.json", mutant(lambda d: d["violations"][0]["evidence_frames"][0].update(
    image_base64=base64.b64encode(png.tobytes()).decode())))

# обрезать JPEG — теряется маркер EOI и декодирование ломается
dump("truncated_jpeg.json", mutant(lambda d: d["violations"][0]["evidence_frames"][0].update(
    image_base64=base64.b64encode(jpeg[: int(len(jpeg) * 0.6)]).decode())))

dump("bad_b64.json", mutant(lambda d: d["violations"][0]["evidence_frames"][0].update(
    image_base64="!!!не-base64!!!")))
dump("no_frames.json", mutant(lambda d: d["violations"][0].update(evidence_frames=[])))

with open(os.path.join(out, "not_json.json"), "w", encoding="utf-8") as fh:
    fh.write("<html>502 Bad Gateway</html>")
full = json.dumps(good, ensure_ascii=False)
with open(os.path.join(out, "truncated.json"), "w", encoding="utf-8") as fh:
    fh.write(full[: len(full) // 2])
PY
  then
    fv_err "не удалось подготовить фикстуры selftest (нужен venv-python с numpy и cv2)"
    exit 2
  fi

  sfail=0
  selftest_case() {  # selftest_case <file> <ожидаемый_rc> <описание>
    local file="$1" expect="$2" desc="$3" out rc
    out="$(validate_done_json "$file" 2>&1)"; rc=$?
    if [[ "$rc" == "$expect" ]]; then
      printf '  %s✓%s %-36s rc=%s  %s\n' "$C_GREEN" "$C_OFF" "$desc" "$rc" "${out%%$'\n'*}"
    else
      printf '  %s✗%s %-36s rc=%s (ожидался %s)  %s\n' "$C_RED" "$C_OFF" "$desc" "$rc" "$expect" "${out%%$'\n'*}"
      sfail=$((sfail + 1))
    fi
  }

  fv_info "Ожидаем PASS на корректном ответе и FAIL на каждом виде порчи:"
  selftest_case "$FIXDIR/good.json"                     0 "корректный done-ответ"
  selftest_case "$FIXDIR/failed.json"                   1 "status=failed"
  selftest_case "$FIXDIR/not_json.json"                 1 "не JSON (502 от прокси)"
  selftest_case "$FIXDIR/truncated.json"                1 "обрезанный JSON"
  selftest_case "$FIXDIR/nokey_violations.json"         1 "нет поля violations"
  selftest_case "$FIXDIR/mismatch.json"                 1 "verdict=violation, список пуст"
  selftest_case "$FIXDIR/no_violation_with_events.json" 1 "verdict=no_violation, список полон"
  selftest_case "$FIXDIR/not_jpeg.json"                 1 "в evidence не JPEG (PNG)"
  selftest_case "$FIXDIR/truncated_jpeg.json"           1 "обрезанный JPEG в evidence"
  selftest_case "$FIXDIR/bad_b64.json"                  1 "не-base64 в evidence"
  selftest_case "$FIXDIR/no_frames.json"                1 "пустой evidence_frames"

  printf '\n'
  if [[ $sfail -eq 0 ]]; then
    fv_ok "selftest PASS: 11/11 проверок ведут себя ожидаемо"
    exit 0
  fi
  fv_err "selftest FAIL: $sfail из 11 проверок ведут себя неверно"
  exit 1
fi

# ---------------------------------------------------------------------------------------
# один целевой адрес
# ---------------------------------------------------------------------------------------
TARGET_FAILURES=0
TARGET_TOTAL=0

check_target() {
  local label="$1" base="$2"
  local base_clean="${base%/}"
  local f="$TMPDIR_RUN/$(printf '%s' "$label" | tr -c 'a-zA-Z0-9' '_')"
  local failed=0
  local t_start t_end

  fv_step "Проверка [$label] $base_clean"
  TARGET_TOTAL=$((TARGET_TOTAL + 1))

  # --- 1. health ---------------------------------------------------------------------
  local code
  code="$(curl -s -o "$f.health.json" -w '%{http_code}' --max-time 15 "$base_clean/api/v1/health" 2>/dev/null || printf '000')"
  if [[ "$code" == "200" ]]; then
    local health_json
    health_json="$(cat "$f.health.json" 2>/dev/null | tr -d '\n' | cut -c1-160)"
    printf '  [1/6] GET  /api/v1/health              %s200%s  %s\n' "$C_GREEN" "$C_OFF" "$health_json"
  else
    printf '  [1/6] GET  /api/v1/health              %s%s%s  %s\n' "$C_RED" "$code" "$C_OFF" "$(head -c 160 "$f.health.json" 2>/dev/null | tr -d '\n')"
    failed=1
  fi

  # --- 2. 401 без ключа ----------------------------------------------------------------
  code="$(curl -s -o "$f.noauth.json" -w '%{http_code}' --max-time 15 \
        -X POST "$base_clean/api/v1/analyze" -F "file=@$CHECK_FILE;type=video/quicktime" 2>/dev/null || printf '000')"
  case "$code" in
    401|403) printf '  [2/6] POST /api/v1/analyze без ключа   %s%s%s  (верно: доступ закрыт)\n' "$C_GREEN" "$code" "$C_OFF" ;;
    000)     printf '  [2/6] POST /api/v1/analyze без ключа   %s000%s  соединение не установлено\n' "$C_RED" "$C_OFF"; failed=1 ;;
    *)       printf '  [2/6] POST /api/v1/analyze без ключа   %s%s%s  %s\n' "$C_RED" "$code" "$C_OFF" \
              "ОЖИДАЛСЯ 401 — API открыт без ключа! $(head -c 120 "$f.noauth.json" | tr -d '\n')"; failed=1 ;;
  esac

  # --- 3. POST с ключом ----------------------------------------------------------------
  t_start=$(date +%s)
  code="$(curl -s -o "$f.accept.json" -w '%{http_code}' --max-time "$UPLOAD_TIMEOUT" \
        -X POST "$base_clean/api/v1/analyze" \
        -H "X-API-Key: $API_KEY" \
        -F "file=@$CHECK_FILE;type=video/quicktime" 2>/dev/null || printf '000')"
  local job_id
  job_id="$("$PY" -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("job_id") or "")
except Exception: print("")' "$f.accept.json" 2>/dev/null)"
  if [[ "$code" == "202" && -n "$job_id" ]]; then
    printf '  [3/6] POST /api/v1/analyze (%s МБ)   %s202%s  job_id=%s\n' "$FILE_SIZE_MB" "$C_GREEN" "$C_OFF" "$job_id"
  else
    printf '  [3/6] POST /api/v1/analyze (%s МБ)   %s%s%s  %s\n' "$FILE_SIZE_MB" "$C_RED" "$code" "$C_OFF" \
      "$(head -c 200 "$f.accept.json" 2>/dev/null | tr -d '\n')"
    failed=1
  fi

  # --- 4. опрос до done ---------------------------------------------------------------
  local job_done=0 elapsed=0 status=""
  if [[ -n "$job_id" ]]; then
    while (( elapsed < TIMEOUT )); do
      code="$(curl -s -o "$f.job.json" -w '%{http_code}' --max-time 15 \
            -H "X-API-Key: $API_KEY" "$base_clean/api/v1/jobs/$job_id" 2>/dev/null || printf '000')"
      status="$("$PY" -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("status") or "")
except Exception: print("")' "$f.job.json" 2>/dev/null)"
      if [[ "$status" == "done" || "$status" == "failed" ]]; then job_done=1; break; fi
      sleep 1; elapsed=$(( $(date +%s) - t_start ))
    done
    if [[ $job_done -eq 1 && "$status" == "done" ]]; then
      printf '  [4/6] GET  /api/v1/jobs/%s    %sdone%s за %s с (лимит %s с)\n' "$job_id" "$C_GREEN" "$C_OFF" "$elapsed" "$TIMEOUT"
    elif [[ $job_done -eq 1 ]]; then
      printf '  [4/6] GET  /api/v1/jobs/%s    %sfailed%s через %s с\n' "$job_id" "$C_RED" "$C_OFF" "$elapsed"; failed=1
    else
      printf '  [4/6] GET  /api/v1/jobs/%s    %sтаймаут >%s с%s (последний status=%s)\n' "$job_id" "$C_RED" "$TIMEOUT" "$C_OFF" "$status"; failed=1
    fi
  else
    printf '  [4/6] опрос задачи                      пропущен (нет job_id)\n'; failed=1
  fi

  # --- 5. валидация JSON --------------------------------------------------------------
  if [[ -f "$f.job.json" && "$status" == "done" ]]; then
    local v_out v_rc
    v_out="$(validate_done_json "$f.job.json" 2>&1)"; v_rc=$?
    local first_line="${v_out%%$'\n'*}"
    if [[ $v_rc -eq 0 ]]; then
      printf '  [5/6] контракт результата               %s%s\n' "$C_GREEN" "$first_line$C_OFF"
      # остальные строки — предупреждения валидатора
      local rest="${v_out#*$'\n'}"
      [[ "$rest" != "$v_out" ]] && printf '        %s%s%s\n' "$C_YELLOW" "$(printf '%s' "$rest" | tr '\n' ' ')" "$C_OFF"
    else
      printf '  [5/6] контракт результата               %s%s%s\n' "$C_RED" "$first_line" "$C_OFF"; failed=1
    fi
  else
    printf '  [5/6] контракт результата               пропущен (нет done-ответа)\n'; failed=1
  fi

  # --- 6. торчащие служебные эндпоинты -------------------------------------------------
  local exposed="" p pcode
  for p in /docs /openapi.json /redoc /api/docs /api/openapi.json /api/redoc; do
    pcode="$(fv_http_code "$base_clean$p" 10)"
    [[ "$pcode" == "200" ]] && exposed="$exposed $p"
  done
  if [[ -z "$exposed" ]]; then
    printf '  [6/6] служебные /docs,/redoc,/openapi %sзакрыты%s\n' "$C_GREEN" "$C_OFF"
  else
    if [[ $STRICT_DOCS -eq 1 ]]; then
      printf '  [6/6] служебные эндпоинты              %sОТКРЫТЫ:%s%s (строгий режим → FAIL)\n' "$C_RED" "$exposed" "$C_OFF"; failed=1
    else
      printf '  [6/6] служебные эндпоинты              %sОТКРЫТЫ:%s%s (не блокирует; см. --strict-docs)\n' "$C_YELLOW" "$exposed" "$C_OFF"
    fi
  fi

  if [[ $failed -eq 0 ]]; then
    fv_ok "  ИТОГ [$label]: PASS"
  else
    fv_err "  ИТОГ [$label]: FAIL"
    TARGET_FAILURES=$((TARGET_FAILURES + 1))
  fi
  return $failed
}

# ---------------------------------------------------------------------------------------
# прогон
# ---------------------------------------------------------------------------------------
printf '%sForteVision healthcheck%s  %s\n' "$C_BOLD" "$C_OFF" "$(date '+%Y-%m-%d %H:%M:%S %z')"
fv_info "файл     : $CHECK_FILE ($FILE_SIZE_MB МБ)"
fv_info "таймаут  : $TIMEOUT с (лимит жюри 300 с)"
fv_info "ключ     : ${API_KEY:0:4}…${API_KEY: -2} (из ${KEY_OVERRIDE:+--key}${KEY_OVERRIDE:-env})"

if [[ ${#BASES[@]} -eq 0 || -z "${BASES[0]:-}" ]]; then
  fv_err "Не задан публичный адрес. Укажи --base https://… или FV_PUBLIC_BASE_URL/NGROK_DOMAIN, либо запусти без --public."
  exit 2
fi

for b in "${BASES[@]}"; do
  [[ -z "$b" ]] && continue
  check_target "$([[ "$b" == "$LOCAL_BASE" ]] && echo local || echo public)" "$b" || true
done

printf '\n%s== ИТОГ ==%s\n' "$C_BOLD" "$C_OFF"
if [[ $TARGET_FAILURES -eq 0 ]]; then
  fv_ok "PASS: все обязательные проверки пройдены ($TARGET_TOTAL адрес(а))"
  exit 0
else
  fv_err "FAIL: проблемных адресов $TARGET_FAILURES из $TARGET_TOTAL"
  exit 1
fi
