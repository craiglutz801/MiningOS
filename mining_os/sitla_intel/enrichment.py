"""Geometry + MLRS enrichment for SITLA opportunities."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.sitla_intel.enrichment")


def enrich_account_opportunities(account_id: int, limit: int = 40) -> dict[str, Any]:
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, latitude, longitude, township, range, section_summary, plss_key
                FROM sitla_intel.opportunities
                WHERE account_id = :aid AND is_active = true
                  AND COALESCE(enrichment_status, 'pending') IN ('pending', 'partial')
                ORDER BY overall_priority_score DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"aid": account_id, "lim": limit},
        ).mappings().all()

    geom_n = mlrs_n = 0
    for row in rows:
        oid = str(row["id"])
        if row.get("latitude") is not None and row.get("longitude") is not None:
            _insert_geom(eng, oid, float(row["latitude"]), float(row["longitude"]))
            geom_n += 1
        try:
            if _enrich_mlrs(eng, dict(row)):
                mlrs_n += 1
        except Exception:
            log.exception("sitla mlrs enrich failed for %s", oid)
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE sitla_intel.opportunities
                    SET enrichment_status = 'enriched', last_enriched_at = :ts, updated_at = :ts
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"id": oid, "ts": datetime.now(timezone.utc)},
            )
    return {"ok": True, "considered": len(rows), "geometry_updates": geom_n, "mlrs_updates": mlrs_n}


def _insert_geom(eng, opportunity_id: str, lat: float, lon: float) -> None:
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE sitla_intel.geometry_versions
                SET is_current = false
                WHERE opportunity_id = CAST(:oid AS uuid) AND is_current = true
                """
            ),
            {"oid": opportunity_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO sitla_intel.geometry_versions (
                  opportunity_id, centroid_lat, centroid_lon, accuracy, is_current
                ) VALUES (CAST(:oid AS uuid), :lat, :lon, 'COORDINATE', true)
                """
            ),
            {"oid": opportunity_id, "lat": lat, "lon": lon},
        )
        conn.execute(
            text(
                """
                UPDATE sitla_intel.opportunities
                SET geometry_accuracy = CASE
                      WHEN geometry_accuracy = 'SURVEY' THEN geometry_accuracy ELSE 'COORDINATE'
                    END
                WHERE id = CAST(:oid AS uuid)
                """
            ),
            {"oid": opportunity_id},
        )


def _enrich_mlrs(eng, opp: dict[str, Any]) -> bool:
    claims: list[dict[str, Any]] = []
    twp, rng = opp.get("township"), opp.get("range")
    sec = str(opp.get("section_summary") or "").split(",")[0].strip() or None
    lat, lon = opp.get("latitude"), opp.get("longitude")
    try:
        if twp and rng:
            from mining_os.services.blm_plss import query_claims_by_plss_with_status

            ok, claims = query_claims_by_plss_with_status(
                state="UT", township=str(twp), range_val=str(rng), section=sec
            )
            if not ok:
                claims = []
        elif lat is not None and lon is not None:
            from mining_os.services.blm_plss import query_claims_by_coords

            claims = query_claims_by_coords(float(lat), float(lon), radius_meters=2500)[:25]
    except Exception:
        log.exception("MLRS query failed")
        return False
    if not claims:
        return False
    oid = str(opp["id"])
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM sitla_intel.claim_context
                WHERE opportunity_id = CAST(:oid AS uuid)
                  AND COALESCE(raw_payload_json->>'enrichment', '') = 'mlrs_auto'
                """
            ),
            {"oid": oid},
        )
        for c in claims[:25]:
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.claim_context (
                      opportunity_id, mlrs_serial_number, claim_name, claim_status,
                      claimant_name, distance_meters, raw_payload_json
                    ) VALUES (
                      CAST(:oid AS uuid), :serial, :name, :status, :claimant, :dist, CAST(:raw AS jsonb)
                    )
                    """
                ),
                {
                    "oid": oid,
                    "serial": c.get("serial_number"),
                    "name": c.get("claim_name"),
                    "status": c.get("status") or c.get("case_status"),
                    "claimant": c.get("claimant_name"),
                    "dist": c.get("distance_meters"),
                    "raw": json.dumps({**c, "enrichment": "mlrs_auto"}, default=str),
                },
            )
        conn.execute(
            text(
                """
                UPDATE sitla_intel.opportunities
                SET mineral_potential_score = LEAST(100, mineral_potential_score + 5),
                    overall_priority_score = LEAST(100, overall_priority_score + 2),
                    updated_at = :ts
                WHERE id = CAST(:oid AS uuid)
                """
            ),
            {"oid": oid, "ts": datetime.now(timezone.utc)},
        )
    return True
