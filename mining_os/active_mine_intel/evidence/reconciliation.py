"""Deterministic ID / name / operator / coordinate reconciliation.

Match method is always explicit. Coordinate proximity alone never merges
identity. This module is used for BMRR and other evidence overlays; it does
not replace the existing matcher state↔MSHA merge.
"""

from __future__ import annotations

from typing import Any

from mining_os.active_mine_intel.matcher.normalize import (
    normalize_entity_name,
    normalize_mine_name,
    similarity_score,
)

MATCH_ID_EXACT = "id_exact"
MATCH_NAME_OPERATOR_COORDS = "name_operator_coords"
MATCH_NAME_COORDS = "name_coords"
MATCH_UNMATCHED = "unmatched"

# Tight identity gates — fail closed rather than over-merge.
NAME_HIGH = 90.0
NAME_MID = 75.0
OPERATOR_HIGH = 85.0
COORDS_TIGHT_M = 250.0
COORDS_WIDE_M = 5000.0


def _clean_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return ""
    return text


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters (WGS84 spherical)."""
    from math import atan2, cos, radians, sin, sqrt

    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(max(0.0, 1.0 - a)))


def _distance_m(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    try:
        lat1 = float(left.get("latitude"))
        lon1 = float(left.get("longitude"))
        lat2 = float(right.get("latitude"))
        lon2 = float(right.get("longitude"))
    except (TypeError, ValueError):
        return None
    if any(v != v for v in (lat1, lon1, lat2, lon2)):  # NaN
        return None
    return haversine_m(lat1, lon1, lat2, lon2)


def match_records(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    id_fields: tuple[str, ...] = ("msha_mine_id", "msha_number", "state_mine_id", "permit_number"),
) -> dict[str, Any]:
    """Return a deterministic match decision between two mine-like records."""
    for field in id_fields:
        a = _clean_id(left.get(field))
        b = _clean_id(right.get(field))
        if a and b and a == b:
            return {
                "matched": True,
                "match_method": MATCH_ID_EXACT,
                "id_field": field,
                "id_value": a,
                "name_similarity": similarity_score(left.get("mine_name") or left.get("name"), right.get("mine_name") or right.get("name")),
                "operator_similarity": similarity_score(
                    left.get("operator_name"), right.get("operator_name")
                ),
                "distance_m": _distance_m(left, right),
            }

    name_sim = similarity_score(
        normalize_mine_name(left.get("mine_name") or left.get("name") or left.get("canonical_mine_name")),
        normalize_mine_name(right.get("mine_name") or right.get("name") or right.get("canonical_mine_name")),
    )
    op_sim = similarity_score(
        normalize_entity_name(left.get("operator_name")),
        normalize_entity_name(right.get("operator_name")),
    )
    dist = _distance_m(left, right)

    if dist is None:
        return {
            "matched": False,
            "match_method": MATCH_UNMATCHED,
            "reason": "missing_coordinates",
            "name_similarity": name_sim,
            "operator_similarity": op_sim,
            "distance_m": None,
        }

    if name_sim >= NAME_HIGH and dist <= COORDS_TIGHT_M:
        return {
            "matched": True,
            "match_method": MATCH_NAME_COORDS,
            "name_similarity": name_sim,
            "operator_similarity": op_sim,
            "distance_m": round(dist, 1),
        }

    if (
        name_sim >= NAME_MID
        and op_sim >= OPERATOR_HIGH
        and dist <= COORDS_WIDE_M
    ):
        return {
            "matched": True,
            "match_method": MATCH_NAME_OPERATOR_COORDS,
            "name_similarity": name_sim,
            "operator_similarity": op_sim,
            "distance_m": round(dist, 1),
        }

    return {
        "matched": False,
        "match_method": MATCH_UNMATCHED,
        "reason": "below_identity_gate",
        "name_similarity": name_sim,
        "operator_similarity": op_sim,
        "distance_m": round(dist, 1),
    }


def best_match(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    id_fields: tuple[str, ...] = ("msha_mine_id", "msha_number", "state_mine_id", "permit_number"),
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Pick the unique best match. Ties fail closed (unmatched)."""
    ranked: list[tuple[tuple, dict[str, Any], dict[str, Any]]] = []
    method_rank = {
        MATCH_ID_EXACT: 3,
        MATCH_NAME_COORDS: 2,
        MATCH_NAME_OPERATOR_COORDS: 1,
    }
    for cand in candidates:
        decision = match_records(target, cand, id_fields=id_fields)
        if not decision.get("matched"):
            continue
        dist = decision.get("distance_m")
        dist_key = 0.0 if dist is None else float(dist)
        rank = (
            method_rank.get(str(decision["match_method"]), 0),
            float(decision.get("name_similarity") or 0.0),
            -dist_key,
        )
        ranked.append((rank, cand, decision))
    if not ranked:
        return None, {"matched": False, "match_method": MATCH_UNMATCHED, "reason": "no_candidate"}
    ranked.sort(key=lambda row: row[0], reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None, {
            "matched": False,
            "match_method": MATCH_UNMATCHED,
            "reason": "ambiguous_tie",
            "tied": 2,
        }
    return ranked[0][1], ranked[0][2]
