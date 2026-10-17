#!/bin/bash
# ForteVision — обёртка запуска бэкенда для launchd.
#
# launchd не читает ~/.zshrc и не знает про секреты, поэтому:
#   1) ставим предсказуемый PATH (иначе дочерний процесс анализа не найдёт ffprobe/ffmpeg,
#      а они лежат в /usr/local/bin — см. FV_PROJECT_ROOT в проде);
#   2) читаем единственный env-файл с секретами;
#   3) выставляем продовые дефолты и exec'аем штатный scripts/run_server.sh.
#
# Ручной запуск (для отладки, в терминале):  infra/macos/run_api.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

# launchd выдаёт минимальный PATH. ffprobe/ffmpeg установлены в /usr/local/bin:
# без них pipelines/job_entry меняют поведение (фолбэк на полное декодирование OpenCV).
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin${PATH:+:$PATH}"

if ! fv_load_env; then
  fv_warn "Не найден env-файл $FV_ENV_FILE — беру только унаследованные переменные окружения."
fi
# Порядок важен: сначала env-файл, потом значения по умолчанию (иначе файл не переопределит их).
fv_defaults

PY="$(fv_python || true)"
if [[ -z "$PY" ]]; then
  fv_err "Не найден python. Ожидался $FV_APP_ROOT/.venv/bin/python"
  exit 1
fi
if [[ ! -x "$FV_APP_ROOT/scripts/run_server.sh" ]]; then
  fv_err "Не найден $FV_APP_ROOT/scripts/run_server.sh (FV_APP_ROOT указывает не туда?)"
  exit 1
fi
if [[ -z "${FV_API_KEYS:-}" ]]; then
  fv_err "FV_API_KEYS пуст. Публичные эндпоинты закрыты, жюри не сможет отправить видео."
  fv_err "Заполни $FV_ENV_FILE (см. infra/env/forte-vision.env.example)"
  exit 1
fi

export FV_APP_ROOT FV_INFRA_ROOT
export FV_PYTHON="$PY"
export FV_WORKER_CONCURRENCY="${FV_WORKER_CONCURRENCY:-2}"
export FV_JOB_TIMEOUT_SEC="${FV_JOB_TIMEOUT_SEC:-280}"
# Дочерние процессы анализа наследуют окружение родителя (worker._child_env), поэтому PATH выше доедет и до них.
export PYTHONUNBUFFERED=1

mkdir -p "$FV_LOG_DIR"

fv_info "$(date '+%Y-%m-%d %H:%M:%S') run_api.sh: старт"
fv_info "  app_root          = $FV_APP_ROOT"
fv_info "  python            = $PY"
fv_info "  env_file          = $FV_ENV_FILE"
fv_info "  worker_concurrency= $FV_WORKER_CONCURRENCY"
fv_info "  job_timeout_sec   = $FV_JOB_TIMEOUT_SEC"
fv_info "  data_dir          = ${FV_DATA_DIR:-$FV_APP_ROOT/data}"

cd "$FV_APP_ROOT" || exit 1
exec "$FV_APP_ROOT/scripts/run_server.sh"
