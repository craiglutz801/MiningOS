"""CRUD for minerals of interest (backed by DB)."""

from __future__ import annotations

from typing import List

from sqlalchemy import text

from mining_os.db import get_engine


def list_minerals() -> List[dict]:
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT id, name, sort_order, updated_at FROM minerals_of_interest ORDER BY sort_order, name")
        ).mappings().all()
    return [dict(r) for r in rows]


def add_mineral(name: str, sort_order: int | None = None) -> dict:
    eng = get_engine()
    with eng.begin() as conn:
        if sort_order is None:
            r = conn.execute(text("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM minerals_of_interest")).scalar()
            sort_order = r
        conn.execute(
            text(
                "INSERT INTO minerals_of_interest (name, sort_order) VALUES (:name, :sort_order) "
                "ON CONFLICT (name) DO UPDATE SET sort_order = EXCLUDED.sort_order, updated_at = now() "
                "RETURNING id, name, sort_order, updated_at"
            ),
            {"name": name.strip(), "sort_order": sort_order},
        )
        row = conn.execute(
            text("SELECT id, name, sort_order, updated_at FROM minerals_of_interest WHERE name = :name"),
            {"name": name.strip()},
        ).mappings().first()
    return dict(row)


def update_mineral(id: int, name: str | None = None, sort_order: int | None = None) -> dict | None:
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
            text(f"UPDATE minerals_of_interest SET {', '.join(updates)} WHERE id = :id"),
            params,
        )
        return get_mineral(id)


def get_mineral(id: int) -> dict | None:
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT id, name, sort_order, created_at, updated_at FROM minerals_of_interest WHERE id = :id"),
            {"id": id},
        ).mappings().first()
    return dict(row) if row else None


def delete_mineral(id: int) -> bool:
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(text("DELETE FROM minerals_of_interest WHERE id = :id"), {"id": id})
    return r.rowcount > 0
