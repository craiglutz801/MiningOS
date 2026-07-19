# Tax Sales — Phase 0 Baseline Report

**Date:** 2026-07-18  
**Branch:** `feature/tax-sales-patented-claims`  
**Commit at assessment:** `c09b97b` (from `main`)

## Current architecture

| Layer | Technology |
|---|---|
| Frontend | React 19 + TypeScript + Vite + Tailwind + React Router |
| Map | Leaflet / react-leaflet |
| Backend | FastAPI (`api_app` mounted at `/api`) — routes live on one app, not separate routers |
| DB | PostgreSQL + PostGIS; migrations are numbered SQL under `mining_os/sql/` applied by `init_db()` |
| Auth | Session cookie + `current_account_id()` |
| Deploy | Vercel frontend + Render API |

## Relevant existing files

- Nav: `frontend/src/Layout.tsx`
- Routes: `frontend/src/App.tsx`
- API client: `frontend/src/api.ts`
- API surface: `mining_os/api/main.py`
- Settings: `mining_os/config.py`
- Migrations: `mining_os/sql/001` … `021_share_links.sql`
- Migration runner: `mining_os/pipelines/run_all.py`
- Targets table: `areas_of_focus` (do not flood with tax records)
- MLRS helpers: `mining_os/services/blm_plss.py`, `fetch_claim_records.py`

## Feature flags today

None (`VITE_*` flags do not exist). Tax Sales will introduce the first feature-flag pattern.

## Baseline test results (pre-change)

```text
.venv/bin/python -m pytest -q
18 failed, 140 passed
```

All 18 failures are in `tests/test_area_edit_endpoints.py` (pre-existing; unrelated to Tax Sales).  
Do not treat those as regressions introduced by this module.

## Proposed files (Phase 1–2 vertical slice)

**Create**

- `mining_os/sql/022_tax_intel.sql`
- `mining_os/tax_intel/**` (config, enums, scoring, services, demo seed, API helpers)
- `frontend/src/features/tax-sales/**`
- `frontend/src/pages/TaxSales.tsx` (thin route shell)
- `docs/tax_sales/OVERVIEW.md`
- `tests/test_tax_intel_*.py`

**Modify**

- `mining_os/config.py` — tax feature flags
- `mining_os/pipelines/run_all.py` — register `022_tax_intel.sql`
- `mining_os/api/main.py` — mount tax-sales endpoints when enabled
- `frontend/src/App.tsx`, `Layout.tsx`, `api.ts`
- `.env.example`, `frontend/.env.development`
- `ROADMAP.md`

## Dependency changes

None for Phase 1–2. Playwright/OCR remain optional and flag-gated; not required at startup.

## Rollback plan

1. Set `ENABLE_TAX_SALES_API=false` and `VITE_ENABLE_TAX_SALES=false`.
2. Redeploy frontend/backend.
3. Leave `tax_intel` tables intact (additive; no destructive rollback required).
4. Existing Mining OS routes/tables/endpoints remain untouched.
