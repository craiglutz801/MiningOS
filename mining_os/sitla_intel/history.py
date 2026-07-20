"""Historical offering / reoffering match heuristics."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.sitla_intel.history")


def match_historical_offerings(account_id: int) -> dict[str, Any]:
    """Link active opportunities to historical ones sharing PLSS or reference prefix."""
    eng = get_engine()
    created = 0
    with eng.begin() as conn:
        active = conn.execute(
            text(
                """
                SELECT id::text, plss_key, reference_number, county_name, acreage
                FROM sitla_intel.opportunities
                WHERE account_id = :aid AND is_active = true AND is_historical = false
                """
            ),
            {"aid": account_id},
        ).mappings().all()
        historical = conn.execute(
            text(
                """
                SELECT id::text, plss_key, reference_number, county_name, acreage, winning_bid
                FROM sitla_intel.opportunities
                WHERE account_id = :aid AND (is_historical = true OR lifecycle_status IN ('AWARDED','NO_BID','ARCHIVED'))
                """
            ),
            {"aid": account_id},
        ).mappings().all()

        for a in active:
            for h in historical:
                if str(a["id"]) == str(h["id"]):
                    continue
                conf = 0.0
                mtype = "POSSIBLE_MATCH"
                if a.get("plss_key") and a.get("plss_key") == h.get("plss_key"):
                    conf = 0.85
                    mtype = "REOFFERING"
                elif (
                    a.get("county_name")
                    and a.get("county_name") == h.get("county_name")
                    and a.get("acreage")
                    and h.get("acreage")
                    and abs(float(a["acreage"]) - float(h["acreage"])) < 1.0
                ):
                    conf = 0.55
                    mtype = "COMPARABLE_ACREAGE"
                else:
                    continue
                exists = conn.execute(
                    text(
                        """
                        SELECT 1 FROM sitla_intel.historical_matches
                        WHERE opportunity_id = CAST(:oid AS uuid)
                          AND related_opportunity_id = CAST(:rid AS uuid)
                        LIMIT 1
                        """
                    ),
                    {"oid": a["id"], "rid": h["id"]},
                ).first()
                if exists:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO sitla_intel.historical_matches (
                          opportunity_id, related_opportunity_id, match_type, match_confidence, summary
                        ) VALUES (
                          CAST(:oid AS uuid), CAST(:rid AS uuid), :mt, :conf, :summary
                        )
                        """
                    ),
                    {
                        "oid": a["id"],
                        "rid": h["id"],
                        "mt": mtype,
                        "conf": conf,
                        "summary": (
                            f"Matched to historical {h.get('reference_number') or h['id']}"
                            + (f" (winning bid {h.get('winning_bid')})" if h.get("winning_bid") else "")
                        ),
                    },
                )
                created += 1
                # Nudge acquisition readiness for historical comps
                conn.execute(
                    text(
                        """
                        UPDATE sitla_intel.opportunities
                        SET acquisition_readiness_score = LEAST(100, acquisition_readiness_score + 3),
                            overall_priority_score = LEAST(100, overall_priority_score + 1)
                        WHERE id = CAST(:oid AS uuid)
                        """
                    ),
                    {"oid": a["id"]},
                )
    return {"ok": True, "matches_created": created}
