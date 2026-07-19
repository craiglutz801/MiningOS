# Tax Sales — Patented Claim Watch

Additive Mining OS module for tax-delinquent / tax-sale intelligence across Utah, Idaho, and Nevada.

## What shipped

### Phase 1–2 (foundation)

- Isolated PostgreSQL schema `tax_intel` (`022_tax_intel.sql`)
- Feature flags (API + frontend), default **off** in production
- Deterministic scoring (`tax-v1.0`)
- API under `/api/tax-sales/*`
- Frontend tab **Tax Sales** (summary, filters, detail, review, coverage)
- Pilot-county demo fixtures seeded per account on first API use

### Phase 3+ (live pipeline)

- Migration `023_tax_intel_phase3.sql` — `alert_events`, `parcel_geometry_versions`, `mineral_surveys`, enrichment columns
- Reusable adapters: fixture JSON, CSV, HTML table (opt-in), ArcGIS FeatureServer
- Nine pilot-county packaged fixtures under `mining_os/tax_intel/fixtures/`
- Ingest orchestration (`source_runs`, `raw_artifacts`, append-only `tax_observations`)
- Enrichment: parcel geometry versions, legal/MS/PLSS parse, GLO review tasks, MLRS claim context
- Promote-to-Target (explicit only — never auto-floods `areas_of_focus`)
- Watchlist change detection + alert delivery (SMTP when configured)
- Scheduler hook when `ENABLE_TAX_SALES_JOBS=true` (hourly cadence inside 60s tick)
- Admin/manual: `POST /tax-sales/jobs/refresh`, CSV upload, alerts list

## Enable locally

```bash
# 1) Apply migrations
.venv/bin/python -m mining_os.pipelines.run_all --init-db

# 2) API .env
ENABLE_TAX_SALES_API=true
ENABLE_TAX_SALES_JOBS=true          # scheduled + manual refresh
# optional:
# ENABLE_TAX_SALES_ADMIN=true
# ALERT_EMAIL=you@example.com       # watchlist email delivery

# 3) Frontend
# frontend/.env.development already has VITE_ENABLE_TAX_SALES=true
cd frontend && npm run dev
```

## How content stays updated

1. **Scheduled** (when `ENABLE_TAX_SALES_JOBS=true`): automation scheduler calls tax jobs ~hourly → enabled non-manual sources ingest → enrich → watchlist alerts.
2. **Manual**: Tax Sales UI **Refresh sources** button, or `POST /api/tax-sales/jobs/refresh`.
3. **CSV upload**: `POST /api/tax-sales/admin/upload-csv?source_key=...` for counties without stable feeds.
4. **Live HTML/ArcGIS**: set `configuration_json.allow_live_html=true` or `layer_url` / `parcel_layer_url` on a source. Default pilots use packaged fixtures so refresh is reliable without fragile scrapers.

## Safety rules

- Never floods `areas_of_focus` automatically (promote is explicit)
- Never overwrites historical tax observations
- Source coverage language: “All publicly available records from enabled and healthy sources.”
- Patent/tax status ≠ mineral ownership (disclaimer always shown)
- No CAPTCHA bypass / no LLM final legal decisions
- Failed adapter/job must never take down other Mining OS endpoints

## Pilot counties

| State | Counties |
|-------|----------|
| UT | Beaver, Juab, Tooele |
| ID | Shoshone, Custer, Lemhi |
| NV | White Pine, Nye, Elko |
