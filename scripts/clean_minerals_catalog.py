#!/usr/bin/env python3
"""
Clean ``minerals_of_interest`` so every row is a single canonical mineral.

For each row whose ``name`` expands (via _normalize_minerals) to either:
  * multiple distinct minerals (e.g. "Au Cu Ag …" -> [Gold, Copper, Silver, …]), or
  * a single mineral with a different canonical name (e.g. "Be" -> Beryllium),
we insert each canonical name (if missing) and delete the compound/abbreviated
original row.

Idempotent. Safe to run repeatedly. Use --dry-run first.

Usage:
    .venv/bin/python -m scripts.clean_minerals_catalog --dry-run
    DATABASE_URL='postgresql://...' .venv/bin/python -m scripts.clean_minerals_catalog
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

log = logging.getLogger("clean_minerals_catalog")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")

    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT id, name, sort_order FROM minerals_of_interest ORDER BY sort_order, name")
        ).mappings().all()
        existing_lower = {r["name"].strip().lower(): r["id"] for r in rows if r["name"]}
        max_order = max((r["sort_order"] or 0) for r in rows) if rows else 0

    log.info("loaded %d catalog rows", len(rows))

    to_delete: list[tuple[int, str]] = []
    to_insert: list[str] = []
    seen_inserts: set[str] = set()

    for r in rows:
        rid = r["id"]
        name = (r["name"] or "").strip()
        if not name:
            to_delete.append((rid, name))
            continue
        canon = _normalize_minerals([name])
        # Decide whether this row is "compound or abbreviated"
        if not canon:
            continue
        if len(canon) == 1 and canon[0].strip().lower() == name.lower():
            continue  # already canonical
        # Either multi-mineral OR single mineral with different canonical spelling
        for nm in canon:
            key = nm.strip().lower()
            if not key:
                continue
            if key in existing_lower:
                continue
            if key in seen_inserts:
                continue
            seen_inserts.add(key)
            to_insert.append(nm)
        to_delete.append((rid, name))

    log.info("to_insert: %d new canonical minerals", len(to_insert))
    log.info("to_delete: %d compound/abbrev rows", len(to_delete))
    for nm in to_insert[:25]:
        log.info("  + %s", nm)
    for rid, nm in to_delete[:25]:
        log.info("  - id=%s %s", rid, nm)

    if args.dry_run:
        log.info("DRY RUN — no changes written")
        return 0

    with eng.begin() as conn:
        # Insert new canonical names first
        for i, nm in enumerate(to_insert, start=1):
            conn.execute(
                text(
                    "INSERT INTO minerals_of_interest (name, sort_order) "
                    "VALUES (:name, :sort_order) ON CONFLICT (name) DO NOTHING"
                ),
                {"name": nm, "sort_order": max_order + i},
            )
        # Delete compound rows
        if to_delete:
            ids = [rid for rid, _ in to_delete]
            conn.execute(text("DELETE FROM minerals_of_interest WHERE id = ANY(:ids)"), {"ids": ids})

    log.info("DONE: inserted %d, deleted %d", len(to_insert), len(to_delete))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
