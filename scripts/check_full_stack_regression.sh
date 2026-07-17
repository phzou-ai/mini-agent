#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

cd "$ROOT_DIR"

echo "[1/4] Backend unit and integration tests"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m pytest -p no:cacheprovider

echo "[2/4] Frontend typecheck"
pnpm --dir web run typecheck

echo "[3/4] Frontend production build"
pnpm --dir web run build

echo "[4/4] Deterministic migrated-frontend regression"
pnpm --dir web run test:regression

if [[ "${RUN_LIVE_E2E:-0}" == "1" ]]; then
  echo "[live] Full Playwright suite"
  pnpm --dir web run test:e2e
fi

echo "Full-stack regression baseline passed."
