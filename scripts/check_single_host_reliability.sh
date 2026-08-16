#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

cd "$ROOT_DIR"

echo "[1/2] Deterministic single-host runtime contracts"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  tests/test_lifecycle_transactions.py \
  tests/test_storage.py \
  tests/test_main_agent_store.py \
  tests/test_main_agent_core.py \
  tests/test_api_a2a_adapter.py \
  tests/test_langgraph_runtime.py \
  tests/test_execution_context.py \
  tests/test_tool_invocation_ledger.py \
  tests/test_openai_compatible_client.py

echo "[2/2] Deterministic browser reliability regression"
pnpm --dir web run test:regression

echo "Single-host reliability baseline passed."
