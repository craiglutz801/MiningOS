#!/usr/bin/env python3
"""Copy Utah Active Mine Search data from localhost DB → production DB.

Usage (never prints connection strings):

  # Local is the usual POSTGRES_* / empty DATABASE_URL fallback.
  # Production URL must be provided explicitly:
  PROD_DATABASE_URL='postgresql+psycopg://…' \\
    .venv/bin/python scripts/sync_ut_active_mines_to_prod.py

What is copied (account_id=1 by default):
  - active_mine_intel.runs / candidate_sites / candidate_matches / fetch_jobs for UT
  - areas_of_focus rows linked from those UT sites (for MLRS claim_records / drilldown)

UT candidate_sites on prod for that account are replaced. Linked Targets are
upserted by id (or inserted if missing). Does not delete unrelated production Targets.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

try:
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover
    Jsonb = None  # type: ignore


def _as_jsonb(val: Any) -> Any:
    if val is None:
        return None
    if Jsonb is not None and isinstance(val, (dict, list)):
        return Jsonb(val)
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return val


ACCOUNT_ID = int(os.getenv("AMI_SYNC_ACCOUNT_ID") or "1")
STATE = (os.getenv("AMI_SYNC_STATE") or "UT").upper()


def _local_engine() -> Engine:
    from mining_os.db import get_engine

    return get_engine()


def _prod_engine() -> Engine:
    url = (os.getenv("PROD_DATABASE_URL") or "").strip()
    if not url:
        print("ERROR: set PROD_DATABASE_URL to the production Postgres URL.", file=sys.stderr)
        sys.exit(2)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True)


def _rows(eng: Engine, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    with eng.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def main() -> int:
    local = _local_engine()
    prod = _prod_engine()

    sites = _rows(
        local,
        """
        SELECT * FROM active_mine_intel.candidate_sites
        WHERE account_id = :aid AND state_abbr = :st
        """,
        {"aid": ACCOUNT_ID, "st": STATE},
    )
    if not sites:
        print(f"No local {STATE} candidate_sites for account {ACCOUNT_ID}.")
        return 1

    area_ids = sorted({int(s["area_of_focus_id"]) for s in sites if s.get("area_of_focus_id")})
    run_ids = sorted({str(s["run_id"]) for s in sites if s.get("run_id")})

    areas = []
    if area_ids:
        areas = _rows(
            local,
            """
            SELECT * FROM areas_of_focus
            WHERE account_id = :aid AND id = ANY(:ids)
            """,
            {"aid": ACCOUNT_ID, "ids": area_ids},
        )

    runs = []
    if run_ids:
        runs = _rows(
            local,
            """
            SELECT * FROM active_mine_intel.runs
            WHERE account_id = :aid AND id::text = ANY(:ids)
            """,
            {"aid": ACCOUNT_ID, "ids": run_ids},
        )

    matches = _rows(
        local,
        """
        SELECT m.*
        FROM active_mine_intel.candidate_matches m
        WHERE m.account_id = :aid
          AND m.mine_site_id IN (
            SELECT mine_site_id FROM active_mine_intel.candidate_sites
            WHERE account_id = :aid AND state_abbr = :st
          )
        """,
        {"aid": ACCOUNT_ID, "st": STATE},
    )

    fetch_jobs = _rows(
        local,
        """
        SELECT * FROM active_mine_intel.fetch_jobs
        WHERE account_id = :aid AND (state_abbr = :st OR state_abbr IS NULL)
        """,
        {"aid": ACCOUNT_ID, "st": STATE},
    )

    print(
        f"Local {STATE}: sites={len(sites)} areas={len(areas)} "
        f"runs={len(runs)} matches={len(matches)} fetch_jobs={len(fetch_jobs)}"
    )

    # Ensure schema on prod (idempotent migrations).
    sql_dir = os.path.join(os.path.dirname(__file__), "..", "mining_os", "sql")
    for name in (
        "026_active_mine_intel.sql",
        "027_active_mine_mlrs_claim_count.sql",
        "028_active_mine_fetch_progress.sql",
        "029_active_mine_paid_unknown_counts.sql",
    ):
        path = os.path.join(sql_dir, name)
        with open(path, encoding="utf-8") as f:
            body = f.read()
        with prod.begin() as conn:
            conn.execute(text(body))
    print("Prod AMI schema migrations applied.")

    with prod.begin() as conn:
        # Replace UT AMI list for this account.
        conn.execute(
            text(
                """
                DELETE FROM active_mine_intel.candidate_matches
                WHERE account_id = :aid
                  AND mine_site_id IN (
                    SELECT mine_site_id FROM active_mine_intel.candidate_sites
                    WHERE account_id = :aid AND state_abbr = :st
                  )
                """
            ),
            {"aid": ACCOUNT_ID, "st": STATE},
        )
        conn.execute(
            text(
                """
                DELETE FROM active_mine_intel.candidate_sites
                WHERE account_id = :aid AND state_abbr = :st
                """
            ),
            {"aid": ACCOUNT_ID, "st": STATE},
        )
        conn.execute(
            text(
                """
                DELETE FROM active_mine_intel.fetch_jobs
                WHERE account_id = :aid AND state_abbr = :st
                """
            ),
            {"aid": ACCOUNT_ID, "st": STATE},
        )

        # Upsert linked Targets (preserve other prod targets).
        # Production may already have the same PLSS under a different id
        # (unique on account_id + plss_normalized). Remap site FKs when needed.
        area_id_map: dict[int, int] = {}
        for a in areas:
            payload = dict(a)
            local_id = int(payload["id"])
            # JSONB columns: keep dict/list; TEXT[] columns: keep Python lists.
            # Do not json.dumps array-typed columns (minerals/report_links).
            if isinstance(payload.get("characteristics"), str):
                try:
                    payload["characteristics"] = json.loads(payload["characteristics"])
                except Exception:
                    pass
            if "characteristics" in payload:
                payload["characteristics"] = _as_jsonb(payload["characteristics"])

            plss = payload.get("plss_normalized")
            existing_id = None
            if plss:
                existing_id = conn.execute(
                    text(
                        """
                        SELECT id FROM areas_of_focus
                        WHERE account_id = :aid AND plss_normalized = :plss
                        LIMIT 1
                        """
                    ),
                    {"aid": ACCOUNT_ID, "plss": plss},
                ).scalar()
            if existing_id is None:
                existing_id = conn.execute(
                    text("SELECT id FROM areas_of_focus WHERE id = :id LIMIT 1"),
                    {"id": local_id},
                ).scalar()

            target_id = int(existing_id) if existing_id is not None else local_id
            area_id_map[local_id] = target_id
            payload["id"] = target_id

            cols = list(payload.keys())
            insert_cols = ", ".join(f'"{c}"' if c == "range" else c for c in cols)
            params = {f"p_{i}": payload[c] for i, c in enumerate(cols)}
            placeholders = ", ".join(f":p_{i}" for i in range(len(cols)))
            updates = ", ".join(
                f'"{c}" = EXCLUDED."{c}"' if c == "range" else f"{c} = EXCLUDED.{c}"
                for c in cols
                if c != "id"
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO areas_of_focus ({insert_cols})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO UPDATE SET {updates}
                    """
                ),
                params,
            )

        remapped = sum(1 for a, b in area_id_map.items() if a != b)
        if remapped:
            print(f"Remapped {remapped} Target id(s) to existing prod PLSS rows.")
        for s in sites:
            lid = s.get("area_of_focus_id")
            if lid is None:
                continue
            s["area_of_focus_id"] = area_id_map.get(int(lid), int(lid))
        for job in fetch_jobs:
            tids = job.get("target_ids")
            if tids is None:
                continue
            if isinstance(tids, str):
                try:
                    tids = json.loads(tids)
                except Exception:
                    continue
            job["target_ids"] = [area_id_map.get(int(x), int(x)) for x in (tids or [])]

        for r in runs:
            payload = dict(r)
            for k in ("progress_json", "qc_json", "manifest_json"):
                if k in payload:
                    if isinstance(payload[k], str):
                        try:
                            payload[k] = json.loads(payload[k])
                        except Exception:
                            pass
                    payload[k] = _as_jsonb(payload[k])
            cols = list(payload.keys())
            insert_cols = ", ".join(cols)
            params = {f"p_{i}": payload[c] for i, c in enumerate(cols)}
            placeholders = ", ".join(f":p_{i}" for i in range(len(cols)))
            update_cols = [
                c
                for c in cols
                if c
                not in {
                    "id",
                    "account_id",
                    "created_at",
                }
            ]
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            conn.execute(
                text(
                    f"""
                    INSERT INTO active_mine_intel.runs ({insert_cols})
                    VALUES ({placeholders})
                    ON CONFLICT (id) DO UPDATE SET {updates}
                    """
                ),
                params,
            )

        def _insert_table(table: str, rows: list[dict[str, Any]], json_keys: tuple[str, ...] = ()) -> None:
            for row in rows:
                payload = dict(row)
                for k in json_keys:
                    val = payload.get(k)
                    if isinstance(val, str):
                        try:
                            payload[k] = json.loads(val)
                        except Exception:
                            pass
                    payload[k] = _as_jsonb(payload.get(k))
                if "target_ids" in payload and payload["target_ids"] is not None:
                    tids = payload["target_ids"]
                    if isinstance(tids, str):
                        tids = json.loads(tids)
                    payload["target_ids"] = [int(x) for x in (tids or [])]
                cols = list(payload.keys())
                insert_cols = ", ".join(f'"{c}"' if c == "range" else c for c in cols)
                params = {f"p_{i}": payload[c] for i, c in enumerate(cols)}
                placeholders = ", ".join(f":p_{i}" for i in range(len(cols)))
                conn.execute(
                    text(f"INSERT INTO {table} ({insert_cols}) VALUES ({placeholders})"),
                    params,
                )

        _insert_table(
            "active_mine_intel.candidate_sites",
            sites,
            json_keys=("score_breakdown_json", "evidence_summary_json"),
        )
        _insert_table(
            "active_mine_intel.candidate_matches",
            matches,
            json_keys=("score_breakdown_json", "evidence_summary_json"),
        )
        _insert_table(
            "active_mine_intel.fetch_jobs",
            fetch_jobs,
            json_keys=("results_json", "progress_json"),
        )

    with prod.connect() as conn:
        n = conn.execute(
            text(
                """
                SELECT count(*) FROM active_mine_intel.candidate_sites
                WHERE account_id = :aid AND state_abbr = :st
                """
            ),
            {"aid": ACCOUNT_ID, "st": STATE},
        ).scalar()
    print(f"Prod now has {n} {STATE} candidate_sites for account {ACCOUNT_ID}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
