#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  printf 'Source release check failed: %s\n' "$1" >&2
  exit 1
}

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  fail ".env is tracked; commit only .env.example"
fi

tracked_runtime_state="$(git ls-files data traces | grep -Ev '(^|/)\.gitkeep$' || true)"
if [[ -n "$tracked_runtime_state" ]]; then
  printf '%s\n' "$tracked_runtime_state" >&2
  fail "runtime state is tracked under data/ or traces/"
fi

git diff --check

if [[ "${ALLOW_DIRTY:-0}" != "1" ]] && [[ -n "$(git status --porcelain)" ]]; then
  fail "working tree is not clean; commit reviewed changes before release"
fi

scripts/check_full_stack_regression.sh

printf 'Source release boundary check passed.\n'
