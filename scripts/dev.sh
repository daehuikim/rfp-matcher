#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/backend/.venv"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

if [[ ! -d "$VENV" ]]; then
  echo "venv not found. Run ./scripts/setup.sh first." >&2
  exit 1
fi

free_port() {
  local port=$1
  local pids
  pids=$(lsof -ti "tcp:${port}" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "Port ${port} in use — stopping previous process(es): ${pids}"
    # shellcheck disable=SC2086
    kill ${pids} 2>/dev/null || true
    sleep 1
  fi
}

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [[ -f "$ROOT/config/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/config/.env"
  set +a
fi

free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"
rm -f "$ROOT/frontend/.next/dev/lock"

cleanup() {
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Backend  http://127.0.0.1:${BACKEND_PORT}"
(
  cd "$ROOT/backend"
  PYTHONPATH=. uvicorn app.main:app --reload --port "$BACKEND_PORT"
) &
BACKEND_PID=$!

echo "==> Frontend http://localhost:${FRONTEND_PORT}"
(
  cd "$ROOT/frontend"
  npm run dev -- --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

wait
