# Mining OS — Roadmap

Living document of planned initiatives, features, and improvements.
Items are checked off when completed, with the completion date noted.

---

## Completed

- [x] **Process Mine PDF Report** — Upload historical mining PDF reports, AI extracts targets (PLSS, lat/long, minerals, county, notes) with two-pass processing (extraction + geo-location). Dashboard modal with review/select/import workflow. *(Completed 2026-03-15)*
- [x] **PDF linked to imported targets** — Uploaded PDFs are saved and associated with every target extracted from them via `report_links`. *(Completed 2026-03-15)*
- [x] **Mineral tag badges** — Minerals display as styled tag pills instead of plain text across all views (table, detail, duplicates, clean preview). *(Completed 2026-03-15)*
- [x] **Mineral name normalization** — All ingest paths (CSV, PDF, manual, merge) clean mineral names: title-case, strip numbers/parens, deduplicate. *(Completed 2026-03-15)*
- [x] **Mineral click-through to Targets** — Clicking a mineral on the Minerals page navigates to Targets filtered by that mineral. *(Completed 2026-03-15)*
- [x] **Minerals dropdown uses full list** — CSV import and PDF report mineral dropdowns show all minerals from the Minerals page, not just those already on targets. *(Completed 2026-03-15)*
- [x] **Target Notes field** — Editable Notes field in target detail panel. Users can add/edit free-form notes on any target. Notes also populated automatically from batch imports and PDF extraction (county, docket info, AI extraction notes). *(Completed 2026-03-15)*
- [x] **Target Status & Claim Type rework** — Renamed Priority → Target Status with stages: Monitoring (Low/Med/High), Negotiation, Due Diligence, Ownership. Renamed Status → Claim Status. Added editable Claim Type dropdown (Patented, Unpatented, Lode, Placer, Mill Site, Tunnel Site). Color-coded badges across all views including map pins. *(Completed 2026-03-15)*
- [x] **PLSS geocoding for Map** — Auto-converts PLSS locations to lat/long coordinates via BLM Cadastral API. Runs on target save and available as batch endpoint. Map now shows all targets with PLSS. *(Completed 2026-03-18)*
- [x] **Map stack: Leaflet (no Google Earth migration)** — Keep building the map on Leaflet with raster/vector overlays (WMS, ArcGIS, GeoJSON). Google Earth / Maps Platform would add API keys, billing, and tighter coupling without clear upside for public mining/GIS layers. Satellite/topo/street basemaps already cover exploration needs. *(Decision recorded 2026-03-23)*
- [x] **Advanced GIS overlays on Leaflet** — Toggleable overlays on the map (WMS, ArcGIS, GeoJSON, MRDS “Known Mines” from USGS FeatureServer, etc.) with layer controls so users can add land/USGS/BLM context around targets without leaving Leaflet. *(Completed 2026-03-26)*
- [x] **LR2000 / Geographic Index–style report (in-app)** — “Run LR2000 Report” queries BLM’s national MLRS mining-claims layer by target PLSS/coords (same conceptual source as the [Geographic Index report](https://reports.blm.gov/report/MLRS/104/Mining-Claims-Geographic-Index-Report/)); results stored under target `characteristics` and shown in Targets detail alongside MLRS scrape fetch. *(Completed 2026-03-26)*
- [x] **Batch PDF Report Processing** — Upload a CSV of report metadata (docket, property name, state, county, minerals). System constructs USGS OME/DMEA download URLs, downloads PDFs, runs AI extraction (process_pdf_report) in batches, and presents aggregated results for review/import. Supports "Import as Targets (skip PDF)" for metadata-only import, and full AI extraction with progress tracking. Dashboard "Batch Process Reports" button. *(Completed 2026-03-15)*
- [x] **Batch PDF: multi-engine text + DMA/DMEA URLs + clear “PDF not readable” vs “0 targets”** — PyMuPDF / pypdf fallback, optional OCR (`BATCH_OCR_MAX_PAGES`), correct DMA scan paths (`…/dma/{docket}_DMA.pdf`), list-type selector (OME / DMEA / DMA), and UI/API fields distinguishing open/read failures from successful reads with no extracted targets. *(Completed 2026-03-27)*
- [x] **Isolated target generation pipeline** — Python package `target_pipeline/`: MLRS/USGS file ingest, PLSS + commodity normalization, grouping/scoring, upsert into `areas_of_focus` (or optional `targets` table); does not modify app code. *(Completed 2026-04-01)*
- [x] **PLSS from latitude / longitude** — BLM Cadastral reverse lookup (same MapServer layer as forward geocode); per-target action in detail when coords exist and `plss_normalized` is empty; batch in Clean Targets; manual add supports coordinates-only targets; API `coordinates` update and relaxed create (name + lat/lon or PLSS). *(Completed and verified 2026-04-03: live BLM intersect query + `reverse_geocode_plss` returns correct human PLSS string and DB-friendly T/R storage.)*
- [x] **Batch Fetch Claim Records & LR2000 Report** — Multi-select on Targets table; sequential batch APIs (`/areas-of-focus/batch/fetch-claim-records`, `.../batch/lr2000-geographic-report`) up to 25 ids per request with client chunking; per-row results modal. *(Completed 2026-04-05)*
- [x] **Clean Targets: AI + web assist for missing PLSS** — OpenAI + DuckDuckGo web snippets infer PLSS from name/state/county/notes; Clean Targets UI runs **preview** (`fill-plss-ai-preview`) then **review modal** (editable PLSS, per-row apply checkboxes) and **Apply** (`fill-plss-ai-apply`). Guardrails: no DB write until Apply; preview/apply caps (40); spacing between web lookups; logging. Legacy `fill-plss-ai` still supports immediate apply for API callers. *(Completed 2026-04-08)*
- [x] **Automation Engine (Rules + Scheduled Actions)** — Cron-style rules engine: users define a filter (e.g. "high priority targets"), an action (`fetch_claim_records`, `lr2000_report`, `check_blm`, `generate_report`), an outcome (e.g. email on status change), and a schedule. New Automations page (rules CRUD + run history with per-target results). Background scheduler (`automation_scheduler`) runs due rules every 60s. *(Completed 2026-04-15)*
- [x] **Production safety: never-500 endpoint contract + CI tests** — All user-facing actions now degrade gracefully and always return structured `{ok, error}` JSON. Specifically: Fetch Claim Records falls back to the built-in BLM ArcGIS API when the `BLM_ClaimAgent` companion repo isn't deployed (Render/Railway), and LR2000 Report is wrapped in try/except so it can never bubble to `Internal Server Error`. New `tests/test_api_endpoints.py`, `tests/test_fetch_claim_records.py`, `tests/test_mlrs_geographic_index.py`. GitHub Actions workflow runs pytest on every push; `scripts/pre-push.sh` enforces tests locally before push. See `TESTING.md`. *(Completed 2026-04-20)*

---

## Backlog

*Add new initiatives below. Prioritize by moving items up.*

---

## Ideas / Someday

*Rough ideas not yet scoped or committed to.*


