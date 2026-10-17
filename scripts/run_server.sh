#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${FV_PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "Create .venv and install apps/api/requirements.txt first (see README.md)." >&2
  exit 1
fi
if [[ -z "${FV_API_KEYS:-}" ]]; then
  echo "Set FV_API_KEYS in the environment; no built-in jury key exists." >&2
  exit 1
fi
export FV_PROJECT_ROOT="$ROOT"
export FV_DATA_DIR="${FV_DATA_DIR:-$ROOT/data}"
export PYTHONPATH="$ROOT/apps/api:$ROOT/packages/vision:$ROOT/packages/rules${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$ROOT"
exec "$PYTHON" -m uvicorn app.main:app --host "${FV_HOST:-0.0.0.0}" --port 8000 --workers 1
