"""
CLI entry-point for the Mining_OS pipeline.

Usage examples:
    python -m mining_os.pipelines.run_all --init-db
    python -m mining_os.pipelines.run_all --all
    python -m mining_os.pipelines.run_all --all --max-records 500
    python -m mining_os.pipelines.run_all --ingest
    python -m mining_os.pipelines.run_all --candidates
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy import text

from mining_os.db import exec_sql
from mining_os.db import get_engine
from mining_os.logging_setup import setup_logging
from mining_os.pipelines.build_candidates import build as build_candidates
from mining_os.pipelines.ingest_blm_claims import ingest_closed, ingest_open
from mining_os.pipelines.ingest_mrds import ingest as ingest_mrds
from mining_os.pipelines.ingest_plss import ingest as ingest_plss

log = logging.getLogger("mining_os.run_all")


def _auth_schema_present() -> bool:
    eng = get_engine()
    with eng.begin() as conn:
        return bool(
            conn.execute(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                      FROM information_schema.columns
                      WHERE table_schema = 'public'
                        AND table_name = 'areas_of_focus'
                        AND column_name = 'account_id'
                    )
                    """
                )
            ).scalar()
        )


def init_db() -> None:
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    pre_auth_sql = [
        "001_init.sql",
        "002_minerals_and_focus.sql",
        "003_update_minerals_to_six.sql",
        "004_areas_state_claim_type.sql",
        "005_discovery_prompts.sql",
        "006_discovery_runs.sql",
        "007_discovery_runs_locations.sql",
        "008_targets_plss_unique.sql",
    ]
    auth_safe_sql = [
        "001_init.sql",
        "004_areas_state_claim_type.sql",
        "006_discovery_runs.sql",
        "007_discovery_runs_locations.sql",
        "010_add_priority.sql",
        "011_priority_default_low.sql",
        "012_characteristics.sql",
        "013_add_meridian.sql",
        "015_plss_components_and_uploaded.sql",
        "016_areas_optional_coordinates.sql",
        "017_automation_engine.sql",
        "018_add_retrieval_type.sql",
        "019_accounts_auth.sql",
    ]
    auth_present = _auth_schema_present()
    sql_sequence = auth_safe_sql if auth_present else pre_auth_sql
    for name in sql_sequence:
        sql_path = sql_dir / name
        if sql_path.exists():
            exec_sql(sql_path.read_text())
            log.info("DB initialised from %s", sql_path)
    if not auth_present:
        # Section-level (sector) PLSS: backfill plss_normalized then re-merge duplicates
        try:
            from mining_os.services.areas_of_focus import backfill_plss_normalized_to_section
            backfill_plss_normalized_to_section()
        except Exception as e:
            log.warning("PLSS section backfill skipped or failed: %s", e)
        for name in [
            "009_plss_section_merge.sql",
            "010_add_priority.sql",
            "011_priority_default_low.sql",
            "012_characteristics.sql",
            "013_add_meridian.sql",
            "014_update_discovery_plss_prompt.sql",
            "015_plss_components_and_uploaded.sql",
            "016_areas_optional_coordinates.sql",
            "017_automation_engine.sql",
            "018_add_retrieval_type.sql",
            "019_accounts_auth.sql",
        ]:
            sql_path = sql_dir / name
            if sql_path.exists():
                exec_sql(sql_path.read_text())
                log.info("DB initialised from %s", sql_path)
    try:
        from mining_os.services.areas_of_focus import backfill_plss_components
        backfill_plss_components()
    except Exception as e:
        log.warning("PLSS components backfill skipped or failed: %s", e)


def main() -> None:
    setup_logging("INFO")

    parser = argparse.ArgumentParser(description="Mining_OS pipeline runner")
    parser.add_argument("--init-db", action="store_true", help="Create/update DB schema")
    parser.add_argument("--all", action="store_true", help="Run full pipeline (ingest + candidates)")
    parser.add_argument("--ingest", action="store_true", help="Ingest only")
    parser.add_argument("--candidates", action="store_true", help="Build candidates only")
    parser.add_argument("--max-records", type=int, default=None, help="Limit records per ingest source (debug)")
    args = parser.parse_args()

    if args.init_db:
        init_db()

    if args.all or args.ingest:
        ingest_open(max_records=args.max_records)
        ingest_closed(max_records=args.max_records)
        ingest_plss(max_records=args.max_records)
        ingest_mrds(max_records=args.max_records or 20000)

    if args.all or args.candidates:
        build_candidates()

    if not any([args.init_db, args.all, args.ingest, args.candidates]):
        parser.print_help()


if __name__ == "__main__":
    main()
