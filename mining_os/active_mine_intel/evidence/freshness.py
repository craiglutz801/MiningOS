"""Fail-closed freshness and source-outcome helpers.

A source failure must never look like a valid zero-result pull. Stale cache
may still feed the matcher, but it cannot support positive operational
assertions.
"""

from __future__ import annotations

from typing import Any

from mining_os.active_mine_intel.matcher.models import SourceStatus

# Cache older than TTL is stale for assertions. Matcher may still consume it.
DEFAULT_STALE_TTL_HOURS = 72.0
PRODUCTION_RECENCY_YEARS = 2


def source_outcome(
    *,
    fetched_ok: bool,
    record_count: int,
    cache_used: bool = False,
    stale: bool = False,
    message: str | None = None,
    failure_class: str | None = None,
) -> dict[str, Any]:
    """Distinguish success-empty from failure-empty.

    Returns a dict suitable for QC / SourceStatus extras:
      outcome: ok | empty | failed | stale
      usable_for_assertions: bool
    """
    if not fetched_ok:
        return {
            "outcome": "failed",
            "usable_for_assertions": False,
            "failure_class": failure_class or "unavailable",
            "record_count": int(record_count or 0),
            "message": message or "Source unavailable",
        }
    if stale:
        return {
            "outcome": "stale",
            "usable_for_assertions": False,
            "failure_class": "stale",
            "record_count": int(record_count or 0),
            "message": message or "Cached source exceeded freshness TTL",
        }
    if int(record_count or 0) == 0:
        return {
            "outcome": "empty",
            "usable_for_assertions": True,
            "failure_class": None,
            "record_count": 0,
            "message": message or "Valid zero-result response",
        }
    return {
        "outcome": "ok",
        "usable_for_assertions": True,
        "failure_class": None,
        "record_count": int(record_count),
        "message": message,
        "cache_used": cache_used,
    }


def apply_outcome(status: SourceStatus, outcome: dict[str, Any]) -> SourceStatus:
    """Copy outcome fields onto SourceStatus (status string stays matcher-compatible)."""
    status.record_count = int(outcome.get("record_count") or status.record_count or 0)
    if outcome.get("message"):
        status.message = outcome["message"]
    kind = outcome.get("outcome")
    if kind == "failed":
        status.status = "failed"
    elif kind == "stale":
        status.status = "stale"
    elif kind == "empty":
        status.status = "empty"
    elif status.status == "pending":
        status.status = "cached" if outcome.get("cache_used") else "success"
    return status


def freshness_label(
    *,
    stale: bool = False,
    cache_age_hours: float | None = None,
    ttl_hours: float = DEFAULT_STALE_TTL_HOURS,
    retrieved_at: str | None = None,
) -> str:
    if stale:
        return "stale"
    if cache_age_hours is not None and cache_age_hours > ttl_hours:
        return "stale"
    if retrieved_at or cache_age_hours is not None:
        return "current"
    return "unknown"


def production_year_is_current(
    latest_year: int | None,
    *,
    current_year: int,
    max_age_years: int = PRODUCTION_RECENCY_YEARS,
) -> bool:
    if latest_year is None:
        return False
    try:
        year = int(latest_year)
    except (TypeError, ValueError):
        return False
    return 0 <= current_year - year <= max_age_years


def source_usable(status: SourceStatus | dict[str, Any] | None) -> bool:
    if status is None:
        return False
    if isinstance(status, SourceStatus):
        payload = status.to_dict()
        extra_status = status.status
    else:
        payload = status
        extra_status = str(status.get("status") or "")
    if payload.get("usable_for_assertions") is False:
        return False
    if extra_status in {"failed", "stale", "unavailable", "degraded"}:
        return False
    if payload.get("outcome") in {"failed", "stale"}:
        return False
    return extra_status in {"success", "cached", "empty", "ok"} or payload.get("outcome") in {
        "ok",
        "empty",
    }
