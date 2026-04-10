#!/usr/bin/env python3
"""
Orchestration: load MLRS + USGS → normalize → optional spatial PLSS → build → score → DB.

Run from repository root:
  python -m target_pipeline.run

Or with PYTHONPATH:
  PYTHONPATH=. python target_pipeline/run.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

if os.environ.get("TARGET_PIPELINE_DRY_RUN", "").strip().lower() in ("1", "true", "yes"):
    # Allow dry-run without a real DATABASE_URL (no connection is opened).
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://noop:noop@127.0.0.1:5432/noop",
    )

from target_pipeline.config import get_settings
from target_pipeline.filters import gather_commodity_text, match_target_mineral
from target_pipeline.db import get_engine
from target_pipeline.logging_config import log_counts, setup_logging
from target_pipeline.matchers.spatial import load_plss_lookup_geojson
from target_pipeline.outputs.db_writer import write_targets
from target_pipeline.processors.normalize import apply_spatial_plss_if_needed, standardize_raw_row
from target_pipeline.sources.mlrs import load_mlrs_rows
from target_pipeline.sources.usgs import load_usgs_rows
from target_pipeline.targets.builder import build_targets
from target_pipeline.targets.scorer import score_target

log = logging.getLogger("target_pipeline.run")


def main() -> int:
    setup_logging()
    settings = get_settings()
    data_dir = settings.target_pipeline_data_dir
    if not Path(data_dir).is_absolute():
        data_dir = str(_REPO_ROOT / data_dir)

    lookup_path = os.environ.get("PLSS_LOOKUP_GEOJSON", "").strip()
    spatial_features = load_plss_lookup_geojson(lookup_path) if lookup_path else None

    cap = settings.max_rows_per_source
    raw_mlrs = load_mlrs_rows(data_dir, max_rows=cap)
    raw_usgs = load_usgs_rows(data_dir, max_rows=cap)
    raw_all = raw_mlrs + raw_usgs
    log_counts(log, "raw_loaded", mlrs=len(raw_mlrs), usgs=len(raw_usgs), total=len(raw_all))
    if not raw_all:
        log.warning(
            "No rows loaded. Add .csv or .geojson files under %s/mlrs/ and %s/usgs/ "
            "(see sample_mrds_export.csv and sample_mlrs_export.csv).",
            data_dir,
            data_dir,
        )

    default_state = next(iter(sorted(settings.target_states))) if settings.target_states else "UT"

    standardized: list = []
    norm_errors = 0
    for raw in raw_all:
        try:
            rec = standardize_raw_row(raw, default_state=default_state)
            rec = apply_spatial_plss_if_needed(rec, spatial_features, default_state=default_state)
            st = rec.get("state")
            if not st or st not in settings.target_states:
                continue
            mineral = match_target_mineral(gather_commodity_text(raw))
            if not mineral:
                continue
            rec["commodity"] = mineral
            standardized.append(rec)
        except Exception as e:
            norm_errors += 1
            log.warning("normalize failed: %s (%s)", e, raw.get("name"))

    log_counts(
        log,
        "normalized",
        rows=len(standardized),
        errors=norm_errors,
        focus_states=len(settings.target_states),
    )

    built = build_targets(standardized)
    scored = [score_target(dict(t)) for t in built]
    log_counts(log, "targets", built=len(built), scored=len(scored))

    dry = os.environ.get("TARGET_PIPELINE_DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    if dry:
        log.info("TARGET_PIPELINE_DRY_RUN set — skipping database write")
        if scored:
            log.info("Sample targets (up to 10):")
            for t in scored[:10]:
                log.info(
                    "  %s | score=%s | sources=%s | plss=%s",
                    t.get("target_name"),
                    t.get("score"),
                    t.get("source_count"),
                    t.get("plss_normalized") or t.get("plss"),
                )
        return 0

    rid = os.environ.get("TARGET_PIPELINE_RUN_ID", "").strip() or "(auto UTC timestamp per row in characteristics)"
    log.info("Import tag: source=target_pipeline, characteristics.target_pipeline.managed=true, run_id=%s", rid)
    eng = get_engine()
    stats = write_targets(eng, scored, settings)
    log_counts(log, "db_write", **{k: int(v) for k, v in stats.items()})
    log.info("done — remove with: PYTHONPATH=. python3 -m target_pipeline.cleanup_pipeline_targets --execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
