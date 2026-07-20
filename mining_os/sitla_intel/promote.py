"""Promote a SITLA opportunity into an areas_of_focus Target (never auto-flood)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.sitla_intel.promote")


def promote_to_target(
    account_id: int,
    opportunity_id: str,
    *,
    user_id: int | None = None,
    link_type: str = "PROMOTED_TO_TARGET",
) -> dict[str, Any]:
    eng = get_engine()
    with eng.connect() as conn:
        opp = conn.execute(
            text(
                """
                SELECT * FROM sitla_intel.opportunities
                WHERE id = CAST(:id AS uuid) AND account_id = :aid
                """
            ),
            {"id": opportunity_id, "aid": account_id},
        ).mappings().first()
        if not opp:
            return {"ok": False, "error": "Opportunity not found"}
        existing = conn.execute(
            text(
                """
                SELECT id, area_of_focus_id, link_type
                FROM sitla_intel.opportunity_target_links
                WHERE opportunity_id = CAST(:oid AS uuid) AND link_type = 'PROMOTED_TO_TARGET'
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"oid": opportunity_id},
        ).mappings().first()

    if existing:
        return {
            "ok": True,
            "already_linked": True,
            "area_of_focus_id": existing["area_of_focus_id"],
            "link_id": str(existing["id"]),
            "link_type": existing["link_type"],
        }

    name = (opp.get("best_title") or opp.get("reference_number") or "SITLA opportunity").strip()
    plss = opp.get("plss_key")
    lat, lon = opp.get("latitude"), opp.get("longitude")
    if not plss and (lat is None or lon is None):
        return {"ok": False, "error": "Cannot promote without PLSS or coordinates."}

    minerals = list(opp.get("commodities") or [])
    notes = (
        f"Promoted from SITLA {opportunity_id} | {opp.get('reference_number')} | "
        f"{opp.get('county_name')} | {opp.get('lifecycle_status')} | "
        f"Official: {opp.get('official_detail_url') or 'n/a'}"
    )

    from mining_os.services.areas_of_focus import upsert_area

    try:
        area_id = upsert_area(
            name=name,
            location_plss=plss,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            minerals=minerals or None,
            status="Monitoring",
            priority="Medium",
            source="sitla",
            external_id=str(opportunity_id),
            state_abbr="UT",
            township=opp.get("township"),
            range_val=opp.get("range"),
            section=str(opp.get("section_summary") or "").split(",")[0] or None,
            meridian=opp.get("meridian"),
            retrieval_type="User Added",
            tag="sitla",
            validity_notes=notes,
            account_id=account_id,
            is_uploaded=True,
        )
    except Exception as e:
        log.exception("sitla promote failed")
        return {"ok": False, "error": f"Target create failed: {e}"}

    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO sitla_intel.opportunity_target_links (
                  opportunity_id, area_of_focus_id, link_type, confidence, created_by
                ) VALUES (CAST(:oid AS uuid), :aid, :lt, 0.7, :uid)
                RETURNING id
                """
            ),
            {"oid": opportunity_id, "aid": area_id, "lt": link_type, "uid": user_id},
        ).first()
        conn.execute(
            text(
                """
                INSERT INTO sitla_intel.opportunity_events (
                  opportunity_id, event_type, event_at, title, description
                ) VALUES (
                  CAST(:oid AS uuid), 'PROMOTED_TO_TARGET', now(),
                  'Promoted to Target', :descr
                )
                """
            ),
            {"oid": opportunity_id, "descr": f"areas_of_focus.id={area_id}"},
        )
    return {
        "ok": True,
        "already_linked": False,
        "area_of_focus_id": area_id,
        "link_id": str(row[0]) if row else None,
        "link_type": link_type,
    }
