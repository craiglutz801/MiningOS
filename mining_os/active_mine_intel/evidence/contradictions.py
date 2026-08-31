"""Contradiction detection — fail closed rather than pick a winner."""

from __future__ import annotations

from typing import Any

from mining_os.active_mine_intel.evidence.freshness import production_year_is_current
from mining_os.active_mine_intel.matcher.scoring import normalize_msha_status


def detect_contradictions(
    site: dict[str, Any],
    *,
    current_year: int,
    source_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return structured contradictions. Any hit blocks Producing and Cross-source confirmed."""
    hits: list[dict[str, Any]] = []
    msha = normalize_msha_status(site.get("msha_status"))
    latest = site.get("latest_state_production_year") or site.get("latest_production_activity_year")
    recent_prod = production_year_is_current(latest, current_year=current_year)
    utah_prod = bool(site.get("state_production_indicator"))
    producing_claim = recent_prod or utah_prod

    if producing_claim and msha == "ABANDONED":
        hits.append(
            {
                "code": "production_vs_abandoned",
                "left": {"assertion_type": "operational_status", "value": "Producing"},
                "right": {"assertion_type": "msha_status", "value": site.get("msha_status")},
                "resolution": "fail_closed",
            }
        )
    if producing_claim and msha == "TEMP_IDLE":
        hits.append(
            {
                "code": "production_vs_idle",
                "left": {"assertion_type": "operational_status", "value": "Producing"},
                "right": {"assertion_type": "msha_status", "value": site.get("msha_status")},
                "resolution": "fail_closed",
            }
        )

    bmrr_physical = str(site.get("bmrr_physical_status") or "").strip().lower()
    if producing_claim and any(
        tok in bmrr_physical for tok in ("reclaim", "closed", "inactive", "abandon")
    ):
        hits.append(
            {
                "code": "production_vs_bmrr_inactive",
                "left": {"assertion_type": "operational_status", "value": "Producing"},
                "right": {
                    "assertion_type": "regulatory_status",
                    "value": site.get("bmrr_physical_status"),
                    "source_id": "ndep_bmrr_regulation",
                },
                "resolution": "fail_closed",
            }
        )

    dogm_codes = " ".join(
        str(site.get(k) or "") for k in ("permit_status", "mine_status", "mine_class")
    ).upper()
    if producing_claim and any(
        code in dogm_codes.split() for code in ("RET", "ARC", "REC", "INA", "NAP", "NPR", "FOR")
    ):
        hits.append(
            {
                "code": "production_vs_dogm_inactive",
                "left": {"assertion_type": "operational_status", "value": "Producing"},
                "right": {"assertion_type": "regulatory_status", "value": dogm_codes},
                "resolution": "fail_closed",
            }
        )

    if source_status:
        for source_id, payload in source_status.items():
            status = payload.get("status") if isinstance(payload, dict) else getattr(payload, "status", None)
            if status in {"failed", "stale", "unavailable"}:
                hits.append(
                    {
                        "code": "source_unusable",
                        "source_id": source_id,
                        "status": status,
                        "resolution": "fail_closed",
                    }
                )

    tenure = site.get("tenure_class")
    if tenure == "Mixed" and site.get("require_unpatented_only"):
        hits.append(
            {
                "code": "mixed_tenure",
                "left": {"assertion_type": "tenure", "value": "Unpatented"},
                "right": {"assertion_type": "tenure", "value": "Mixed"},
                "resolution": "flag_only",
            }
        )
    return hits


def has_blocking_contradiction(hits: list[dict[str, Any]]) -> bool:
    return any(h.get("resolution") == "fail_closed" for h in hits)
