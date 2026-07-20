"""Scheduled SITLA jobs — gated by ENABLE_SITLA_JOBS."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.sitla_intel.config import sitla_enabled, sitla_jobs_enabled

log = logging.getLogger("mining_os.sitla_intel.jobs")

_lock = threading.Lock()
_last_run_at: datetime | None = None
MIN_INTERVAL_SEC = 3600


def tick_sitla_jobs() -> None:
    if not sitla_enabled() or not sitla_jobs_enabled():
        return
    global _last_run_at
    now = datetime.now(timezone.utc)
    with _lock:
        if _last_run_at and (now - _last_run_at).total_seconds() < MIN_INTERVAL_SEC:
            return
        _last_run_at = now
    try:
        run_scheduled_jobs()
    except Exception:
        log.exception("sitla scheduled jobs failed")


def run_scheduled_jobs(account_ids: list[int] | None = None) -> dict[str, Any]:
    from mining_os.sitla_intel.alerts import deliver_pending_alerts, detect_watchlist_changes
    from mining_os.sitla_intel.demo_seed import ensure_demo_seed
    from mining_os.sitla_intel.enrichment import enrich_account_opportunities
    from mining_os.sitla_intel.history import match_historical_offerings
    from mining_os.sitla_intel.ingest import run_all_enabled_sources

    if account_ids is None:
        account_ids = _accounts_for_jobs()
    results = []
    for aid in account_ids:
        try:
            ensure_demo_seed(aid)
            ingest = run_all_enabled_sources(aid, trigger_type="scheduled")
            enrich = enrich_account_opportunities(aid, limit=40)
            history = match_historical_offerings(aid)
            detect = detect_watchlist_changes(aid)
            results.append(
                {
                    "account_id": aid,
                    "ok": True,
                    "ingest": ingest,
                    "enrich": enrich,
                    "history": history,
                    "detect": detect,
                }
            )
        except Exception as e:
            log.exception("sitla jobs failed for account %s", aid)
            results.append({"account_id": aid, "ok": False, "error": str(e)})
    delivery = deliver_pending_alerts(limit=100)
    return {"ok": True, "accounts": len(account_ids), "results": results, "delivery": delivery}


def run_manual_refresh(account_id: int, source_key: str | None = None) -> dict[str, Any]:
    from mining_os.sitla_intel.demo_seed import ensure_demo_seed
    from mining_os.sitla_intel.enrichment import enrich_account_opportunities
    from mining_os.sitla_intel.history import match_historical_offerings
    from mining_os.sitla_intel.ingest import run_all_enabled_sources, run_source

    seed = ensure_demo_seed(account_id)
    if source_key:
        result = run_source(source_key, account_id=account_id, trigger_type="manual", enrich=True)
    else:
        result = run_all_enabled_sources(account_id, trigger_type="manual")
        enrich_account_opportunities(account_id, limit=80)
    history = match_historical_offerings(account_id)
    return {
        "ok": True,
        "error": None,
        "message": "SITLA sources refreshed (fixture by default; live HTML when allow_live_html).",
        "seed": seed,
        "ingest": result,
        "history": history,
    }


def _accounts_for_jobs() -> list[int]:
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT account_id
                FROM sitla_intel.opportunities
                WHERE account_id IS NOT NULL
                UNION
                SELECT id FROM accounts
                ORDER BY 1
                LIMIT 50
                """
            )
        ).fetchall()
    ids = [int(r[0]) for r in rows if r[0] is not None]
    return ids or [1]


def jobs_status() -> dict[str, Any]:
    return {
        "ok": True,
        "api_enabled": sitla_enabled(),
        "jobs_enabled": sitla_jobs_enabled(),
        "last_run_at": _last_run_at.isoformat() if _last_run_at else None,
        "min_interval_sec": MIN_INTERVAL_SEC,
    }
