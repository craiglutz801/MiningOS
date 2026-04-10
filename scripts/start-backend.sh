#!/usr/bin/env bash
# Start the API on port 8000 and wait until /api/health returns ok.
# Run from repo root:  bash scripts/start-backend.sh

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if command -v lsof &>/dev/null; then
  if lsof -i :8000 -sTCP:LISTEN -t &>/dev/null; then
    echo "Port 8000 already in use. Checking /api/health..."
    if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
      echo "  → Backend is up. API: http://localhost:8000/api"
      exit 0
    fi
    echo "  → Port 8000 in use but /api/health failed. Stop the process (e.g. kill \$(lsof -t -i :8000)) and run again."
    exit 1
  fi
fi

echo "Starting backend on http://127.0.0.1:8000 ..."
.venv/bin/uvicorn mining_os.api.main:app --host 127.0.0.1 --port 8000 &
UVICORN_PID=$!
trap "kill $UVICORN_PID 2>/dev/null || true" EXIT

# Wait for health (load .env from ROOT)
for i in {1..30}; do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "  → Backend is up. API: http://localhost:8000/api"
    trap - EXIT
    echo "  → Press Ctrl+C to stop the backend."
    wait $UVICORN_PID
    exit 0
  fi
  sleep 0.5
done

echo "  → Timeout waiting for /api/health"
kill $UVICORN_PID 2>/dev/null || true
exit 1
