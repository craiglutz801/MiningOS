#!/usr/bin/env bash
# Start Mining_OS: one URL (http://localhost:8000), one command.
# Saves to frontend? Save → rebuild runs → refresh browser to see changes.
# Run from repo root:  bash scripts/start-web.sh

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Building frontend..."
(cd frontend && npm install --silent && npm run build)

# Start API in background
.venv/bin/uvicorn mining_os.api.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir mining_os &
UVICORN_PID=$!
cleanup() {
  kill "$UVICORN_PID" 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo "  → Open:  http://localhost:8000"
echo "  → Edit frontend files, save, then refresh the page to see changes."
echo "  → Press Ctrl+C to stop."
echo ""

# Watch frontend and rebuild on change (so refresh shows updates)
cd frontend
npx --yes chokidar-cli "src/**" "index.html" -c "npm run build && echo '  ✓ Build done — refresh your browser'"
# (when chokidar exits, trap runs and kills uvicorn)
