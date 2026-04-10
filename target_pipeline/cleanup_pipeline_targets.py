#!/usr/bin/env python3
"""
Remove targets created by target_pipeline (areas_of_focus.source = 'target_pipeline').

Default: print how many rows match (dry run). Use --execute to DELETE.

Optional: --run-id <id> to delete only rows from one import (characteristics.target_pipeline.run_id).

Usage (from repo root):
  PYTHONPATH=. python3 -m target_pipeline.cleanup_pipeline_targets
  PYTHONPATH=. python3 -m target_pipeline.cleanup_pipeline_targets --execute
  PYTHONPATH=. python3 -m target_pipeline.cleanup_pipeline_targets --run-id 2026-04-02T120000Z --execute
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

from sqlalchemy import text

from target_pipeline.config import get_settings
from target_pipeline.db import get_engine


def main() -> int:
    p = argparse.ArgumentParser(description="Remove target_pipeline imports from areas_of_focus")
    p.add_argument(
        "--execute",
        action="store_true",
        help="Actually DELETE rows (default is count-only)",
    )
    p.add_argument(
        "--run-id",
        dest="run_id",
        default="",
        help="Only delete rows whose characteristics.target_pipeline.run_id matches",
    )
    args = p.parse_args()

    get_settings()  # validates DATABASE_URL
    eng = get_engine()

    params: dict = {}
    if args.run_id.strip():
        where_sql = (
            "source = 'target_pipeline' "
            "AND characteristics->'target_pipeline'->>'run_id' = :run_id"
        )
        params["run_id"] = args.run_id.strip()
    else:
        where_sql = "source = 'target_pipeline'"

    with eng.connect() as conn:
        n = conn.execute(
            text(f"SELECT count(*) FROM areas_of_focus WHERE {where_sql}"),
            params,
        ).scalar()
        n = int(n or 0)

    print(f"Matching rows: {n}")
    if n == 0:
        return 0

    if not args.execute:
        print("Dry run only. To delete, run again with --execute")
        if args.run_id:
            print(f"  (filtered by run_id={args.run_id!r})")
        return 0

    with eng.begin() as conn:
        res = conn.execute(
            text(f"DELETE FROM areas_of_focus WHERE {where_sql}"),
            params,
        )
        deleted = int(res.rowcount or 0)
    print(f"Deleted {deleted} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
