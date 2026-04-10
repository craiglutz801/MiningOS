#!/usr/bin/env bash
# Start backend (if needed) and frontend dev server. One URL: http://localhost:5173
# Run from repo root:  bash scripts/dev.sh

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Start backend in background if port 8000 is free
BACKEND_STARTED=
if ! command -v lsof &>/dev/null; then
  echo "Starting backend (lsof not found, not checking port)..."
  .venv/bin/uvicorn mining_os.api.main:app --host 127.0.0.1 --port 8000 &
  BACKEND_PID=$!
  BACKEND_STARTED=1
  sleep 2
else
  if ! lsof -i :8000 -sTCP:LISTEN -t &>/dev/null; then
    echo "Starting backend on port 8000..."
    .venv/bin/uvicorn mining_os.api.main:app --host 127.0.0.1 --port 8000 &
    BACKEND_PID=$!
    BACKEND_STARTED=1
    for i in {1..20}; do
      if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
      sleep 0.5
    done
  else
    echo "Backend already running on port 8000."
  fi
fi

# Frontend dev server (proxies /api to 8000)
echo "Starting frontend dev server..."
cd frontend
npm run dev &
VITE_PID=$!

cleanup() {
  kill $VITE_PID 2>/dev/null || true
  if [[ -n "$BACKEND_STARTED" ]] && kill $BACKEND_PID 2>/dev/null; then true; fi
  exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo "  → Open:  http://localhost:5173"
echo "  → Discovery and all API calls go to the backend on port 8000."
echo "  → Press Ctrl+C to stop."
echo ""

wait $VITE_PID
