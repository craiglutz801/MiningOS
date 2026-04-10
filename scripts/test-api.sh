#!/usr/bin/env bash
# Quick test that the API is reachable (health + discovery prompts; optional discovery run).
# Run from repo root:  bash scripts/test-api.sh
#   BASE_URL=http://localhost:5173  — test via Vite proxy (frontend dev server)
#   SKIP_DISCOVERY_RUN=1            — skip the slow POST discovery/run test

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

echo "Testing API at $BASE_URL"
echo ""

# Health
echo -n "  GET /api/health ... "
if ! HEALTH=$(curl -sf "$BASE_URL/api/health" 2>/dev/null); then
  echo "FAIL (is the backend running? try: bash scripts/start-backend.sh)"
  exit 1
fi
echo "OK ($HEALTH)"

# Discovery prompts (proves discovery routes work)
echo -n "  GET /api/discovery/prompts ... "
if ! curl -sf "$BASE_URL/api/discovery/prompts" -o /dev/null 2>/dev/null; then
  echo "FAIL"
  exit 1
fi
echo "OK"

# Discovery run (optional; can take 1–2 min). Skip with: SKIP_DISCOVERY_RUN=1 bash scripts/test-api.sh
if [[ -z "$SKIP_DISCOVERY_RUN" ]]; then
  echo -n "  POST /api/discovery/run (limit_per_mineral=1, max 120s) ... "
  RESP=$(curl -sf -X POST "$BASE_URL/api/discovery/run?replace=false&limit_per_mineral=1" -H "Content-Type: application/json" --max-time 120 2>/dev/null) || true
  if echo "$RESP" | grep -q '"status"'; then
    echo "OK (response has status)"
    echo "    Sample: $(echo "$RESP" | head -c 120)..."
  else
    echo "FAIL or timeout (run with SKIP_DISCOVERY_RUN=1 for a quick health-only test)"
    exit 1
  fi
else
  echo "  POST /api/discovery/run ... skipped (SKIP_DISCOVERY_RUN=1)"
fi

echo ""
echo "All checks passed. You can use Run discovery in the app."
