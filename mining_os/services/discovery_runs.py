"""Persist and query discovery run history."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from mining_os.db import get_engine
from mining_os.services.auth import current_account_id

log = logging.getLogger(__name__)


def _effective_account_id(account_id: int | None = None) -> int:
    return int(account_id or current_account_id())


def create_run(
    *,
    replace: bool,
    limit_per_mineral: int,
    status: str,
    message: Optional[str] = None,
    minerals_checked: Optional[List[str]] = None,
    areas_added: int = 0,
    log: Optional[List[str]] = None,
    errors: Optional[List[str]] = None,
    locations_from_ai: Optional[List[Dict[str, Any]]] = None,
    urls_from_web_search: Optional[List[str]] = None,
    account_id: int | None = None,
) -> int:
    """Insert a discovery run record; returns id. Works with or without 007 migration columns."""
    account_id = _effective_account_id(account_id)
    engine = get_engine()
    params = {
        "account_id": account_id,
        "replace": replace,
        "limit_per_mineral": limit_per_mineral,
        "status": status,
        "message": message,
        "minerals_checked": minerals_checked or [],
        "areas_added": areas_added,
        "log": log or [],
        "errors": errors or [],
    }
    with engine.connect() as conn:
        try:
            row = conn.execute(
                text("""
                    INSERT INTO discovery_runs
                    (account_id, replace, limit_per_mineral, status, message, minerals_checked, areas_added, log, errors,
                     locations_from_ai, urls_from_web_search)
                    VALUES (:account_id, :replace, :limit_per_mineral, :status, :message, :minerals_checked, :areas_added, :log, :errors,
                            :locations_from_ai::jsonb, :urls_from_web_search)
                    RETURNING id
                """),
                {**params, "locations_from_ai": json.dumps(locations_from_ai or []), "urls_from_web_search": urls_from_web_search or []},
            )
        except ProgrammingError:
            log.debug("discovery_runs missing 007 columns; saving without locations_from_ai/urls_from_web_search")
            row = conn.execute(
                text("""
                    INSERT INTO discovery_runs
                    (account_id, replace, limit_per_mineral, status, message, minerals_checked, areas_added, log, errors)
                    VALUES (:account_id, :replace, :limit_per_mineral, :status, :message, :minerals_checked, :areas_added, :log, :errors)
                    RETURNING id
                """),
                params,
            )
        run_id = row.scalar_one()
        conn.commit()
        return run_id


def _row_to_list_item(r: Any) -> Dict[str, Any]:
    d = dict(r)
    created = d.get("created_at")
    if hasattr(created, "isoformat"):
        d["created_at"] = created.isoformat()
    return d


def list_runs(limit: int = 50, account_id: int | None = None) -> List[Dict[str, Any]]:
    """Return discovery runs newest first."""
    account_id = _effective_account_id(account_id)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, created_at, replace, limit_per_mineral, status, message,
                       minerals_checked, areas_added,
                       COALESCE(array_length(log, 1), 0) AS log_line_count,
                       COALESCE(array_length(errors, 1), 0) AS error_count
                FROM discovery_runs
                WHERE account_id = :account_id
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"account_id": account_id, "limit": limit},
        ).mappings().all()
        return [_row_to_list_item(r) for r in rows]


def get_run(run_id: int, account_id: int | None = None) -> Optional[Dict[str, Any]]:
    """Return full discovery run by id. Works with or without 007 migration columns."""
    account_id = _effective_account_id(account_id)
    engine = get_engine()
    with engine.connect() as conn:
        try:
            row = conn.execute(
                text("""
                    SELECT id, created_at, replace, limit_per_mineral, status, message,
                           minerals_checked, areas_added, log, errors,
                           locations_from_ai, urls_from_web_search
                    FROM discovery_runs
                    WHERE id = :id AND account_id = :account_id
                """),
                {"id": run_id, "account_id": account_id},
            ).mappings().first()
        except ProgrammingError:
            log.debug("discovery_runs table missing 007 columns; returning run without locations_from_ai/urls_from_web_search")
            row = conn.execute(
                text("""
                    SELECT id, created_at, replace, limit_per_mineral, status, message,
                           minerals_checked, areas_added, log, errors
                    FROM discovery_runs
                    WHERE id = :id AND account_id = :account_id
                """),
                {"id": run_id, "account_id": account_id},
            ).mappings().first()
        if not row:
            return None
        d = _row_to_list_item(row)
        d["log"] = list(d.get("log") or [])
        d["errors"] = list(d.get("errors") or [])
        locs = d.get("locations_from_ai")
        if locs is None:
            d["locations_from_ai"] = []
        elif isinstance(locs, str):
            try:
                d["locations_from_ai"] = json.loads(locs) if locs else []
            except Exception:
                d["locations_from_ai"] = []
        elif not isinstance(locs, list):
            d["locations_from_ai"] = []
        d["urls_from_web_search"] = list(d.get("urls_from_web_search") or [])
        return d
