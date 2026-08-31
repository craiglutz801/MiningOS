"""Tenure classification from BLM MLRS polygons.

Not Closed / active unpatented polygons are tenure evidence only. They never
set operational status. Geometry is PLSS-derived and approximate — not a
surveyed boundary.
"""

from __future__ import annotations

from typing import Any

GEOMETRY_LIMITATIONS = (
    "BLM MLRS claim polygons are generated from legal land descriptions / PLSS "
    "and are approximate, not surveyed boundaries.",
    "Intersection with a Not Closed polygon is tenure evidence only and is not "
    "proof of production, permitting, or current extraction.",
    "Patented, conveyed, or excluded claims are retained only for mixed-tenure "
    "detection; they are not analytical unpatented matches.",
)


def classify_tenure(
    *,
    unpatented_intersects: bool,
    patented_intersects: bool,
    conveyed_intersects: bool = False,
    claim_count: int = 0,
    geometry_quality_groups: list[str] | None = None,
) -> dict[str, Any]:
    mixed = bool(unpatented_intersects) and bool(patented_intersects or conveyed_intersects)
    if mixed:
        tenure = "Mixed"
    elif unpatented_intersects:
        tenure = "Unpatented"
    elif patented_intersects:
        tenure = "Patented"
    else:
        tenure = "Unknown"

    groups = [g for g in (geometry_quality_groups or []) if g]
    coarse = any(g in {"COARSE", "UNKNOWN"} for g in groups)
    return {
        "tenure_class": tenure,
        "unpatented_intersects": bool(unpatented_intersects),
        "patented_intersects": bool(patented_intersects),
        "conveyed_intersects": bool(conveyed_intersects),
        "mixed_tenure": mixed,
        "claim_count": int(claim_count or 0),
        "geometry_limitations": list(GEOMETRY_LIMITATIONS),
        "geometry_approximate": True,
        "geometry_surveyed": False,
        "geometry_quality_groups": groups,
        "geometry_coarse_or_unknown": coarse or not groups,
        "tenure_source": "blm_claims",
        "tenure_notes": "MLRS Not Closed polygons used as tenure evidence only.",
    }


def tenure_from_claim_rows(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Classify from matcher claim-match rows plus optional patented overlay flags."""
    rows = rows or []
    unpatented = False
    patented = False
    conveyed = False
    qualities: list[str] = []
    for row in rows:
        patented_flag = bool(row.get("patented_flag") or row.get("patented_intersects"))
        conveyed_flag = bool(row.get("conveyed_flag") or row.get("conveyed_intersects"))
        if row.get("tenure_overlay") == "patented" or patented_flag:
            patented = True
        elif conveyed_flag or row.get("tenure_overlay") == "conveyed":
            conveyed = True
        else:
            unpatented = True
        q = row.get("geometry_quality_group")
        if q:
            qualities.append(str(q))
    # Site-level flags (set by pipeline overlay even when patented rows were filtered).
    if any(row.get("site_patented_intersects") for row in rows):
        patented = True
    if any(row.get("site_conveyed_intersects") for row in rows):
        conveyed = True
    return classify_tenure(
        unpatented_intersects=unpatented or bool(rows),
        patented_intersects=patented,
        conveyed_intersects=conveyed,
        claim_count=len(rows),
        geometry_quality_groups=qualities,
    )
