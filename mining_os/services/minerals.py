"""CRUD for minerals of interest (backed by DB).

The Minerals page is the canonical "every mineral the system knows about"
list. Rules:

  * Any mineral name that appears on a Target (`areas_of_focus.minerals`) MUST
    appear here. We enforce this in two places:
      1. ``ensure_minerals_exist`` — called from ``upsert_area`` after every
         target write to add any new mineral names immediately.
      2. ``list_minerals`` — lazy-syncs on every read so a stale Minerals page
         can never lag behind Targets even if path (1) is bypassed.

  * Manual rows are still editable / re-orderable here; deletion of a mineral
    that is still referenced by a Target will reappear on the next read (by
    design — the Minerals page must stay a true superset of Target minerals).
"""

from __future__ import annotations

import logging
from typing import Iterable, List

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.services.auth import current_account_id

log = logging.getLogger("mining_os.minerals")


def _effective_account_id(account_id: int | None = None) -> int:
    return int(account_id or current_account_id())


def _distinct_target_minerals(conn, account_id: int) -> set[str]:
    rows = conn.execute(
        text("""
        SELECT DISTINCT TRIM(unnest(minerals)) AS name
        FROM areas_of_focus
        WHERE account_id = :account_id
          AND minerals IS NOT NULL AND array_length(minerals, 1) > 0
        """)
        ,
        {"account_id": account_id},
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def _existing_mineral_names(conn, account_id: int) -> set[str]:
    rows = conn.execute(
        text("SELECT name FROM minerals_of_interest WHERE account_id = :account_id"),
        {"account_id": account_id},
    ).fetchall()
    return {r[0].strip() for r in rows if r[0]}


def ensure_minerals_exist(names: Iterable[str], account_id: int | None = None) -> int:
    """Insert any provided mineral names that are not already in the curated list.

    Safe to call from hot paths (idempotent, ON CONFLICT DO NOTHING). Names are
    inserted in alpha order with sort_order appended after the current max so
    they appear at the end of the list (curated rows keep their priority).

    Returns the number of rows actually inserted.
    """
    cleaned = sorted({(n or "").strip() for n in names if (n or "").strip()})
    if not cleaned:
        return 0
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    inserted = 0
    with eng.begin() as conn:
        existing = _existing_mineral_names(conn, account_id)
        existing_lower = {n.lower() for n in existing}
        missing = [n for n in cleaned if n.lower() not in existing_lower]
        if not missing:
            return 0
        max_order = conn.execute(
            text("SELECT COALESCE(MAX(sort_order), 0) FROM minerals_of_interest WHERE account_id = :account_id"),
            {"account_id": account_id},
        ).scalar() or 0
        for i, name in enumerate(missing, start=1):
            r = conn.execute(
                text(
                    "INSERT INTO minerals_of_interest (account_id, name, sort_order) "
                    "VALUES (:account_id, :name, :sort_order) "
                    "ON CONFLICT (account_id, name) DO NOTHING"
                ),
                {"account_id": account_id, "name": name, "sort_order": max_order + i},
            )
            inserted += r.rowcount or 0
    if inserted:
        log.info("ensure_minerals_exist: inserted %d new mineral(s) into minerals_of_interest", inserted)
    return inserted


def list_minerals(account_id: int | None = None) -> List[dict]:
    """Return every mineral known to the system.

    Lazy-syncs missing entries from ``areas_of_focus.minerals`` so the Minerals
    page is always a true superset of what Targets are tagged with.
    """
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        target_names = _distinct_target_minerals(conn, account_id)
        existing = _existing_mineral_names(conn, account_id)
        existing_lower = {n.lower() for n in existing}
        missing = sorted(
            (n for n in target_names if n.lower() not in existing_lower),
            key=lambda x: x.lower(),
        )
        if missing:
            max_order = conn.execute(
                text("SELECT COALESCE(MAX(sort_order), 0) FROM minerals_of_interest WHERE account_id = :account_id"),
                {"account_id": account_id},
            ).scalar() or 0
            for i, name in enumerate(missing, start=1):
                conn.execute(
                    text(
                        "INSERT INTO minerals_of_interest (account_id, name, sort_order) "
                        "VALUES (:account_id, :name, :sort_order) "
                        "ON CONFLICT (account_id, name) DO NOTHING"
                    ),
                    {"account_id": account_id, "name": name, "sort_order": max_order + i},
                )
            log.info("list_minerals: lazy-backfilled %d mineral(s) from targets", len(missing))
        rows = conn.execute(
            text(
                "SELECT id, name, sort_order, updated_at FROM minerals_of_interest "
                "WHERE account_id = :account_id ORDER BY sort_order, name"
            ),
            {"account_id": account_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def add_mineral(name: str, sort_order: int | None = None, account_id: int | None = None) -> dict:
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        if sort_order is None:
            r = conn.execute(
                text("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM minerals_of_interest WHERE account_id = :account_id"),
                {"account_id": account_id},
            ).scalar()
            sort_order = r
        conn.execute(
            text(
                "INSERT INTO minerals_of_interest (account_id, name, sort_order) VALUES (:account_id, :name, :sort_order) "
                "ON CONFLICT (account_id, name) DO UPDATE SET sort_order = EXCLUDED.sort_order, updated_at = now() "
                "RETURNING id, name, sort_order, updated_at"
            ),
            {"account_id": account_id, "name": name.strip(), "sort_order": sort_order},
        )
        row = conn.execute(
            text(
                "SELECT id, name, sort_order, updated_at FROM minerals_of_interest "
                "WHERE account_id = :account_id AND name = :name"
            ),
            {"account_id": account_id, "name": name.strip()},
        ).mappings().first()
    return dict(row)


def update_mineral(id: int, name: str | None = None, sort_order: int | None = None, account_id: int | None = None) -> dict | None:
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    updates = []
    params = {"id": id}
    if name is not None:
        updates.append("name = :name")
        params["name"] = name.strip()
    if sort_order is not None:
        updates.append("sort_order = :sort_order")
        params["sort_order"] = sort_order
    if not updates:
        return get_mineral(id)
    updates.append("updated_at = now()")
    with eng.begin() as conn:
        conn.execute(
            text(f"UPDATE minerals_of_interest SET {', '.join(updates)} WHERE id = :id AND account_id = :account_id"),
            params,
        )
        return get_mineral(id, account_id=account_id)


def get_mineral(id: int, account_id: int | None = None) -> dict | None:
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, sort_order, created_at, updated_at FROM minerals_of_interest "
                "WHERE id = :id AND account_id = :account_id"
            ),
            {"id": id, "account_id": account_id},
        ).mappings().first()
    return dict(row) if row else None


def delete_mineral(id: int, account_id: int | None = None) -> bool:
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("DELETE FROM minerals_of_interest WHERE id = :id AND account_id = :account_id"),
            {"id": id, "account_id": account_id},
        )
    return r.rowcount > 0
