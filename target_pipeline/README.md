# Target generation pipeline

Isolated Python tooling to build **target rows** from local MLRS / USGS-style exports, normalize PLSS and commodities, score groups, and **upsert into the same Postgres database** the Mining OS app already uses.

This package does **not** modify application code under `mining_os/` or `frontend/`.

**Scope:** Rows are kept only if **state** is in `UT, ID, WY, NV, AZ, MT` (override with `TARGET_PIPELINE_STATES`) **and** at least one **critical mineral** appears in commodity fields: tungsten, scandium, beryllium, uranium, fluorspar/fluorite, germanium, gallium (aliases like W, U, Sc, … are matched in `target_pipeline/filters.py`).

## Where the app stores targets

The UI reads **`areas_of_focus`**, not a table named `targets`. By default this pipeline writes there with **`source = 'target_pipeline'`** (so you can filter in SQL or the app), merges minerals and report links on duplicate `plss_normalized`, and stores **`characteristics.target_pipeline`**, including **`managed: true`**, **`import_tag: "target_pipeline"`**, and a **`run_id`** (from `TARGET_PIPELINE_RUN_ID` or an auto UTC timestamp). Optional **latitude** / **longitude** (WGS84, decimal degrees) are written when source rows include coordinates.

If you use the standalone **`targets`** table and it was created before coordinate columns existed, run `target_pipeline/sql/alter_targets_add_lat_lon.sql` once against your database.

### Removing pipeline imports

- **All** pipeline targets:  
  `PYTHONPATH=. python3 -m target_pipeline.cleanup_pipeline_targets --execute`
- **Preview count only** (no delete): omit `--execute`.
- **One batch only** (same `run_id`):  
  `PYTHONPATH=. python3 -m target_pipeline.cleanup_pipeline_targets --run-id YOUR_RUN_ID --execute`

Or run SQL from `target_pipeline/sql/delete_target_pipeline_targets.sql` in `psql`.

**Caution:** If the pipeline **updates** an existing target (same `plss_normalized`) that was created manually, that row’s `source` becomes `target_pipeline` and could be deleted by the cleanup. Prefer running on PLSS your manual data does not use, or back up first.

The app enforces **one row per `plss_normalized`**. Pipeline logic groups by `(PLSS, commodity)` first; when `MERGE_BY_PLSS_FOR_APP=true` (default), rows for the same section are **collapsed** into a single app target with multiple minerals.

## Setup

From the repository root, use **`python3`** and preferably the project **venv** (Apple’s system `python3` is often 3.9 and may not have SQLAlchemy/psycopg installed):

```bash
source .venv/bin/activate   # optional but recommended
pip install -r target_pipeline/requirements.txt
```

Copy `target_pipeline/.env.example` to `target_pipeline/.env` or add variables to the root `.env`.

Required:

- `DATABASE_URL` — same URL as Mining OS, e.g. `postgresql+psycopg://miningos:miningos@localhost:5432/miningos`

Optional:

- `TARGET_PIPELINE_STATES` (or legacy `TARGET_FOCUS_STATES`) — comma-separated state codes; default is UT,ID,WY,NV,AZ,MT
- `TARGET_PIPELINE_DATA_DIR` — folder containing `mlrs/` and `usgs/` subfolders (default `./target_pipeline/data` relative to repo root)
- `OUTPUT_TABLE` — `areas_of_focus` (default) or `targets`
- `MERGE_BY_PLSS_FOR_APP` — `true` / `false` (only applies to `areas_of_focus`)
- `PLSS_LOOKUP_GEOJSON` — optional polygon GeoJSON for PLSS when coordinates exist but PLSS text is missing
- `TARGET_PIPELINE_DRY_RUN` — `true` to skip DB writes and log a sample of targets
- `TARGET_PIPELINE_RUN_ID` — optional label stored in `characteristics.target_pipeline.run_id` for batch deletes

## Data layout

Place exports under:

```text
target_pipeline/data/mlrs/    # CSV, GeoJSON, or JSON FeatureCollections
target_pipeline/data/usgs/
```

Column names are matched flexibly (e.g. `claim_name` / `name`, `plss` / `location_plss`, `commodity` / `minerals`). GeoJSON point coordinates populate latitude/longitude when missing from properties.

## Run

```bash
cd /path/to/Mining_OS
PYTHONPATH=. python3 -m target_pipeline.run
```

Quick validation without Postgres (uses a placeholder URL; no DB connection):

```bash
TARGET_PIPELINE_DRY_RUN=true PYTHONPATH=. python3 -m target_pipeline.run
```

## Optional `targets` table

If you want a separate `(plss_normalized, commodity)` table for analytics (the **app does not read it**), apply:

```bash
psql "$DATABASE_URL" -f target_pipeline/sql/create_targets_table.sql
```

Then set `OUTPUT_TABLE=targets`.

## Scoring (v1)

- +3 at least one deposit-class record  
- +2 reports / references  
- +2 more than one deposit  
- +1 related claims  

## Tests

```bash
PYTHONPATH=. pytest target_pipeline/tests -q
```
