# Mining OS — Target Pipeline Spec (Cursor-Ready)

## PURPOSE

Build a Python-based Target Generation Pipeline that:
- Ingests mining/deposit + claim datasets
- Normalizes and links via PLSS
- Produces a clean Target list
- Writes into your existing app database

DO NOT modify the existing app.

---

# OUTPUT TABLE: targets

Fields:
- id
- target_name
- state
- county
- plss
- commodity
- source_count
- has_report
- score
- created_at

---

# PIPELINE FLOW

raw data → normalize → PLSS link → group → score → write to DB

---

# PROJECT STRUCTURE

target_pipeline/
  run.py
  db.py
  sources/
    mlrs.py
    usgs.py
  processors/
    plss.py
    commodities.py
  targets/
    builder.py
    scorer.py
  outputs/
    db_writer.py

---

# CORE LOGIC

Group by:
- PLSS
- commodity

Each group = Target

---

# SCORING

score = 0
+3 deposit
+2 report
+2 multiple deposits
+1 claims nearby

---

# BUILDER (example)

def build_targets(deposits, claims):
    targets = {}
    for d in deposits:
        key = (d["plss"], d["commodity"])
        if key not in targets:
            targets[key] = {"plss": d["plss"], "commodity": d["commodity"], "deposits": [], "claims": []}
        targets[key]["deposits"].append(d)
    for c in claims:
        key = (c["plss"], c.get("commodity"))
        if key in targets:
            targets[key]["claims"].append(c)
    return list(targets.values())

---

# SCORER

def score_target(t):
    score = 0
    if t["deposits"]:
        score += 3
    if any(d.get("reports") for d in t["deposits"]):
        score += 2
    if len(t["deposits"]) > 1:
        score += 2
    if t["claims"]:
        score += 1
    return score

---

# RUN

- load claims
- load deposits
- build targets
- score
- write to DB

---

# SUCCESS

Targets populate your app without changing app code


---

# CURSOR EXECUTION PLAN

Use the following sections as the exact implementation order inside Cursor.
Do not skip ahead.
Build each step, verify it works, then move to the next one.

## RULES FOR CURSOR

- Do not modify existing application code unless needed to read from the `targets` table.
- Do not replace existing backend or frontend architecture.
- Build only a Python pipeline that writes clean Target rows into the existing database.
- Keep everything isolated in a new folder called `target_pipeline/`.
- Prefer simple, working code over abstraction.
- Use clear Python modules, type hints, logging, and `.env` configuration.
- Assume the existing app will read from the database once targets are populated.

---

# STEP-BY-STEP BUILD ORDER

## STEP 1 — Create the isolated pipeline project

Create a new folder named `target_pipeline/` in the existing repository.

Generate this structure:

```text
target_pipeline/
  README.md
  requirements.txt
  .env.example
  run.py
  config.py
  db.py
  logging_config.py
  models.py
  sources/
    __init__.py
    mlrs.py
    usgs.py
  processors/
    __init__.py
    normalize.py
    plss.py
    commodities.py
  matchers/
    __init__.py
    spatial.py
  targets/
    __init__.py
    builder.py
    scorer.py
  outputs/
    __init__.py
    db_writer.py
  tests/
    test_plss.py
    test_commodities.py
    test_builder.py
```

### Prompt for Cursor
```text
Create a new isolated Python package named `target_pipeline` inside this repository. Do not modify the existing application architecture. Add the following files and folders exactly as specified in the Mining OS target pipeline spec. Use Python 3.12 style, type hints, and simple module organization. Add a README, requirements.txt, .env.example, and the package folders for sources, processors, matchers, targets, outputs, and tests.
```

---

## STEP 2 — Add configuration and database connectivity

Build:
- `.env.example`
- `config.py`
- `db.py`

Required environment variables:
- DATABASE_URL
- TARGET_FOCUS_STATES
- LOG_LEVEL

### Prompt for Cursor
```text
Implement `config.py` and `db.py` for the `target_pipeline` package. Use environment variables loaded from `.env`. Add a typed settings class for DATABASE_URL, TARGET_FOCUS_STATES, and LOG_LEVEL. Create a SQLAlchemy engine connection helper in `db.py`. Keep this isolated from the existing app and do not alter existing app database code.
```

### Expected outcome
- Pipeline can connect to the existing database
- Configuration is centralized
- No existing code is touched

---

## STEP 3 — Define the minimum target table contract

Do not redesign the app.
Just define the minimum schema the pipeline expects.

If the `targets` table already exists, map to it.
If not, create a migration or SQL file for the minimum required fields.

Minimum fields:
- id
- target_name
- state
- county
- plss
- commodity
- source_count
- has_report
- score
- created_at

Recommended extra fields:
- deposit_names_json
- claim_ids_json
- report_links_json
- status
- notes

### Prompt for Cursor
```text
Create a minimal database model or SQL contract for the `targets` table used by the target pipeline. Do not redesign the application database. If a targets table already exists, adapt to it. Otherwise define the smallest practical schema needed for the pipeline output, including target_name, state, county, plss, commodity, source_count, has_report, score, created_at, and optional JSON fields for deposit names, claim IDs, and report links.
```

---

## STEP 4 — Build source loaders for MLRS and USGS

Implement:
- `sources/mlrs.py`
- `sources/usgs.py`

These should load from local raw files first.
Do not start with live scraping.
Assume CSV, GeoJSON, shapefile exports, or prepared flat files.

Each source loader should return normalized raw dictionaries with fields like:
- source
- name
- state
- county
- commodity_raw
- plss_raw
- latitude
- longitude
- reports
- status

### Prompt for Cursor
```text
Implement `sources/mlrs.py` and `sources/usgs.py` for the target pipeline. Start with local file ingestion only, not live scraping. Support reading CSV and GeoJSON first, with extension points for shapefiles later. Return lists of raw dictionaries with consistent keys such as source, name, state, county, commodity_raw, plss_raw, latitude, longitude, reports, and status.
```

### Expected outcome
- Pipeline can ingest source exports placed in a local data folder
- Sources return consistent intermediate rows

---

## STEP 5 — Build normalization utilities

Implement:
- `processors/normalize.py`
- `processors/commodities.py`
- `processors/plss.py`

Requirements:
- normalize names
- normalize state abbreviations
- map commodity aliases to canonical names
- parse common PLSS patterns like `T12S R8W Sec 14`

### Prompt for Cursor
```text
Implement normalization utilities for the target pipeline. In `normalize.py`, add helpers for normalizing names and state/county strings. In `commodities.py`, add a canonical commodity mapping system that converts aliases like au/gold into standard output values. In `plss.py`, add a parser for common PLSS text formats such as `T12S R8W Sec 14`, `Sec 14 T12S R8W`, and similar variations. Return canonical PLSS strings suitable for grouping.
```

### Expected outcome
- Raw source rows can be turned into consistent normalized records
- PLSS becomes groupable
- Commodity labels become stable

---

## STEP 6 — Add spatial fallback matching

Implement:
- `matchers/spatial.py`

Use this only when PLSS is missing from the raw row but coordinates exist.

Behavior:
- if row has lat/lon and a PLSS lookup layer is available, assign nearest/intersecting PLSS
- otherwise leave PLSS null and flag for review

Do not overbuild GIS.
Keep it lightweight and optional.

### Prompt for Cursor
```text
Implement a lightweight spatial matching module in `matchers/spatial.py`. Use it only as a fallback when a record has latitude/longitude but no parsed PLSS. Design it so it can optionally read a PLSS lookup layer and return an intersecting or nearest PLSS string. If no lookup layer is available, leave PLSS unresolved and expose a review flag. Keep this module simple and optional.
```

---

## STEP 7 — Create the normalized record pipeline

After source loading, build a normalization pass that transforms every raw source row into a clean record with:

- source
- normalized_name
- state
- county
- commodity
- plss
- reports
- status
- latitude
- longitude
- review_flags

### Prompt for Cursor
```text
Add a normalization pipeline stage that takes rows from MLRS and USGS source loaders and converts them into a clean standardized record shape. Each standardized record should include source, normalized_name, state, county, commodity, plss, reports, status, latitude, longitude, and review_flags. Use the normalization, commodity, and PLSS parser utilities. Preserve original raw values when helpful for traceability.
```

---

## STEP 8 — Build the Target builder

Implement:
- `targets/builder.py`

Rules:
- group by `(plss, commodity)` for v1
- each grouped cluster becomes one Target
- include:
  - target_name
  - state
  - county
  - plss
  - commodity
  - deposit_names
  - claim_ids
  - report_links
  - source_count

Suggested target_name format:
- `{commodity} Target {plss}`
- if county available: `{commodity} Target {plss} - {county} County`

### Prompt for Cursor
```text
Implement `targets/builder.py` for the target pipeline. Build v1 targets by grouping normalized records by `(plss, commodity)`. Each group should produce a target object containing target_name, state, county, plss, commodity, deposit_names, claim_ids, report_links, source_count, and any supporting record metadata needed for scoring. Keep the implementation straightforward and deterministic.
```

### Expected outcome
- Target rows are generated cleanly from grouped normalized records

---

## STEP 9 — Build the scoring engine

Implement:
- `targets/scorer.py`

V1 scoring:
- +3 if target has at least one deposit/deposit-like record
- +2 if target has at least one report/reference
- +2 if target has multiple supporting source records
- +1 if target has related claims

Also compute:
- has_report boolean
- confidence notes or review flags

### Prompt for Cursor
```text
Implement `targets/scorer.py` for the target pipeline. Use a simple v1 scoring model: +3 if the target has at least one deposit or occurrence record, +2 if it has one or more reports/references, +2 if it has multiple supporting source records, and +1 if it has related claims. Return both the numeric score and convenient derived fields like has_report and confidence/review notes.
```

---

## STEP 10 — Build database writer / upsert logic

Implement:
- `outputs/db_writer.py`

Requirements:
- upsert by stable business key, preferably `(plss, commodity)` for v1
- write only target output rows
- do not delete existing app data outside this scope
- store JSON arrays for deposits, claims, reports where supported

### Prompt for Cursor
```text
Implement `outputs/db_writer.py` for the target pipeline. Add safe insert/update logic for the `targets` table using a stable v1 business key such as `(plss, commodity)`. Only write target output rows. Do not modify unrelated application tables. Store deposit names, claim IDs, and report links in JSON-capable fields when available, and degrade gracefully if the existing schema is simpler.
```

### Expected outcome
- Running the pipeline populates or updates target rows in the existing database

---

## STEP 11 — Create the orchestration script

Implement:
- `run.py`

Flow:
1. load MLRS
2. load USGS
3. normalize everything
4. build targets
5. score targets
6. write to DB
7. print summary

### Prompt for Cursor
```text
Implement `run.py` as the orchestration entry point for the target pipeline. It should load MLRS and USGS source data, normalize records, optionally perform PLSS spatial fallback matching, build targets, score them, and upsert them into the database. At the end, print a concise summary showing counts for raw rows loaded, normalized rows, targets generated, and targets written.
```

---

## STEP 12 — Add logging and error handling

Implement:
- `logging_config.py`

Requirements:
- structured but simple logging
- log source counts
- log failed rows with reason
- continue processing when possible

### Prompt for Cursor
```text
Add simple structured logging and resilient error handling to the target pipeline. Create `logging_config.py` and use it across the pipeline. Log source ingest counts, normalization failures, unresolved PLSS records, targets generated, and database writes. The pipeline should continue processing where practical instead of aborting on a single bad row.
```

---

## STEP 13 — Add basic tests

Implement:
- `tests/test_plss.py`
- `tests/test_commodities.py`
- `tests/test_builder.py`

Test at minimum:
- PLSS parsing cases
- commodity alias mapping
- grouping logic

### Prompt for Cursor
```text
Add pytest-based tests for the target pipeline. Include tests for PLSS parsing, commodity normalization, and target grouping logic. Use small inline fixture records and keep the tests fast and easy to understand.
```

---

## STEP 14 — Add README instructions for operators

The README should explain:
- where to place source files
- how to configure `.env`
- how to run the pipeline
- what table gets written
- what assumptions the pipeline makes

### Prompt for Cursor
```text
Write a practical README for the target pipeline. Explain setup, required environment variables, expected local source file locations, how to run the pipeline, what the output schema is, and what assumptions are made in v1. Keep it focused on operators and developers using the existing app.
```

---

# RECOMMENDED V1 DATA CONTRACT

Normalized record shape:

```python
{
    "source": "mlrs" | "usgs",
    "record_type": "claim" | "deposit",
    "raw_name": str,
    "normalized_name": str,
    "state": str | None,
    "county": str | None,
    "commodity": str | None,
    "plss": str | None,
    "latitude": float | None,
    "longitude": float | None,
    "reports": list[str],
    "status": str | None,
    "review_flags": list[str],
    "raw": dict,
}
```

Target output shape:

```python
{
    "target_name": str,
    "state": str | None,
    "county": str | None,
    "plss": str,
    "commodity": str | None,
    "source_count": int,
    "has_report": bool,
    "score": int,
    "deposit_names": list[str],
    "claim_ids": list[str],
    "report_links": list[str],
}
```

---

# RECOMMENDED DEVELOPMENT APPROACH

1. Build with local test files first
2. Verify normalized outputs in JSON before DB writes
3. Verify grouping logic before scoring
4. Verify DB upsert with a few rows before full runs
5. Only later add more sources, more fields, and smarter spatial logic

---

# WHAT CURSOR SHOULD NOT DO

- Do not rebuild the existing app
- Do not replace the existing backend
- Do not create a whole new frontend
- Do not build a giant GIS platform
- Do not introduce unnecessary microservices
- Do not over-abstract the first version

---

# FINAL CURSOR INSTRUCTION

Build only an isolated Python Target Generation Pipeline that feeds the existing Mining OS application through the database.

The deliverable is:
- a runnable `target_pipeline/` package
- source loaders for MLRS and USGS exports
- normalization and PLSS grouping
- target generation and scoring
- safe database upserts into the existing `targets` table

The existing application should remain intact.
