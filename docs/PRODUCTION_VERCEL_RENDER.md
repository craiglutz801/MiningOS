# Production deployment (Vercel + Render) and MLRS maintenance-fee detection

This document describes how **Mining OS** is deployed to production, how traffic flows between **Vercel** (frontend) and **Render** (API + bundled UI), and how we detect the **“Maintenance fee payment was not received…”** banner on BLM MLRS **claim case pages**. It also explains common failure modes—including why enrichment can appear to work in one environment (tests, CLI, or an earlier session) while the browser UI looks stuck or shows only `unknown` payment status.

---

## 1. High-level architecture

```text
┌─────────────────┐     HTTPS      ┌──────────────────────────────┐
│  User browser   │ ─────────────► │ Vercel (static React SPA)    │
│                 │                │ e.g. *.vercel.app / custom   │
└────────┬────────┘                └──────────────┬───────────────┘
         │                                         │
         │  fetch("/api/…") — same origin          │ server-side rewrite
         │  (browser never talks to Render        │
         │   hostname directly for API calls)      ▼
         │                               ┌──────────────────────────────┐
         └──────────────────────────────►│ Render Web Service           │
                                         │ mining-os-api (FastAPI)      │
                                         │ • Postgres (DATABASE_URL)    │
                                         │ • Playwright Chromium (MLRS) │
                                         └──────────────────────────────┘
```

- **Vercel** serves the compiled React app (HTML/JS/CSS). Any request whose path starts with `/api/` is **rewritten** on the server to your Render API origin so the browser still sees a **same-origin** `/api/...` URL (no CORS configuration needed for the typical setup).
- **Render** runs **FastAPI** (`uvicorn mining_os.api.main:app`). During the Render **build**, the repo installs Python dependencies, installs **Playwright Chromium**, runs **`npm ci && npm run build`** in `frontend/`, and ships `frontend/dist/` inside the image. At runtime, the API can serve the SPA from `frontend/dist` **when those files exist** (see §5).

---

## 2. Vercel (frontend)

### 2.1 Configuration file

The frontend contains `frontend/vercel.json`:

```json
{
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://miningos.onrender.com/api/:path*"
    },
    {
      "source": "/(.*)",
      "destination": "/index.html"
    }
  ]
}
```

**Meaning:**

1. **`/api/:path*`** — Every API call from the browser goes to `https://<your-vercel-domain>/api/...`. Vercel **proxies** that to `https://miningos.onrender.com/api/...`. Update the destination hostname if your Render service URL changes.
2. **`/(.*)` → `/index.html`** — Standard single-page application (SPA) fallback so client-side routes (React Router) work on refresh and deep links.

### 2.2 Build & project settings (checklist)

In the Vercel dashboard, point the project at the **`frontend/`** directory (or monorepo equivalent):

| Setting | Typical value |
|--------|----------------|
| Root directory | `frontend` |
| Framework | Vite |
| Build command | `npm run build` |
| Output directory | `dist` |

Environment variables on Vercel are usually **not** required for the API base URL because the app uses relative paths (`/api/...`) in production—see `frontend/src/api.ts` (`BASE = "/api"`).

### 2.3 Local development contrast

Locally, **Vite** proxies `/api` to the FastAPI process (default `http://127.0.0.1:8000`) with long timeouts for heavy operations—see `frontend/vite.config.ts`. Use `http://127.0.0.1:5173` if `localhost` resolves to IPv6-only edge cases on your machine.

---

## 3. Render (backend API)

### 3.1 Blueprint: `render.yaml`

The repository root includes `render.yaml` (Render Blueprint). Important excerpts:

- **Build command** (must match Dashboard if you override manually):

  ```bash
  pip install --upgrade pip && pip install -r requirements.txt && python -m playwright install chromium && cd frontend && npm ci && npm run build
  ```

  **Why Playwright is in the build:** MLRS case pages are a **Salesforce Lightning** SPA. A plain HTTP GET returns mostly shell HTML; the red **maintenance-fee** banner is rendered in the browser. **Playwright headless Chromium** is used to load the real page and read the DOM.

- **Start command:**

  ```bash
  uvicorn mining_os.api.main:app --host 0.0.0.0 --port $PORT
  ```

- **Health check:** `GET /api/health` (`healthCheckPath: /api/health` in the blueprint).

- **Environment:** `MINING_OS_MLRS_PAYMENT_HEADLESS=1` is set in the blueprint so production **will** attempt headless browser enrichment (subject to a successful Chromium install).

You must still configure **secrets** in the Render dashboard (not committed):

- `DATABASE_URL` — Postgres connection string for production.
- `OPENAI_API_KEY` — if you use PDF / AI features.

### 3.2 Railway / Nixpacks note

`nixpacks.toml` exists for providers that use Nixpacks (e.g. Railway). It pins **Python + Node**, runs `playwright install chromium`, and builds the frontend similarly. The same operational lessons as Render apply.

### 3.3 Postgres

Production uses managed Postgres (or another host) via `DATABASE_URL`. Local development often uses Docker Compose (`docker-compose.yml`) with PostGIS; see the Dashboard copy and `README.md` for `docker compose up -d` and `python -m mining_os.pipelines.run_all --init-db`.

---

## 4. How the API serves the UI on Render (optional path)

`mining_os/api/main.py` mounts static assets when `frontend/dist/index.html` exists:

- `GET /` and SPA routes serve `frontend/dist/index.html`.
- `/assets/...` serves hashed bundles from `frontend/dist/assets`.

So **Render alone** can host both API and UI at `https://miningos.onrender.com`. The **Vercel** deployment is the “pretty” frontend hostname; it delegates `/api` to Render via `vercel.json`. Pick one primary UX or use both—just keep the **rewrite destination** in sync with the live Render URL.

---

## 5. Fetch Claim Records: UI vs long-running work

### 5.1 Why the UI uses `/start` + job polling

**Problem:** Running MLRS payment detection for many claims can take a long time (dozens of sequential browser navigations). Holding a single HTTP request open caused:

- Vite dev proxy / browser timeouts (“Failed to fetch” even when the server finished).
- Poor UX with no clean recovery.

**Solution** (implemented in `mining_os/api/main.py` and `frontend/src/api.ts`):

1. `POST /api/areas-of-focus/{id}/fetch-claim-records/start` returns immediately with `{ ok: true, job_id }`.
2. The worker thread runs the same `_safe_fetch_claim_records` logic as the synchronous endpoint.
3. The UI polls `GET /api/jobs/{job_id}` every few seconds until `status` is `done` or `error`.

**Important:** Job state lives in an **in-memory dict** on the worker process. That matches a **single** uvicorn worker on Render. If you ever scale to multiple workers, job polling would need Redis/Postgres-backed jobs—but claim results are still persisted on the area’s `characteristics.claim_records`, so a refresh can show completed data even if the job handle is lost.

### 5.2 Symptom: button shows “Fetching…” for a long time

This is often **not** a hung API—it is **many claims × ~tens of seconds per Playwright pass** (plus network variance). For large spatial queries, expect multi-minute (or longer) runs. Improvements (batching, parallelism, shorter timeouts) are product decisions; today the implementation favors correctness and BLM rate-limit safety over speed.

---

## 6. Maintenance-fee (“UNPAID”) detection — design

### 6.1 What we are trying to read

On MLRS, each claim has a **case page** URL (e.g. under `https://mlrs.blm.gov/s/blm-case/...`). When maintenance fees are overdue, Salesforce shows a prominent message containing:

> Maintenance fee payment was not received and may result in the closing of the claim.

That string is the **ground truth** we match (see `mining_os/services/mlrs_case_payment.py`).

### 6.2 Where this fits in Fetch Claim Records

`mining_os/services/fetch_claim_records.py`:

1. Resolve claims via **BLM_ClaimAgent** (optional, gated by env) or **ArcGIS / spatial** fallbacks.
2. Normalize claim rows (including `case_page`, `payment_report`, `serial_number`, etc.).
3. **Step 3 — payment enrichment:** call `enrich_claims_from_mlrs_case_pages(claims)` from `mlrs_case_payment.py`.
4. Persist under `characteristics.claim_records` and derive coarse area `status` when possible.

ArcGIS **does not** expose this banner text as structured fields—hence scraping.

### 6.3 Enrichment pipeline (per claim)

Implemented in `_enrich_claims_inproc`:

1. **HTTP GET `case_page`** — Occasionally the phrase appears in raw HTML; cheap when it works.
2. **HTTP RAS / Serial Register** (`payment_report` and related URLs) — Sometimes the same wording appears in BLM report HTML or iframes.
3. **Playwright (headless Chromium)** — Navigate with `wait_until="domcontentloaded"` and **poll** the DOM (Salesforce often never reaches “networkidle”). Detect unpaid via text / locator.
4. **Selenium** (optional fallback) — Used when Playwright does not run or does not resolve the status; requires a working ChromeDriver setup locally.

**Headless gating:** `_should_try_headless()` reads `MINING_OS_MLRS_PAYMENT_HEADLESS` / legacy `MINING_OS_MLRS_PAYMENT_SELENIUM`. On PaaS hosts (Render, Railway, etc.) the default is **off** unless explicitly enabled—hence **`MINING_OS_MLRS_PAYMENT_HEADLESS=1`** in `render.yaml`.

### 6.4 Why a subprocess wrapper exists

Playwright’s synchronous API and uvicorn’s threading model had **hang** reports when launching Chromium repeatedly from **background worker threads**. `enrich_claims_from_mlrs_case_pages`:

- By default spawns a **child Python process** (`python -m mining_os.services.mlrs_case_payment`) with stdin/out JSON.
- Inside the child, `MINING_OS_MLRS_ENRICH_INPROC=1` runs the real enrichment inline.

The parent **must not** let the child fill stderr/stdout pipes blindly; the implementation drains stderr and stdout explicitly (see module docstring in `mlrs_case_payment.py`).

### 6.5 Subprocess logging vs JSON on stdout (critical bug — fixed)

The child process communicates enriched claims by writing **only JSON to stdout**. **Console logs must go to stderr.** Previously, calling `setup_logging()` in the child configured a `StreamHandler` on **stdout**, which prepended log lines to the same stream as the final `json.dumps(...)`. The parent then ran `json.loads` on the **combined** output, failed parsing, and fell back to **un-enriched** claims—so the UI showed **`payment_status: unknown`** everywhere even when Playwright would have worked.

**Fix:** `setup_logging(..., stream=sys.stderr)` for the subprocess entrypoint, plus `PYTHONUNBUFFERED=1` in the child environment for timely stderr logs.

### 6.6 Diagnostics you can hit in production

- `GET /api/diag/environment` — Includes `mlrs_payment` block: whether headless will run, Playwright import, env vars.
- `GET /api/diag/check-payment?case_url=...` — Runs **only** payment enrichment for one MLRS case URL (proves Playwright + network without full fetch).

---

## 7. Local setup for MLRS payment (developer machine)

From repo root:

```bash
bash scripts/setup_mlrs_payment_local.sh
```

This installs Python deps and runs `python -m playwright install chromium`.

**Environment:**

- Default on non-PaaS hosts: headless tends to be **on** (see `_should_try_headless()`).
- You can force behavior with `MINING_OS_MLRS_PAYMENT_HEADLESS=0|1` in `.env`.

---

## 8. “It worked before — why not now?” — troubleshooting matrix

| Symptom | Likely cause | What to check |
|--------|----------------|---------------|
| Production: all `payment_status` are `unknown` | Chromium not installed on build host, or headless disabled | Render build logs for `playwright install chromium`; `GET /api/diag/environment`; ensure `MINING_OS_MLRS_PAYMENT_HEADLESS=1` on Render |
| Local: Playwright says executable missing | Playwright browsers not installed or wrong cache path | Run `python -m playwright install chromium`; inspect error path in logs |
| IDE / agent spawned uvicorn: weird Playwright path under `/var/.../cursor-sandbox-cache/...` | Tooling sandbox redirects browser cache | Run the API from a normal terminal session outside sandbox restrictions |
| UI stuck on “Fetching…” | Very large claim count × slow Playwright; job still running | Watch API logs; poll `/api/jobs/{id}`; reduce PLSS scope or wait |
| UI stuck + no progress | DB offline / API errors on poll | Browser devtools Network tab on `/api/jobs/...` |
| Tests passed but UI bad | Tests mock enrichment or run in-process without subprocess JSON protocol | Run `GET /api/diag/check-payment` against a known unpaid case URL on the same server |
| Selenium warnings | ChromeDriver / headless sandbox issues on macOS | Prefer Playwright; fix Selenium separately |

---

## 9. Related files (quick reference)

| Area | Path |
|------|------|
| Vercel rewrites | `frontend/vercel.json` |
| Vite proxy / timeouts | `frontend/vite.config.ts` |
| API client (job polling) | `frontend/src/api.ts` |
| Render blueprint | `render.yaml` |
| Nixpacks / Railway | `nixpacks.toml` |
| FastAPI app, jobs, diag routes | `mining_os/api/main.py` |
| Fetch Claim Records orchestration | `mining_os/services/fetch_claim_records.py` |
| MLRS payment enrichment | `mining_os/services/mlrs_case_payment.py` |
| Logging | `mining_os/logging_setup.py` |
| Local Playwright setup script | `scripts/setup_mlrs_payment_local.sh` |
| Endpoint safety contract / CI | `TESTING.md` |

---

## 10. Operational checklist before trusting UNPAID in production

1. Render **build** completed `python -m playwright install chromium` without error.
2. `MINING_OS_MLRS_PAYMENT_HEADLESS=1` set on the web service.
3. `GET /api/diag/environment` shows Playwright installed and headless willing to run.
4. `GET /api/diag/check-payment?case_url=...` returns `payment_status` **`unpaid`** for a case you know is overdue (or `paid` for a clean case).
5. Run **Fetch Claim Records** on a small target first to validate end-to-end latency.

---

*Last updated: aligned with repo behavior as of the subprocess logging fix (MLRS JSON on stdout) and current `render.yaml` / `vercel.json`.*
