"""
In-app equivalent of BLM MLRS "Mining Claims — Geographic Index" (report 104):
query the same national MLRS FeatureServer by PLSS / map extent as reports.blm.gov,
without opening a browser or automating their SPA.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("mining_os.mlrs_geographic_index")


def _strip_geom(claims: list[dict[str, Any]]) -> None:
    for c in claims:
        c.pop("geometry", None)


def run_lr2000_geographic_index_for_area(area_id: int, area: dict[str, Any]) -> dict[str, Any]:
    """
    Use target PLSS (+ spatial fallback) to query BLM MLRS mining claims.
    Persists snapshot under characteristics.lr2000_geographic_index.

    Defensive: wraps the entire flow so callers always get a structured
    response (`ok`, `error`, `claims`, ...) instead of an unhandled 500.
    """
    try:
        return _run_lr2000_geographic_index_for_area(area_id, area)
    except Exception as e:
        log.exception("run_lr2000_geographic_index_for_area failed for area_id=%s: %s", area_id, e)
        return {
            "ok": False,
            "error": f"LR2000 report failed: {e}",
            "claims": [],
            "query_method": None,
            "fetched_at": None,
            "log": "",
            "input": {},
            "source": None,
        }


def _run_lr2000_geographic_index_for_area(area_id: int, area: dict[str, Any]) -> dict[str, Any]:
    from mining_os.services.areas_of_focus import merge_area_characteristics
    from mining_os.services.blm_plss import query_claims_by_coords, query_claims_by_plss
    from mining_os.services.fetch_claim_records import (
        DEFAULT_MERIDIAN,
        STATE_MERIDIAN,
        _parse_plss_for_script,
    )

    if not area or not isinstance(area, dict):
        return {
            "ok": False,
            "error": "Area not found or invalid.",
            "claims": [],
            "query_method": None,
            "fetched_at": None,
            "log": "",
            "input": {},
            "source": None,
        }

    location_plss = area.get("location_plss")
    state_abbr = area.get("state_abbr")
    meridian = area.get("meridian")
    township = area.get("township")
    range_val = area.get("range")
    section = area.get("section")
    latitude = area.get("latitude")
    longitude = area.get("longitude")

    if state_abbr and township and range_val:
        plss_row = {
            "Township": township,
            "Range": range_val,
            "Section": section or "",
            "State": state_abbr,
            "Meridian": meridian or STATE_MERIDIAN.get(state_abbr, DEFAULT_MERIDIAN),
        }
    else:
        plss_row = _parse_plss_for_script(location_plss)

    if not plss_row:
        if not location_plss or not str(location_plss).strip():
            err = "Add state, township, range (and ideally section) on this target, or a parseable Location (PLSS)."
        else:
            err = f"Could not parse PLSS from “{location_plss}”. Fix Location (PLSS) or enter township/range in the target fields."
        return {
            "ok": False,
            "error": err,
            "claims": [],
            "query_method": None,
            "fetched_at": None,
            "input": {},
        }

    log_parts: list[str] = []
    claims: list[dict[str, Any]] = []
    query_method = "mlrs_plss_section"
    had_section = bool(plss_row["Section"])

    if had_section:
        claims = query_claims_by_plss(
            state=plss_row["State"],
            township=plss_row["Township"],
            range_val=plss_row["Range"],
            section=plss_row["Section"],
            meridian=plss_row["Meridian"],
        )
        _strip_geom(claims)
        log_parts.append(f"PLSS with section: {len(claims)} claim(s)")

    if not claims:
        claims = query_claims_by_plss(
            state=plss_row["State"],
            township=plss_row["Township"],
            range_val=plss_row["Range"],
            section=None,
            meridian=plss_row["Meridian"],
        )
        _strip_geom(claims)
        log_parts.append(f"Township/range (no section filter): {len(claims)} claim(s)")
        query_method = "mlrs_plss_broadened" if had_section else "mlrs_plss_range"

    if not claims and latitude is not None and longitude is not None:
        try:
            la, lo = float(latitude), float(longitude)
        except (TypeError, ValueError):
            pass
        else:
            claims = query_claims_by_coords(la, lo, radius_meters=2000)
            _strip_geom(claims)
            log_parts.append(f"Spatial 2 km around ({la:.5f}, {lo:.5f}): {len(claims)} claim(s)")
            query_method = "mlrs_spatial_2km"

    fetched_at = datetime.now(timezone.utc).isoformat()
    input_summary = {
        "state": plss_row["State"],
        "meridian": plss_row["Meridian"],
        "township": plss_row["Township"],
        "range": plss_row["Range"],
        "section": plss_row["Section"] or None,
        "location_plss": location_plss,
        "latitude": latitude,
        "longitude": longitude,
    }

    payload = {
        "ok": True,
        "fetched_at": fetched_at,
        "claims": claims,
        "query_method": query_method,
        "log": "\n".join(log_parts),
        "source": "BLM MLRS FeatureServer (same national layer as Geographic Index report)",
        "input": input_summary,
    }

    try:
        merge_area_characteristics(area_id, {"lr2000_geographic_index": payload})
    except Exception as merge_err:
        log.warning("LR2000: failed to persist characteristics for area %s: %s", area_id, merge_err)
        log_parts.append(f"warn: could not persist snapshot: {merge_err}")
        payload["log"] = "\n".join(log_parts)

    return {
        "ok": True,
        "error": None,
        "claims": claims,
        "query_method": query_method,
        "fetched_at": fetched_at,
        "log": payload["log"],
        "input": input_summary,
        "source": payload["source"],
    }
