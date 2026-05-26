#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Backend: venv + dependencies"
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"

echo "==> Frontend: npm dependencies"
cd "$ROOT/frontend"
npm install

echo "==> PDF tools (pdf2html, optional — needs Java)"
if command -v node >/dev/null 2>&1; then
  (cd "$ROOT/backend/tools/pdf2html" && npm install --silent) || true
fi

echo
echo "Setup complete."
echo "  source backend/.venv/bin/activate"
echo "  ./scripts/dev.sh"
