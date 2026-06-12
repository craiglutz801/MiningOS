# Mining AI — Product Overview & Documentation

**Tagline:** Deal intelligence for claims & minerals.

**Repository name:** Mining_OS (Mining Deal OS). The user-facing product brand is **Mining AI**.

This document describes what Mining AI is, who it serves, what problems it solves, how it works end-to-end, and where each major capability lives in the stack. It is written for operators, investors, partners, and engineers onboarding to the product.

For operational deployment details, see also [PRODUCTION_VERCEL_RENDER.md](./PRODUCTION_VERCEL_RENDER.md). For a shorter AI-strategy briefing, see [MINING_OS_SYNOPSIS_FOR_AI.md](./MINING_OS_SYNOPSIS_FOR_AI.md). For shipped vs planned work, see [ROADMAP.md](../ROADMAP.md).

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [What Mining AI is](#2-what-mining-ai-is)
3. [Who it is for and why it matters](#3-who-it-is-for-and-why-it-matters)
4. [Core concepts and data model](#4-core-concepts-and-data-model)
5. [Product surface (pages and workflows)](#5-product-surface-pages-and-workflows)
6. [How targets enter the system](#6-how-targets-enter-the-system)
7. [AI capabilities](#7-ai-capabilities)
8. [Government and GIS integrations](#8-government-and-gis-integrations)
9. [Map and spatial intelligence](#9-map-and-spatial-intelligence)
10. [Deal workflow and collaboration](#10-deal-workflow-and-collaboration)
11. [Automation engine](#11-automation-engine)
12. [Offline pipelines and bulk ingestion](#12-offline-pipelines-and-bulk-ingestion)
13. [Technical architecture](#13-technical-architecture)
14. [Security, accounts, and multi-tenancy](#14-security-accounts-and-multi-tenancy)
15. [Configuration and prerequisites](#15-configuration-and-prerequisites)
16. [Use cases (scenarios)](#16-use-cases-scenarios)
17. [Value proposition summary](#17-value-proposition-summary)
18. [Limitations and roadmap gaps](#18-limitations-and-roadmap-gaps)
19. [Related documentation](#19-related-documentation)

---

## 1. Executive summary

Mining AI is a **web-based deal operations platform** for mining and mineral acquisition teams. It centralizes **targets**—properties, claims, or prospects tied to **priority minerals**—and connects each target to:

- **Location** (Public Land Survey System / PLSS, latitude/longitude, state, county)
- **Evidence** (government reports, PDFs, USGS dockets, assay and desk-study links)
- **BLM / MLRS context** (claim records, payment status, case pages, geographic index–style reports)
- **Deal stage** (monitoring through ownership)
- **Maps and overlays** (your portfolio plotted against BLM cadastral data, land ownership, and USGS MRDS known mines)

The product is built to **operationalize** a target list: ingest at scale, enrich with authoritative public data, monitor claim health, prioritize by mineral and stage, automate recurring checks, and **share curated views** with partners without exposing your full workspace.

AI is used where unstructured documents and open-ended research dominate—**PDF report extraction**, **batch USGS report processing**, **discovery-style prospecting**, and **inferring missing PLSS** from names and notes—not as a replacement for BLM or USGS source systems.

---

## 2. What Mining AI is

### 2.1 Category

Mining AI sits at the intersection of:

| Layer | Role |
|--------|------|
| **CRM / deal pipeline** | Target status, notes, tags, negotiation stages |
| **GIS / exploration map** | Leaflet map, PLSS grid, MRDS picks, land ownership overlays |
| **Regulatory intelligence** | BLM MLRS claims, maintenance-fee signals, LR2000-style geographic reports |
| **Document intelligence** | AI extraction from historical mining PDFs and USGS scan libraries |
| **Data integration hub** | CSV import, MRDS bulk pipeline, optional legacy BLM×MRDS scoring |

It is **not** a generic GIS desktop replacement, a full land-records title system, or a subsurface modeling package. It optimizes for **speed-to-action on claims and minerals you care about**.

### 2.2 Primary unit of work: the Target

In the database and API, targets are stored in the **`areas_of_focus`** table (the UI label is **Targets**). Everything else—minerals list, reports, map pins, automations, share links—radiates from this row.

A target typically carries:

- **Identity:** name, optional tag, notes
- **Geography:** PLSS (township, range, section, meridian), normalized PLSS key, WGS84 coordinates, state, county
- **Commodities:** one or more minerals (normalized names; MRDS chemical symbols expanded to full names)
- **Deal fields:** Target Status (pipeline stage), Claim Status, Claim Type
- **Provenance:** `source` (e.g. manual, CSV, PDF import, `discovery_agent`, `mrds_auto`, `target_pipeline`), **Retrieval Type** (`Known Mine` vs `User Added`)
- **Integrations:** JSON **`characteristics`** blob for MLRS claim fetches, LR2000 report snapshots, pipeline metadata, etc.
- **Reports:** `report_links` (URLs and stored PDF references)

### 2.3 Relationship to “Mining OS”

**Mining OS** is the engineering name of the monorepo (FastAPI backend, React frontend, Postgres, Docker, pipelines). **Mining AI** is the product name shown in the browser (`Mining AI — Deal intelligence` in the shell and page title). This document uses **Mining AI** for product language and **Mining OS** where referring to repository layout or deployment artifacts.

---

## 3. Who it is for and why it matters

### 3.1 Intended users

- **Acquisition and business development** teams tracking claim opportunities by mineral theme (e.g. uranium, tungsten, fluorspar, beryllium)
- **Operators and landmen** who need BLM payment status, case links, and PLSS-accurate maps without re-keying spreadsheets
- **Technical and geological advisors** who rely on USGS DMEA/OME/DMA reports and MRDS known-mine context
- **Principals and partners** who need a **read-only share link** or printable summary for diligence conversations

### 3.2 Problems it addresses

| Pain | How Mining AI helps |
|------|---------------------|
| Targets scattered across spreadsheets, PDFs, and email | Single workspace with filters, status, and linked reports |
| Slow PLSS / coordinate cleanup | Forward and reverse BLM Cadastral geocoding; batch tools; AI-assisted PLSS fill with human review |
| Manual BLM website checks | Fetch Claim Records, LR2000-style reports, batch actions, scheduled automations |
| Historical USGS report libraries are huge | Batch CSV → download PDFs → extract targets with OCR fallback |
| Hard to show partners a clean package | Public share links with minerals, location, reports, unpaid claims only |
| No map context for a target list | Leaflet map with BLM PLSS, SMA ownership, MRDS overlay, status-colored pins |

### 3.3 Strategic outcome

Users move targets through a deliberate funnel:

**Monitor → Negotiate → Due diligence → Ownership**

…while the system continuously **refreshes public claim data** and **preserves evidence trails** (which PDF or docket produced a row).

---

## 4. Core concepts and data model

### 4.1 Minerals of interest

A configurable, account-scoped list in the **Minerals** page drives:

- Discovery agent runs (per-mineral prompts)
- Filtering on Targets and the map
- Email alert logic for high-priority unpaid claims tied to priority minerals

Default examples in docs and config include fluorspar, beryllium, tungsten, and uranium. The list is a **superset**: importing or creating targets can add new mineral names, which are then registered on the Minerals tab.

**Mineral normalization** (all ingest paths): title-case, strip noise, deduplicate, expand USGS MRDS symbols (e.g. Au → Gold, U → Uranium) and commodity abbreviations (e.g. Sdg → Sand and Gravel).

### 4.2 Target Status (deal pipeline)

Renamed from legacy “Priority”; values include:

| Status | Meaning |
|--------|---------|
| Monitoring — Low / Med / High | Watch-list tiers; High is closest to active pursuit |
| Negotiation | Active outreach |
| Due Diligence | Under formal review |
| Ownership | Controlled or acquired |

The Dashboard shows **count tiles** linking into Targets with the matching filter.

### 4.3 Claim Status and Claim Type

BLM-oriented fields separate from deal stage:

- **Claim Status** — e.g. paid / unpaid (also influenced by Fetch Claim Records enrichment)
- **Claim Type** — Patented, Unpatented, Lode, Placer, Mill Site, Tunnel Site

Badges are color-coded in tables, detail panels, duplicate review, and **map pin styling**.

### 4.4 Retrieval Type

Distinguishes how a target entered the operational list:

- **Known Mine** — e.g. bulk MRDS auto-import (`source = mrds_auto`)
- **User Added** — manual entry, CSV, PDF, discovery, etc.

Filterable on Targets so teams can separate **database-seeded** prospects from **curated** ones.

### 4.5 PLSS and coordinates

- **PLSS** — Township, range, section (and meridian where applicable); stored human-readable and as **`plss_normalized`** for deduplication (one row per normalized section key in the app model).
- **Forward geocode** — PLSS → lat/long via BLM Cadastral (on save and batch).
- **Reverse geocode** — lat/long → PLSS when coordinates exist but PLSS is empty.

### 4.6 Reports and PDFs

- **`report_links`** on each target (URLs, filenames, metadata)
- Uploaded PDFs from single-report or batch flows are **stored and associated** with every imported target from that document (`report_links` / focus report storage)

### 4.7 Characteristics (integration payload)

Flexible JSON on each target, commonly holding:

- **`claim_records`** — output of Fetch Claim Records (claims, case pages, payment status)
- **LR2000 / MLRS geographic index** report snapshots
- **`target_pipeline`** — metadata when rows come from the offline `target_pipeline` package

---

## 5. Product surface (pages and workflows)

### 5.1 Navigation

Authenticated app routes (React Router):

| Route | Page | Purpose |
|-------|------|---------|
| `/` | Dashboard | Health, counts, ingestion modals, discovery agent |
| `/areas` | Targets | Primary table: filter, edit, bulk actions, import, clean, share |
| `/minerals` | Minerals | Configure minerals of interest; click through to filtered Targets |
| `/discoveries` | Discoveries | History of discovery agent runs |
| `/discoveries/:id` | Discovery detail | Run log and outcomes for one run |
| `/map` | Map | Spatial view of targets + layers |
| `/automations` | Automations | Rules CRUD and run history |
| `/admin/accounts` | Admin | System admin account management |
| `/share/:token` | Share (public) | No-login tailored view for a share link |

### 5.2 Dashboard

The operational **home**:

- API / database health indicator
- Mineral and target counts
- **Target Status** tiles (deep-link to filtered Targets)
- **Process Mine PDF Report** — upload one PDF → AI extract → review → import
- **Batch Process Reports** — CSV of USGS-style dockets → download PDFs → AI extract (or metadata-only import)
- **Discovery agent** — configure prompts per mineral, run replace or supplement mode
- Legacy pointer to **Ingest data files** on Targets for CSV loads

### 5.3 Targets (Areas of focus)

The day-to-day workbench:

- **Rich filtering:** mineral, target status, claim status, claim type, tag, state, PLSS components, retrieval type, text search
- **Detail panel:** edit all core fields, notes, minerals, coordinates, PLSS
- **Per-target actions:**
  - Fetch Claim Records (async job + polling on production)
  - Run LR2000 / Geographic Index–style report
  - Clear stored MLRS / LR2000 snapshots
  - Geocode PLSS → coordinates; reverse geocode coordinates → PLSS
  - Generate AI mineral report (narrative summary for buyers/partners)
- **Bulk actions:** multi-select → batch Fetch Claim Records, batch LR2000 (chunked, up to 25 per API call with client chunking)
- **Import:** CSV upload with conflict strategies (merge, use old, use new)
- **Clean Targets modal:**
  - Targets missing PLSS
  - Duplicate PLSS groups with consolidate workflow
  - **AI + web assist for PLSS** — preview proposed PLSS (editable), then apply (no DB write until Apply; caps and guardrails)
- **Share…** — create public link for selected or filtered targets

### 5.4 Minerals

- CRUD for minerals of interest
- Mineral names appear as **tag badges** across the app
- Click a mineral → Targets filtered to that commodity

### 5.5 Map

Built on **Leaflet** (no Google Maps Platform dependency for core workflows).

- Plot targets by coordinates; pin colors reflect claim/target status
- **Basemaps:** Satellite (Esri), Topo (OpenTopoMap), Streets (CARTO)
- **Overlays:**
  - My Targets
  - PLSS Grid (BLM WMS cadastral)
  - Land ownership (BLM SMA cached tiles)
  - Known Mines (USGS MRDS compact FeatureServer — loads in viewport at zoom 8+)
- Layer control persists preferences in local storage
- US state boundaries on satellite basemap

### 5.6 Discoveries

Read-only audit of **discovery agent** runs: when they ran, what minerals/states were requested, counts, and drill-down to stored run metadata (including AI locations and web URLs when migration columns exist).

### 5.7 Automations

**Rules engine** with cron-style schedules:

- Define a **filter** (tag, target status, mineral, claim status, state, claim type, name, township, range, section)
- Choose an **action:** `fetch_claim_records`, `lr2000_report`, `check_blm_status`, `generate_report`
- Choose an **outcome:** log only, email always, email on change, email on error
- Background scheduler evaluates due rules (~every 60 seconds)
- **Run history** with per-target results; UI toast when a run completes

### 5.8 Share (public)

- Authenticated user selects targets → **Share** modal → receives URL `/share/{token}`
- Public API returns **trimmed fields only:** name, PLSS, lat/long, minerals, known reports, **unpaid claims with BLM case links**
- **Download PDF** via browser print from a print-optimized Share page
- Account-private fields (internal notes, scores, account ids) are never exposed

---

## 6. How targets enter the system

Mining AI supports many **provenance paths**; choosing the right one is the main “prime the pump” decision for new workspaces.

| # | Method | Best for | Provenance / source |
|---|--------|----------|---------------------|
| 1 | **Manual add** | One-off prospects | User Added |
| 2 | **CSV import** (Targets) | Spreadsheets, legacy `data_files` layouts | User Added / custom |
| 3 | **Single PDF upload** (Dashboard) | One historical report | PDF + AI; linked report |
| 4 | **Batch Process Reports** (Dashboard) | USGS DS-1004 docket lists (OME, DMEA, DMA) | CSV metadata + optional PDF download + AI |
| 5 | **Discovery agent** (Dashboard) | AI-led prospecting by mineral and state | `discovery_agent` |
| 6 | **MRDS `mines_to_targets` pipeline** | State-scale known mine seeds (UT, NV, ID, …) | `mrds_auto` → Known Mine |
| 7 | **`target_pipeline` package** | MLRS/USGS file exports with scoring | `target_pipeline` |
| 8 | **Legacy `run_all` candidates** | BLM open claims × MRDS radius scoring | Legacy candidate tables / workflows |

**Batch reports** support:

- Report list types: **OME**, **DMEA**, **DMA** (correct USGS URL patterns per type)
- **Skip PDF** — import metadata-only rows quickly
- Clear distinction between **PDF not readable** vs **readable PDF with zero extracted targets**

**Uniqueness:** The app enforces **one target per `plss_normalized`**; imports merge minerals and report links on conflict.

---

## 7. AI capabilities

All AI features require **`OPENAI_API_KEY`** in the environment unless noted otherwise.

### 7.1 PDF report processing (single upload)

**Flow:** Upload PDF → extract text → OpenAI structured extraction → optional second pass geo-location → user reviews checkboxes → import.

**Text extraction stack:**

1. PyMuPDF
2. pypdf / PyPDF2 fallback
3. Optional **Tesseract OCR** for scan-only PDFs (`BATCH_OCR_MAX_PAGES`, page timeout settings)

**Model:** GPT-4o for extraction (low temperature, structured JSON).

**Extracted fields (typical):** name, PLSS, township/range/section, lat/long, minerals, state, county, notes, mining district.

**Pass 2:** Targets missing PLSS and coordinates get geo-location enrichment after initial extraction.

### 7.2 Batch Process Reports

Same extraction engine at scale:

1. User uploads CSV (docket, property name, state, county, commodities, file size hints)
2. System builds **USGS Data Series 1004** scan URLs
3. Downloads PDFs per row
4. Runs `process_pdf_report` (or metadata-only path)
5. Aggregated **review → import** UI with progress and per-row error semantics

### 7.3 Discovery agent

**Purpose:** Propose new targets aligned to configured **minerals** and **states**, biased toward:

- Known mines and past producers
- Existing government reports (USGS, NGMDB, MRDS, state surveys)
- Locations with monetization path and BLM claim context where integrated

**Mechanism:**

- Editable **system** and **user** prompts per mineral (templates support `{{mineral}}`, `{{states}}`)
- OpenAI generation
- Optional **DuckDuckGo** web search for report URLs
- BLM status checks via BLM_ClaimAgent or built-in PLSS/coordinate queries
- **Replace** (delete prior `discovery_agent` rows) or **Add/supplement**

Runs are logged to **`discovery_runs`** for audit.

### 7.4 Clean Targets: AI PLSS fill

For targets missing PLSS:

- **`fill-plss-ai-preview`** — OpenAI + web snippets propose PLSS; user edits in review modal
- **`fill-plss-ai-apply`** — writes only checked rows (caps, spacing between web lookups)
- Legacy immediate **`fill-plss-ai`** still available for API callers

**Guardrail:** No database mutation until explicit Apply.

### 7.5 Generate report (per target or automation action)

Narrative **mineral / property report** suitable for buyers or partners (GPT-4o-mini), optionally incorporating text snippets from attached reports.

### 7.6 What AI does not do

- AI does **not** replace BLM MLRS as the legal source of claim status
- AI does **not** guarantee correct PLSS; human review is built into preview/apply flows
- AI discovery is only as good as prompts, mineral focus, and public web data availability

---

## 8. Government and GIS integrations

### 8.1 BLM Cadastral (PLSS)

- Forward: PLSS → intersection query → WGS84 centroid for mapping
- Reverse: coordinates → PLSS string + normalized storage fields

### 8.2 Fetch Claim Records

**Purpose:** Attach MLRS mining claim rows to a target’s `characteristics.claim_records`.

**Resolution order:**

1. Optional **BLM_ClaimAgent** sibling repo (coords or PLSS) when deployed
2. Built-in **ArcGIS / spatial** fallbacks on production (no Selenium required for base claim list)

**Payment / maintenance-fee enrichment:**

- ArcGIS does not expose “maintenance fee not received” text
- Enrichment uses HTTP, RAS/serial register URLs, then **Playwright headless Chromium** on MLRS Salesforce case pages (production Render build installs Chromium; `MINING_OS_MLRS_PAYMENT_HEADLESS=1`)
- Long runs use **job start + poll** API so the UI does not hang on multi-claim targets

**Semantics:**

- **0 claims** with successful BLM response → `ok: true` (not an error)
- BLM unreachable → structured error, never opaque HTTP 500 (see TESTING.md contract)

### 8.3 LR2000 / Geographic Index–style report

In-app query against BLM’s national MLRS mining-claims layer by target PLSS/coordinates (same conceptual source as BLM’s Geographic Index report). Results stored under target **characteristics** and shown in Targets detail alongside other MLRS fetches.

### 8.4 USGS MRDS

- Map overlay: MRDS Compact FeatureServer (viewport query, clustered picks)
- Offline pipeline: `mines_to_targets` pages MRDS by state bbox, reverse-geocodes to PLSS, groups by section, upserts with `source = mrds_auto`

### 8.5 BLM_ClaimAgent (optional companion)

Sibling project under `Agents/BLM_ClaimAgent` (or `MINING_OS_BLM_AGENT_PATH`). When present locally, enables richer MLRS case scraping (e.g. Selenium). Production often relies on built-in ArcGIS + Playwright path instead.

### 8.6 Email alerts

High-priority **unpaid** claims for **priority minerals** can trigger email to `ALERT_EMAIL` via SMTP settings in `.env`. Also available from automations (email on change / error / always).

---

## 9. Map and spatial intelligence

The map answers: **“Where are our targets relative to PLSS, land ownership, and known USGS mines?”**

- Targets without coordinates get them via PLSS geocode on save or batch
- Status-colored pins communicate claim/deal state at a glance
- MRDS layer helps validate “known mine” context near a prospect
- BLM PLSS WMS helps verify section boundaries
- SMA ownership overlay adds land-status context

**Design decision (recorded on roadmap):** Stay on Leaflet with public WMS/tiles rather than migrating to Google Maps Platform—lower cost, no extra API keys, sufficient for exploration use cases.

---

## 10. Deal workflow and collaboration

### 10.1 Internal workflow

1. **Build list** — import, MRDS pipeline, discovery, or PDF batch
2. **Normalize** — minerals, PLSS, duplicates (Clean Targets)
3. **Enrich** — Fetch Claim Records, LR2000, geocode
4. **Prioritize** — target status, tags, filters, map review
5. **Advance deals** — Negotiation → Due Diligence → Ownership
6. **Monitor** — automations refresh BLM data; email on changes

### 10.2 External collaboration

- **Share links** for partners/investors (live data, scoped fields)
- **Print / PDF** from share page for offline meetings
- **Generate report** for narrative diligence memos

### 10.3 Tags and notes

- Free-form **Notes** (manual + auto from imports/PDF county/docket text)
- **Tags** for portfolio segmentation (filters, automations, share by tag filter + select all)

---

## 11. Automation engine

| Component | Behavior |
|-----------|----------|
| **Rule** | Filter + action + outcome + cron schedule |
| **Scheduler** | Background thread ~60s tick; runs due rules |
| **Run log** | Per-target JSON results, success/failure, duration |
| **Email** | Optional on outcome type |
| **Cap** | Up to 200 targets per run; pause between targets |

**Actions:**

- `fetch_claim_records` — refresh claim list and payment signals
- `lr2000_report` — refresh geographic index snapshot
- `check_blm_status` — BLM status check path
- `generate_report` — AI narrative report

**UI:** Automations page + toast notification in shell when a run completes.

---

## 12. Offline pipelines and bulk ingestion

These run **outside** the React UI but write the same Postgres `areas_of_focus` rows.

### 12.1 `target_pipeline/`

- Ingest MLRS/USGS exports from `target_pipeline/data/mlrs` and `.../usgs`
- Filter: states in `UT, ID, WY, NV, AZ, MT` (configurable) and critical minerals list
- Score groups (deposit class, reports, claim density)
- Upsert with `source = target_pipeline'`; reversible via `cleanup_pipeline_targets`

### 12.2 `target_pipeline/mines_to_targets`

- Query USGS MRDS ArcGIS FeatureServer by state bounding box
- Exclude non-mine DEV_STAT (e.g. processing plants)
- Reverse-geocode to PLSS (cached grid)
- One target per PLSS section; name lists combined mine names
- `retrieval_type` = Known Mine; revert: `DELETE FROM areas_of_focus WHERE source = 'mrds_auto'`

### 12.3 Legacy `mining_os.pipelines.run_all`

- PostGIS init, BLM open claims, MRDS proximity candidates (MVP scoring: commodity match + evidence density within radius)
- Still available for candidate-style workflows; UI is Targets-centric today

### 12.4 CustomActions (repo folder)

Ad-hoc scripts for exports, uranium grids, PLSS fill utilities—operator tooling, not part of the core web product.

---

## 13. Technical architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                        User browser                              │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┴───────────────────┐
         │                                       │
         ▼                                       ▼
┌─────────────────┐                   ┌─────────────────────────┐
│ Vercel (opt.)   │  /api/* rewrite   │ Render / Railway / local │
│ React SPA       │ ────────────────► │ FastAPI (mining_os.api) │
│ Vite build      │                   │ + optional frontend/dist │
└─────────────────┘                   └───────────┬─────────────┘
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    ▼                           ▼                           ▼
            ┌──────────────┐           ┌──────────────┐            ┌─────────────────┐
            │  Postgres    │           │  OpenAI API  │            │ BLM / USGS APIs │
            │  (areas_of_  │           │              │            │ ArcGIS, WMS,    │
            │   focus, …)  │           │              │            │ MLRS case pages │
            └──────────────┘           └──────────────┘            └─────────────────┘
```

| Layer | Technology |
|-------|------------|
| Frontend | React, TypeScript, Vite, React Router, Tailwind-style utility classes |
| Backend | FastAPI, SQLAlchemy, uvicorn |
| Database | PostgreSQL (PostGIS in local Docker compose) |
| Map | Leaflet, Esri/Carto/OpenTopo tiles, BLM WMS/ArcGIS |
| AI | OpenAI GPT-4o / GPT-4o-mini |
| OCR | Tesseract (optional, batch/single PDF) |
| Browser automation | Playwright Chromium (MLRS payment banner) |
| CI | GitHub Actions pytest; `scripts/pre-push.sh` |
| Deploy | Vercel frontend + Render API (see production doc) |

**API contract:** User-facing endpoints return HTTP 200 with `{ ok, error?, ... }` even on failure—never opaque 500s to the UI (see [TESTING.md](../TESTING.md)).

---

## 14. Security, accounts, and multi-tenancy

- **Users** authenticate with email/username + password (session token in cookie/header pattern via frontend auth module)
- **Accounts** isolate data; users may belong to multiple accounts via **memberships**
- **Active account** on session; switch account reloads workspace
- **System admin** can manage accounts (`/admin/accounts`)
- **Share tokens** are unguessable URL-safe secrets; public view loads only ids stored on the link, scoped to link’s account
- **Bootstrap** route for first admin when no users exist

Row-level scoping: targets and share links respect `account_id`.

---

## 15. Configuration and prerequisites

### 15.1 Runtime

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | 3.12 supported |
| Node.js | Frontend build |
| Docker Compose | Local PostGIS (optional for production managed Postgres) |
| `DATABASE_URL` | Same DB for app and pipelines |

### 15.2 Feature flags and env (representative)

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | PDF, discovery, PLSS AI, generate report |
| `ALERT_EMAIL`, `SMTP_*` | Email alerts |
| `MINING_OS_BLM_AGENT_PATH` | BLM_ClaimAgent location |
| `MINING_OS_MLRS_PAYMENT_HEADLESS` | Enable Playwright on PaaS |
| `BATCH_OCR_MAX_PAGES` | OCR limit for scans |
| `TARGET_PIPELINE_STATES` | Pipeline state filter |

### 15.3 Local dev quick start

```bash
docker compose up -d          # Postgres
pip install -e .              # Python package
python -m mining_os.pipelines.run_all --init-db
bash scripts/start-web.sh     # http://localhost:8000
```

Optional: `pip install openai duckduckgo-search` for discovery; `bash scripts/setup_mlrs_payment_local.sh` for Playwright locally.

---

## 16. Use cases (scenarios)

### 16.1 Uranium portfolio in the Colorado Plateau

1. Run `mines_to_targets --states UT` (and NV) to seed **Known Mine** rows from MRDS.
2. Filter Targets to uranium; set monitoring tiers.
3. Batch Fetch Claim Records on high-priority sections; automate weekly refresh.
4. Share a filtered list with a joint-venture partner (unpaid claims visible).

### 16.2 USGS historical report mining

1. Obtain OME/DMEA docket CSV for a district.
2. Dashboard → Batch Process Reports → download PDFs + AI extract.
3. Review rows with “0 targets” vs “PDF not readable” separately.
4. Import; PDFs linked for diligence.

### 16.3 Single property from a private geological report

1. Dashboard → Process Mine PDF Report.
2. Two-pass extraction fills PLSS/coords where possible.
3. Manual edit in Targets detail; Run LR2000; map verify on PLSS grid overlay.

### 16.4 Ongoing claim-fee monitoring

1. Tag targets “fee-watch”.
2. Automation: weekly `fetch_claim_records` + email on change.
3. Targets detail shows maintenance-fee message when Playwright enrichment succeeds.

### 16.5 Cleaning a legacy spreadsheet import

1. CSV import from `data_files`-style columns.
2. Clean Targets → missing PLSS → AI preview → Apply.
3. Duplicates tab → consolidate into one row per section.
4. Batch geocode PLSS → map.

### 16.6 AI-led greenfield prospecting

1. Configure discovery prompts per mineral (emphasize known mines + reports).
2. Run discovery in **Add** mode to supplement MRDS seeds.
3. Review Discoveries run history; promote rows to Negotiation status.

---

## 17. Value proposition summary

| Stakeholder | Value |
|-------------|-------|
| **Deal lead** | One pipeline view from monitor to ownership with evidence attached |
| **Landman** | BLM claim pulls, payment signals, case links without manual copy-paste |
| **Analyst** | USGS PDF and MRDS scale-ingestion with review gates |
| **Partner** | Share link + print PDF without account access |
| **Engineer** | Structured API, test-gated deploys, isolated pipelines that don’t fork the app |

**Economics of the design:** Heavy use of **free public GIS** (BLM, USGS ArcGIS) and **open map tiles** keeps recurring infra cost low; paid APIs are primarily **OpenAI** (usage-based) and hosting (Vercel/Render/Postgres).

---

## 18. Limitations and roadmap gaps

Documented backlog items (see ROADMAP.md) include:

- **Payment status on production** — Full maintenance-fee text may require Playwright; ArcGIS-only path leaves `payment_status` unknown until enrichment succeeds. Localhost with BLM_ClaimAgent can be more authoritative.
- **Idaho MRDS import** — Planned production run for `mines_to_targets --states ID`.
- **Multi-worker job store** — Fetch Claim Records jobs are in-memory; scaling uvicorn workers would need Redis/DB-backed jobs (results still persist on targets).

**Operational safeguards:** Destructive operations (e.g. delete all targets) require explicit user confirmation per [SAFEGUARDS.md](./SAFEGUARDS.md).

---

## 19. Related documentation

| Document | Contents |
|----------|----------|
| [README.md](../README.md) | Install, run, BLM_ClaimAgent, alerts |
| [ROADMAP.md](../ROADMAP.md) | Completed and planned features |
| [TESTING.md](../TESTING.md) | Pytest, CI, API safety contract |
| [PRODUCTION_VERCEL_RENDER.md](./PRODUCTION_VERCEL_RENDER.md) | Vercel + Render, MLRS Playwright |
| [MINING_OS_SYNOPSIS_FOR_AI.md](./MINING_OS_SYNOPSIS_FOR_AI.md) | Short AI assistant briefing |
| [target_pipeline/README.md](../target_pipeline/README.md) | Offline pipeline operations |
| [mining_target_pipeline_spec.md](./mining_target_pipeline_spec.md) | Pipeline design spec |

---

*Document version: 2026-06-02. Reflects product behavior through roadmap items completed 2026-05-28 (share targets) and codebase structure in Mining_OS repository.*
