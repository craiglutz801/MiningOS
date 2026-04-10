"""Name / state / county normalization and raw → standard record conversion."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from target_pipeline.models import RawSourceRow, StandardRecord
from target_pipeline.processors.commodities import canonical_commodity
from target_pipeline.processors.plss import normalize_plss_key, parse_plss_components


def normalize_name(name: Optional[str]) -> str:
    if not name or not isinstance(name, str):
        return ""
    s = re.sub(r"\s+", " ", name.strip())
    return s[:500]


def normalize_state_abbr(state: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    if not state or not isinstance(state, str):
        return fallback
    s = state.strip().upper()
    if len(s) == 2 and s.isalpha():
        return s
    # common full names → abbr (minimal set)
    full = {
        "UTAH": "UT",
        "NEVADA": "NV",
        "IDAHO": "ID",
        "MONTANA": "MT",
        "WYOMING": "WY",
        "COLORADO": "CO",
        "ARIZONA": "AZ",
        "NEW MEXICO": "NM",
        "CALIFORNIA": "CA",
        "WASHINGTON": "WA",
        "OREGON": "OR",
        "ALASKA": "AK",
    }
    return full.get(s) or (s[:2] if len(s) == 2 else fallback)


def normalize_county(county: Optional[str]) -> Optional[str]:
    if not county or not isinstance(county, str):
        return None
    s = re.sub(r"\s+", " ", county.strip())
    return s[:200] if s else None


def _coerce_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _record_type_from_raw(raw: RawSourceRow) -> str:
    rt = raw.get("record_type")
    if rt in ("claim", "deposit"):
        return rt
    src = (raw.get("source") or "").lower()
    if src == "mlrs":
        return "claim"
    return "deposit"


def standardize_raw_row(raw: RawSourceRow, default_state: str = "UT") -> StandardRecord:
    """Convert a loader row into a StandardRecord."""
    review_flags: list[str] = []
    st = normalize_state_abbr(raw.get("state"), fallback=default_state)
    plss_raw = (raw.get("plss_raw") or "").strip() or None
    commodity = canonical_commodity(raw.get("commodity_raw"))
    if not commodity:
        review_flags.append("missing_commodity")

    plss = plss_raw
    plss_norm = normalize_plss_key(plss, default_state=st or default_state) if plss else None
    if plss and not plss_norm:
        review_flags.append("unresolved_plss")

    lat = _coerce_float(raw.get("latitude"))
    lon = _coerce_float(raw.get("longitude"))

    reports = list(raw.get("reports") or [])
    if not isinstance(reports, list):
        reports = []

    rec: StandardRecord = {
        "source": raw.get("source") or "unknown",
        "record_type": _record_type_from_raw(raw),  # type: ignore[assignment]
        "raw_name": raw.get("name") or "",
        "normalized_name": normalize_name(raw.get("name")),
        "state": st,
        "county": normalize_county(raw.get("county")),
        "commodity": commodity,
        "plss": plss,
        "plss_normalized": plss_norm,
        "latitude": lat,
        "longitude": lon,
        "reports": reports,
        "status": (raw.get("status") or "").strip() or None,
        "review_flags": review_flags,
        "raw": dict(raw.get("raw") or {}),
    }
    return rec


def apply_spatial_plss_if_needed(
    record: StandardRecord,
    spatial_lookup: Any,
    default_state: str = "UT",
) -> StandardRecord:
    """If spatial_lookup is provided and PLSS missing, try to fill PLSS (see matchers.spatial)."""
    if record.get("plss") and record.get("plss_normalized"):
        return record
    lat, lon = record.get("latitude"), record.get("longitude")
    if spatial_lookup is None or lat is None or lon is None:
        return record
    from target_pipeline.matchers.spatial import lookup_plss_from_point

    resolved = lookup_plss_from_point(float(lat), float(lon), spatial_lookup)
    if not resolved:
        flags = list(record.get("review_flags") or [])
        flags.append("spatial_plss_unresolved")
        record = {**record, "review_flags": flags}
        return record
    st = record.get("state") or default_state
    record = {
        **record,
        "plss": resolved,
        "plss_normalized": normalize_plss_key(resolved, default_state=st),
    }
    return record


def plss_components_for_db(record: StandardRecord, default_state: str = "UT") -> Optional[Dict[str, Any]]:
    plss = record.get("plss")
    if not plss:
        return None
    st = record.get("state") or default_state
    return parse_plss_components(plss, default_state=st or default_state)
