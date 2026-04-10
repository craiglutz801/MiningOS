"""Sequential batch runs of per-target BLM actions (claim scrape, LR2000-style report)."""

from __future__ import annotations

import logging
from typing import Any, List

log = logging.getLogger("mining_os.area_batch_actions")

# Keep HTTP requests bounded; each target can take minutes (MLRS script).
MAX_BATCH_AREA_ACTIONS = 25


def _normalize_ids(raw: List[Any]) -> List[int]:
    seen: set[int] = set()
    out: list[int] = []
    for x in raw:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def batch_fetch_claim_records(ids: List[Any]) -> dict[str, Any]:
    """
    Run fetch_claim_records_for_area for each id in order.
    Returns { ok, error?, processed, succeeded, failed, results: [{ id, name, ok, error?, claims_count }] }.
    """
    from mining_os.services.areas_of_focus import get_area
    from mining_os.services.fetch_claim_records import fetch_claim_records_for_area

    clean = _normalize_ids(ids)
    if not clean:
        return {"ok": False, "error": "Provide a non-empty ids array of integers.", "results": []}
    if len(clean) > MAX_BATCH_AREA_ACTIONS:
        return {
            "ok": False,
            "error": f"At most {MAX_BATCH_AREA_ACTIONS} targets per batch request.",
            "results": [],
        }

    results: list[dict[str, Any]] = []
    for aid in clean:
        area = get_area(aid)
        if not area:
            results.append({"id": aid, "name": None, "ok": False, "error": "Target not found", "claims_count": 0})
            continue
        name = (area.get("name") or "").strip() or f"#{aid}"
        try:
            out = fetch_claim_records_for_area(
                aid,
                name,
                area.get("location_plss"),
                state_abbr=area.get("state_abbr"),
                meridian=area.get("meridian"),
                township=area.get("township"),
                range_val=area.get("range"),
                section=area.get("section"),
                latitude=area.get("latitude"),
                longitude=area.get("longitude"),
            )
        except Exception as e:
            log.exception("batch_fetch_claim_records id=%s", aid)
            results.append({"id": aid, "name": name, "ok": False, "error": str(e), "claims_count": 0})
            continue
        claims = out.get("claims") or []
        results.append({
            "id": aid,
            "name": name,
            "ok": bool(out.get("ok")),
            "error": out.get("error"),
            "claims_count": len(claims) if isinstance(claims, list) else 0,
        })

    succeeded = sum(1 for r in results if r.get("ok"))
    return {
        "ok": True,
        "processed": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


def batch_lr2000_geographic_report(ids: List[Any]) -> dict[str, Any]:
    """
    Run run_lr2000_geographic_index_for_area for each id in order.
    Returns { ok, error?, processed, succeeded, failed, results: [{ id, name, ok, error?, claims_count }] }.
    """
    from mining_os.services.areas_of_focus import get_area
    from mining_os.services.mlrs_geographic_index import run_lr2000_geographic_index_for_area

    clean = _normalize_ids(ids)
    if not clean:
        return {"ok": False, "error": "Provide a non-empty ids array of integers.", "results": []}
    if len(clean) > MAX_BATCH_AREA_ACTIONS:
        return {
            "ok": False,
            "error": f"At most {MAX_BATCH_AREA_ACTIONS} targets per batch request.",
            "results": [],
        }

    results: list[dict[str, Any]] = []
    for aid in clean:
        area = get_area(aid)
        if not area:
            results.append({"id": aid, "name": None, "ok": False, "error": "Target not found", "claims_count": 0})
            continue
        name = (area.get("name") or "").strip() or f"#{aid}"
        try:
            out = run_lr2000_geographic_index_for_area(aid, area)
        except Exception as e:
            log.exception("batch_lr2000_geographic_report id=%s", aid)
            results.append({"id": aid, "name": name, "ok": False, "error": str(e), "claims_count": 0})
            continue
        ok = bool(out.get("ok"))
        claims = out.get("claims") or []
        results.append({
            "id": aid,
            "name": name,
            "ok": ok,
            "error": out.get("error"),
            "claims_count": len(claims) if isinstance(claims, list) else 0,
        })

    succeeded = sum(1 for r in results if r.get("ok"))
    return {
        "ok": True,
        "processed": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
