#!/usr/bin/env bash
# Start Mining_OS: database, then API and dashboard.
# Usage: from repo root, run:  bash scripts/start.sh

set -e
cd "$(dirname "$0")/.."

echo "=== Mining_OS start ==="

# 1) Docker (PostGIS + pgAdmin)
if command -v docker &>/dev/null; then
  echo "Starting Docker Compose..."
  docker compose up -d
  echo "Waiting for Postgres to be ready..."
  sleep 5
else
  echo "Docker not found. Install Docker Desktop, then run: docker compose up -d"
  echo "Skipping DB start. API/dashboard will fail until DB is up."
fi

# 2) Python env
if [[ ! -d .venv ]]; then
  echo "Creating venv with Python 3.11+..."
  python3 -m venv .venv || /opt/homebrew/bin/python3.13 -m venv .venv
fi
source .venv/bin/activate
pip install -q -e .

# 3) Init DB (only if we have Docker / Postgres)
if command -v docker &>/dev/null; then
  echo "Initialising DB schema..."
  python -m mining_os.pipelines.run_all --init-db
  echo "Running pipeline (ingest + candidates, max 500 for quick test)..."
  python -m mining_os.pipelines.run_all --all --max-records 500
fi

# 4) Servers (run in background; stop with Ctrl+C in each terminal)
echo ""
echo "Start the API and dashboard in two terminals:"
echo "  Terminal 1:  source .venv/bin/activate && uvicorn mining_os.api.main:app --reload --port 8000"
echo "  Terminal 2:  source .venv/bin/activate && streamlit run mining_os/dashboard/app.py --server.port 8501"
echo ""
echo "  Dashboard: http://localhost:8501   API docs: http://localhost:8000/docs   pgAdmin: http://localhost:5050"
echo ""
