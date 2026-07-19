"""Parcel GIS, patent/GLO review hooks, and MLRS claim-context enrichment."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.tax_intel.enrichment")


def enrich_account_opportunities(account_id: int, limit: int = 50) -> dict[str, Any]:
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, state, county_name, primary_apn, latitude, longitude,
                       township, range, section, meridian, plss_key, enrichment_status
                FROM tax_intel.tax_opportunities
                WHERE account_id = :aid
                  AND is_active = true
                  AND COALESCE(enrichment_status, 'pending') IN ('pending', 'partial')
                ORDER BY overall_priority_score DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"aid": account_id, "lim": limit},
        ).mappings().all()

    parcel_n = mlrs_n = 0
    errors: list[str] = []
    for row in rows:
        oid = str(row["id"])
        try:
            if _resolve_parcel(eng, dict(row)):
                parcel_n += 1
        except Exception as e:
            errors.append(f"parcel:{oid}:{e}")
            log.exception("parcel resolve failed for %s", oid)
        try:
            if _enrich_mlrs(eng, dict(row)):
                mlrs_n += 1
        except Exception as e:
            errors.append(f"mlrs:{oid}:{e}")
            log.exception("mlrs enrich failed for %s", oid)
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE tax_intel.tax_opportunities
                    SET enrichment_status = 'enriched',
                        last_enriched_at = :ts,
                        updated_at = :ts
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"id": oid, "ts": datetime.now(timezone.utc)},
            )
    return {
        "ok": True,
        "considered": len(rows),
        "parcel_updates": parcel_n,
        "mlrs_updates": mlrs_n,
        "errors": errors[:20],
    }


def _resolve_parcel(eng, opp: dict[str, Any]) -> bool:
    """
    Record a parcel geometry version from existing lat/lon (coordinate accuracy)
    or optional ArcGIS parcel layer in source configuration.
    """
    lat, lon = opp.get("latitude"), opp.get("longitude")
    apn = opp.get("primary_apn")
    if lat is None or lon is None:
        # Optional county parcel layer lookup by APN
        layer = _parcel_layer_for_county(eng, opp.get("state"), opp.get("county_name"))
        if layer and apn:
            hit = _query_parcel_by_apn(layer, apn)
            if hit:
                lat, lon = hit.get("lat"), hit.get("lon")
                with eng.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE tax_intel.tax_opportunities
                            SET latitude = COALESCE(latitude, :lat),
                                longitude = COALESCE(longitude, :lon),
                                acreage = COALESCE(acreage, :acre),
                                geometry_accuracy = 'PARCEL_GIS',
                                updated_at = :ts
                            WHERE id = CAST(:id AS uuid)
                            """
                        ),
                        {
                            "lat": lat,
                            "lon": lon,
                            "acre": hit.get("acreage"),
                            "ts": datetime.now(timezone.utc),
                            "id": str(opp["id"]),
                        },
                    )
                _insert_geom_version(
                    eng,
                    str(opp["id"]),
                    lat,
                    lon,
                    hit.get("acreage"),
                    "PARCEL_GIS",
                    hit.get("raw") or {},
                )
                return True
        return False

    accuracy = "COORDINATE"
    _insert_geom_version(eng, str(opp["id"]), float(lat), float(lon), None, accuracy, {"from": "opportunity"})
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE tax_intel.tax_opportunities
                SET geometry_accuracy = CASE
                      WHEN geometry_accuracy IN ('PARCEL_GIS', 'SURVEY') THEN geometry_accuracy
                      ELSE 'COORDINATE'
                    END,
                    updated_at = :ts
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": str(opp["id"]), "ts": datetime.now(timezone.utc)},
        )
    return True


def _insert_geom_version(
    eng,
    opportunity_id: str,
    lat: float | None,
    lon: float | None,
    acreage: float | None,
    accuracy: str,
    raw: dict[str, Any],
) -> None:
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE tax_intel.parcel_geometry_versions
                SET is_current = false
                WHERE opportunity_id = CAST(:oid AS uuid) AND is_current = true
                """
            ),
            {"oid": opportunity_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO tax_intel.parcel_geometry_versions (
                  opportunity_id, centroid_lat, centroid_lon, acreage, accuracy, raw_payload_json, is_current
                ) VALUES (
                  CAST(:oid AS uuid), :lat, :lon, :acre, :acc, CAST(:raw AS jsonb), true
                )
                """
            ),
            {
                "oid": opportunity_id,
                "lat": lat,
                "lon": lon,
                "acre": acreage,
                "acc": accuracy,
                "raw": json.dumps(raw, default=str),
            },
        )


def _parcel_layer_for_county(eng, state: str | None, county: str | None) -> str | None:
    if not state or not county:
        return None
    with eng.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT configuration_json
                FROM tax_intel.source_registry
                WHERE state = :st AND lower(county_name) = lower(:co)
                ORDER BY enabled DESC
                LIMIT 1
                """
            ),
            {"st": state.upper(), "co": county},
        ).mappings().first()
    if not row:
        return None
    cfg = row["configuration_json"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return (cfg or {}).get("parcel_layer_url")


def _query_parcel_by_apn(layer_url: str, apn: str) -> dict[str, Any] | None:
    import requests

    # Try a few common APN field names; soft-fail on errors.
    for field in ("APN", "PARCEL_ID", "PIN", "ACCOUNT"):
        where = f"UPPER({field}) = '{apn.upper().replace(chr(39), '')}'"
        try:
            r = requests.get(
                f"{layer_url.rstrip('/')}/query",
                params={
                    "where": where,
                    "outFields": "*",
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "f": "geojson",
                    "resultRecordCount": 1,
                },
                timeout=30,
                headers={"User-Agent": "MiningOS-TaxIntel/1.0"},
            )
            if r.status_code != 200:
                continue
            feats = (r.json() or {}).get("features") or []
            if not feats:
                continue
            feat = feats[0]
            geom = feat.get("geometry") or {}
            props = feat.get("properties") or {}
            lat = lon = None
            if geom.get("type") == "Point":
                lon, lat = geom["coordinates"][:2]
            elif geom.get("type") == "Polygon":
                ring = geom["coordinates"][0]
                lon = sum(p[0] for p in ring) / len(ring)
                lat = sum(p[1] for p in ring) / len(ring)
            acre = props.get("ACRES") or props.get("acreage") or props.get("SHAPE_Area")
            return {"lat": lat, "lon": lon, "acreage": float(acre) if acre else None, "raw": props}
        except Exception:
            continue
    return None


def _enrich_mlrs(eng, opp: dict[str, Any]) -> bool:
    """Attach nearby MLRS claim context using existing BLM helpers."""
    claims: list[dict[str, Any]] = []
    state = (opp.get("state") or "").upper()
    twp, rng, sec = opp.get("township"), opp.get("range"), opp.get("section")
    lat, lon = opp.get("latitude"), opp.get("longitude")

    try:
        if twp and rng and state:
            from mining_os.services.blm_plss import query_claims_by_plss_with_status

            ok, claims = query_claims_by_plss_with_status(
                state=state,
                township=str(twp),
                range_val=str(rng),
                section=str(sec) if sec else None,
            )
            if not ok:
                claims = []
        elif lat is not None and lon is not None:
            from mining_os.services.blm_plss import query_claims_by_coords

            claims = query_claims_by_coords(float(lat), float(lon), radius_meters=2000)[:25]
    except Exception:
        log.exception("MLRS query failed")
        return False

    if not claims:
        return False

    oid = str(opp["id"])
    with eng.begin() as conn:
        # Replace prior auto-enriched claim context for this opportunity
        conn.execute(
            text(
                """
                DELETE FROM tax_intel.claim_context
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
                    INSERT INTO tax_intel.claim_context (
                      opportunity_id, mlrs_serial_number, claim_name, claim_status,
                      claimant_name, distance_meters, inside_parcel, raw_payload_json
                    ) VALUES (
                      CAST(:oid AS uuid), :serial, :name, :status,
                      :claimant, :dist, false, CAST(:raw AS jsonb)
                    )
                    """
                ),
                {
                    "oid": oid,
                    "serial": c.get("serial_number") or c.get("blm_serial_number"),
                    "name": c.get("claim_name"),
                    "status": c.get("status") or c.get("case_status"),
                    "claimant": c.get("claimant_name") or c.get("claimant"),
                    "dist": c.get("distance_meters"),
                    "raw": json.dumps({**c, "enrichment": "mlrs_auto"}, default=str),
                },
            )
        conn.execute(
            text(
                """
                INSERT INTO tax_intel.evidence_items (
                  opportunity_id, fact_key, fact_value_json, evidence_class,
                  extraction_method, confidence
                ) VALUES (
                  CAST(:oid AS uuid), 'mlrs_nearby_claims', CAST(:val AS jsonb), 'CLAIM',
                  'mlrs_enrich', 0.7
                )
                """
            ),
            {
                "oid": oid,
                "val": json.dumps({"value": len(claims), "sample": [c.get("serial_number") for c in claims[:5]]}),
            },
        )
        # Nudge mineral score flag if nearby claims exist
        conn.execute(
            text(
                """
                UPDATE tax_intel.tax_opportunities
                SET mineral_potential_score = LEAST(100, mineral_potential_score + 5),
                    overall_priority_score = LEAST(100, overall_priority_score + 2),
                    updated_at = :ts
                WHERE id = CAST(:oid AS uuid)
                """
            ),
            {"oid": oid, "ts": datetime.now(timezone.utc)},
        )
    return True
