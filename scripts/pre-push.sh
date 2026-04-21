#!/usr/bin/env bash
# Run the test suite before pushing.
# Install as a git hook with:
#   ln -sf ../../scripts/pre-push.sh .git/hooks/pre-push
# Or run manually: bash scripts/pre-push.sh

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
  else
    PY="python3"
  fi
fi

echo "==> Running pytest with $PY"
"$PY" -m pytest -q

echo ""
echo "==> All tests passed. Safe to push to production."
