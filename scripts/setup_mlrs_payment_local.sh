#!/usr/bin/env bash
# One-shot local setup for MLRS overdue-banner detection (Playwright + Chromium).
# Run from repo root: bash scripts/setup_mlrs_payment_local.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -d .venv ]]; then
  echo "Create a venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
echo "OK: Playwright Chromium installed. Set MINING_OS_MLRS_PAYMENT_HEADLESS=1 in .env (already in template) and restart the API."
