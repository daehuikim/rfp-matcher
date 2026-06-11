#!/usr/bin/env bash
# final batch — backend/.venv (opendataloader_pdf 포함) 사용
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/backend/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "backend/.venv 없음. 먼저:" >&2
  echo "  cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -e . opendataloader-pdf" >&2
  exit 1
fi
export PYTHONPATH="$ROOT/backend"
exec "$PY" "$ROOT/scripts/run_final_batch.py" "$@"
