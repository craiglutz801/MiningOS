#!/usr/bin/env python3
"""
Re-normalize areas_of_focus.minerals so no row contains a chemical symbol or
USGS MRDS commodity abbreviation. Codes are mapped to full canonical names by
``mining_os.services.areas_of_focus._normalize_minerals``.

Usage (from repo root):

    # Local DB (uses .env):
    .venv/bin/python -m scripts.backfill_mineral_names --dry-run
    .venv/bin/python -m scripts.backfill_mineral_names

    # Production DB:
    DATABASE_URL='postgresql://...' .venv/bin/python -m scripts.backfill_mineral_names --dry-run
    DATABASE_URL='postgresql://...' .venv/bin/python -m scripts.backfill_mineral_names
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from sqlalchemy import text  # noqa: E402

from mining_os.db import get_engine  # noqa: E402
from mining_os.services.areas_of_focus import _normalize_minerals  # noqa: E402

log = logging.getLogger("backfill_mineral_names")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Only print what would change")
    parser.add_argument("--limit", type=int, default=None, help="Cap rows processed (debug)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    eng = get_engine()
    rows: list[tuple[int, str, list[str]]] = []
    with eng.begin() as conn:
        sql = "SELECT id, name, COALESCE(minerals, '{}') AS minerals FROM areas_of_focus WHERE minerals IS NOT NULL ORDER BY id"
        if args.limit:
            sql += f" LIMIT {int(args.limit)}"
        for r in conn.execute(text(sql)).mappings():
            rows.append((r["id"], r["name"], list(r["minerals"] or [])))

    log.info("loaded %d rows", len(rows))
    changed = 0
    sample_changes = 0
    with eng.begin() as conn:
        for rid, name, old in rows:
            new = _normalize_minerals(old)
            if new == old:
                continue
            changed += 1
            if sample_changes < 12:
                log.info("id=%-6d %-40s  %s -> %s", rid, (name or "")[:40], old, new)
                sample_changes += 1
            if not args.dry_run:
                conn.execute(
                    text("UPDATE areas_of_focus SET minerals = :m, updated_at = now() WHERE id = :id"),
                    {"m": new, "id": rid},
                )
    log.info("DONE: %d rows %s (of %d total)",
             changed, "would update" if args.dry_run else "updated", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
