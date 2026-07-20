# SITLA Intelligence — Phase 0 Baseline Report

**Branch:** `feature/sitla-intelligence`  
**Base commit:** `0660090` (main at branch creation)  
**Date:** 2026-07-19

## Architecture discovered

| Layer | Technology |
|-------|------------|
| Frontend | React + TypeScript + Vite, React Router |
| Backend | FastAPI (`mining_os/api/main.py`) |
| DB | PostgreSQL via SQLAlchemy; additive SQL files in `mining_os/sql/` |
| Map | Leaflet |
| Jobs | `automation_scheduler` 60s tick (Tax Sales jobs already hooked) |
| Targets | `areas_of_focus` via `upsert_area` |
| Pattern reference | `mining_os/tax_intel/` + Tax Sales UI |

## Relevant existing files

- `mining_os/api/main.py` — API registration, auth whitelist
- `mining_os/config.py` — feature flags
- `mining_os/pipelines/run_all.py` — migration sequence
- `mining_os/services/automation_scheduler.py` — job ticks
- `mining_os/services/areas_of_focus.py` — promote-to-Target
- `mining_os/tax_intel/**` — parallel module pattern
- `frontend/src/App.tsx`, `Layout.tsx`, `api.ts`

## API response pattern

`{ "ok": true|false, "error": null|string, ... }`

## Migration approach

Additive SQL: `024_sitla_intel.sql` creating schema `sitla_intel`. No Alembic required (repo uses `run_all --init-db`).

## Feature flags (default off)

```text
ENABLE_SITLA_API=false
ENABLE_SITLA_ADMIN=false
ENABLE_SITLA_JOBS=false
VITE_ENABLE_SITLA=false
```

## Known pre-existing failures

`test_area_edit_endpoints` suite historically has ~18 failures unrelated to SITLA (recorded in Tax Sales baseline).

## Rollback

1. Disable flags  
2. Stop SITLA jobs  
3. Redeploy prior revision  
4. Retain `sitla_intel` tables (additive)

## Files planned

- `mining_os/sql/024_sitla_intel.sql`
- `mining_os/sitla_intel/**`
- `frontend/src/features/sitla/**`
- `docs/sitla/OVERVIEW.md`
- API/nav/flag wiring + tests
