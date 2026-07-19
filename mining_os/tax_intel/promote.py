"""Promote a Tax Opportunity into an areas_of_focus Target (never auto-flood)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.tax_intel.promote")


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
                SELECT *
                FROM tax_intel.tax_opportunities
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
                FROM tax_intel.opportunity_target_links
                WHERE opportunity_id = CAST(:oid AS uuid)
                  AND link_type = 'PROMOTED_TO_TARGET'
                ORDER BY created_at DESC
                LIMIT 1
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

    name = (opp.get("best_name") or opp.get("primary_apn") or "Tax sale opportunity").strip()
    plss = opp.get("plss_key")
    lat = opp.get("latitude")
    lon = opp.get("longitude")
    if not plss and (lat is None or lon is None):
        return {
            "ok": False,
            "error": "Cannot promote without PLSS or coordinates. Resolve parcel geometry first.",
        }

    minerals = list(opp.get("commodities") or [])
    notes_bits = [
        f"Promoted from Tax Sales opportunity {opportunity_id}",
        f"County: {opp.get('county_name')} {opp.get('state')}",
        f"APN: {opp.get('primary_apn') or '—'}",
        f"Lifecycle: {opp.get('sale_lifecycle_status')}",
        f"Patent class: {opp.get('patent_classification')} (inferred until GLO-confirmed)",
    ]
    information = " | ".join(notes_bits)

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
            source="tax_sales",
            external_id=str(opportunity_id),
            state_abbr=opp.get("state"),
            township=opp.get("township"),
            range_val=opp.get("range"),
            section=opp.get("section"),
            meridian=opp.get("meridian"),
            retrieval_type="User Added",
            tag="tax-sales",
            validity_notes=information,
            account_id=account_id,
            is_uploaded=True,
        )
    except Exception as e:
        log.exception("promote upsert_area failed")
        return {"ok": False, "error": f"Target create failed: {e}"}

    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO tax_intel.opportunity_target_links (
                  opportunity_id, area_of_focus_id, link_type, confidence, created_by
                ) VALUES (
                  CAST(:oid AS uuid), :aid, :lt, :conf, :uid
                )
                RETURNING id
                """
            ),
            {
                "oid": opportunity_id,
                "aid": area_id,
                "lt": link_type,
                "conf": float(opp.get("patent_confidence") or 0),
                "uid": user_id,
            },
        ).first()
        conn.execute(
            text(
                """
                INSERT INTO tax_intel.tax_events (
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
