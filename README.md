# Mining_OS

Mining Deal OS: priority minerals, areas of focus (claims/mines), BLM paid/unpaid checks, reports, and maps.

## Features

- **Minerals of interest** — Editable list (DB-backed) driving discovery and alerts. Default: Fluorspar, Beryllium, Tungsten, Uranium.
- **Areas of focus** — Table of claim/mine name, location (PLSS), mineral(s), status (paid/unpaid), report links, validity notes. Ingest from `data_files/` CSVs (Utah Dockets, PerspectiveMines, Bryson Review).
- **BLM integration** — Uses sibling **BLM_ClaimAgent** (by coords or PLSS) to check paid/unpaid and link to BLM case/payment pages.
- **Reports & validity** — Store and link to reports (e.g. Utah DMEA, MLRS); priority on existing govt/sample reports for proving minerals.
- **Email alerts** — High-priority unpaid claims (priority mineral + unpaid) can be emailed to `ALERT_EMAIL` (e.g. craiglutz801@gmail.com). Configure SMTP in `.env`.
- **Interactive map** — Plot areas of focus by lat/lon; color by status (paid/unpaid).
- **Legacy pipeline** — BLM/MRDS/PLSS ingestion and scored candidates still available.

## 0) Requirements
- Docker + Docker Compose
- Python 3.11+ (3.12 ok)
- Cursor (recommended)

## 1) Start PostGIS

```bash
cd Mining_OS
cp .env.example .env
docker compose up -d
```

- pgAdmin: http://localhost:5050
- Postgres: localhost:5432

## 2) Create a virtualenv + install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Optional (for Discovery agent: OpenAI + web search):
```bash
pip install openai duckduckgo-search
```
Then set `OPENAI_API_KEY` in `.env`.

## 3) Initialize DB schema

```bash
python -m mining_os.pipelines.run_all --init-db
```

## 4) Run ingestion + candidate build

```bash
python -m mining_os.pipelines.run_all --all
```

## 5) Run the web app (one command)

```bash
bash scripts/start-web.sh
```

- **Open:** http://localhost:8000 — React UI (Minerals, Areas, Map)
- **API docs:** http://localhost:8000/docs

The script builds the frontend, starts the API, and watches for frontend changes. Edit files under `frontend/src`, save — it rebuilds automatically. **Refresh your browser** to see changes. Stop with **Ctrl+C**.

## 6) Alternative: run API only

```bash
uvicorn mining_os.api.main:app --host 127.0.0.1 --port 8000
```

- **Web app:** http://localhost:8000 — clean React UI (Minerals, Areas of focus, Map)
- **API docs:** http://localhost:8000/docs

(If you haven’t built the frontend, build the frontend first (see section 5). The legacy Streamlit dashboard is still at `mining_os/dashboard/app.py`; run with `streamlit run mining_os/dashboard/app.py --server.port 8501`.)

## Notes / What "Candidate" means in MVP

MVP candidates are built by:

1. Selecting "open claims" (from BLM layer) in your focus states
2. Computing claim centroids
3. Finding MRDS occurrences within a radius (default 10 km)
4. Scoring based on commodity match + evidence density

## Dashboard pages

1. **Minerals of interest** — View/edit list; add/delete.
2. **Areas of focus** — Filter by mineral/status; table with report links; detail + "Check BLM" for paid/unpaid.
3. **Map** — Areas with coordinates; green = paid, red = unpaid.
4. **Candidates (legacy)** — Original BLM×MRDS scored candidates.

## Discovery agent

From the **Dashboard**, click **Discovery agent** to:

- **Edit prompts** — System and user prompt templates (per mineral or default). Use `{{mineral}}` and `{{states}}` in the user prompt; the agent finds locations/mines with least resistance to monetization, stays within target states, prioritizes known mines and existing reports (USGS, NGMDB, state surveys), and seeks lat/long, PLSS, and BLM claim status.
- **Run** — Choose **Replace** (clear existing discovery-sourced areas and add new) or **Add/supplement** (keep existing and add). The agent uses OpenAI to generate candidate locations, optional DuckDuckGo web search for report links, and BLM (via BLM_ClaimAgent or built-in PLSS/coords query) for claim status. Results are written to Areas of focus with `source=discovery_agent`.

Requires `OPENAI_API_KEY` in `.env`. Optional: `pip install openai duckduckgo-search`.

## BLM_ClaimAgent

To check paid/unpaid status, **BLM_ClaimAgent** must be available. Either:

- Keep `Mining_OS` and `BLM_ClaimAgent` as siblings under `Agents/`, or
- Set `MINING_OS_BLM_AGENT_PATH` to the path of the BLM_ClaimAgent folder.

## Alerts (email)

Set in `.env`: `ALERT_EMAIL`, and optionally `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` (or `APP_PASSWORD` for Gmail). Then use "Email priority unpaid" in the dashboard or `POST /alerts/send-priority-unpaid`.

## Next upgrades

- Discovery agent: OpenAI + web/NGMDB search for more areas and report links.
- ROI scoring: refine with claim density, report strength, market data.
- Deal packet generator; NGMDB/state report discovery and PDF citations.
