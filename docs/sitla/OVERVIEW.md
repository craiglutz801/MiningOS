# SITLA Intelligence

Additive Mining OS module for Utah School and Institutional Trust Lands (SITLA) mineral opportunities.

## What shipped

- Isolated schema `sitla_intel` (`024_sitla_intel.sql`)
- Feature flags (default **off** in production)
- Deterministic scoring `sitla-v1.0`
- API under `/api/sitla/*`
- Frontend tab **SITLA** (summary, filters, table + detail, review, sources)
- Demo fixtures + multi-source ingest (hub / past auctions / public notices / offerings)
- Geometry + MLRS enrichment, historical offering matches, watchlist alerts
- Promote-to-Target (explicit only)
- Scheduler hook when `ENABLE_SITLA_JOBS=true`

## Enable locally

```bash
.venv/bin/python -m mining_os.pipelines.run_all --init-db

# API .env
ENABLE_SITLA_API=true
ENABLE_SITLA_JOBS=true   # optional

# Frontend already sets VITE_ENABLE_SITLA=true in .env.development
cd frontend && npm run dev
```

## Discovery pipeline

Refresh (`POST /api/sitla/jobs/refresh` or scheduled tick) runs:

1. Ensure sources + demo seed exist
2. Ingest all enabled sources (fixture JSON by default)
3. Enrich (geometry versions + optional MLRS claim context)
4. Match historical offerings / comparables
5. Detect watchlist changes (+ deliver email when SMTP configured)

### Live HTML (opt-in)

Hub adapters use `HtmlHubAdapter` only when `sources.configuration_json.allow_live_html=true`.
Until a hub is validated, keep `use_fixture=true` and the packaged fixtures under
`mining_os/sitla_intel/fixtures/`.

## Safety

- Never auto-creates Targets
- No external fetch on page load
- Failed SITLA sources never take down other Mining OS pages
- Official SITLA documents govern rights/fees/awards — scores are decision-support only

## Official sources monitored (hubs)

- https://trustlands.utah.gov/work-with-us/energy-minerals/
- https://trustlands.utah.gov/work-with-us/energy-minerals/past-auctions/
- https://trustlands.utah.gov/work-with-us/public-notice/
