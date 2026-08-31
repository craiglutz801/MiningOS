"""Assemble per-site evidence: operational / regulatory / facility / tenure / verification.

Does not change matcher scores or payment-status rollup.
"""

from __future__ import annotations

from typing import Any

from mining_os.active_mine_intel.evidence.contradictions import (
    detect_contradictions,
    has_blocking_contradiction,
)
from mining_os.active_mine_intel.evidence.freshness import production_year_is_current, source_usable
from mining_os.active_mine_intel.evidence.provenance import AssertionProvenance, assertion
from mining_os.active_mine_intel.evidence.taxonomy import (
    FACILITY_TYPES,
    OPERATIONAL_STATUSES,
    REGULATORY_STATUSES,
)
from mining_os.active_mine_intel.evidence.tenure import classify_tenure, tenure_from_claim_rows
from mining_os.active_mine_intel.evidence.verification import auto_verification_state
from mining_os.active_mine_intel.matcher.scoring import normalize_msha_status


def _status_map(source_status: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (source_status or {}).items():
        if hasattr(value, "to_dict"):
            payload = value.to_dict()
            payload["status"] = getattr(value, "status", payload.get("status"))
            payload["usable_for_assertions"] = payload.get("usable_for_assertions")
            if payload["usable_for_assertions"] is None:
                payload["usable_for_assertions"] = source_usable(value)
            out[key] = payload
        elif isinstance(value, dict):
            out[key] = value
    return out


def _freshness_for(source_id: str, sources: dict[str, Any]) -> str:
    payload = sources.get(source_id) or {}
    status = payload.get("status")
    if status in {"stale", "failed"} or payload.get("outcome") in {"stale", "failed"}:
        return "stale" if status == "stale" or payload.get("outcome") == "stale" else "unknown"
    if payload.get("usable_for_assertions") is False:
        return "stale" if payload.get("outcome") == "stale" else "unknown"
    if status in {"success", "cached", "empty"}:
        return "current"
    return "unknown"


def _usable(source_id: str, sources: dict[str, Any]) -> bool:
    if source_id not in sources:
        # Missing optional overlay is not a failure; it is simply unused.
        return False
    return source_usable(sources[source_id])


def _facility_type(site: dict[str, Any]) -> str:
    raw = " ".join(
        str(site.get(k) or "")
        for k in ("facility_type", "mine_class", "bmrr_site_type", "msha_type", "commodity")
    ).lower()
    if any(tok in raw for tok in ("mill", "processor", "plant", "concentrator", "smelter")):
        return "Mill/processor"
    if any(tok in raw for tok in ("tailing", "waste rock", "dump")):
        return "Waste/tailings"
    if any(tok in raw for tok in ("explor", "notice", "prospect")):
        return "Exploration"
    if raw.strip():
        return "Mine"
    return "Unknown"


def _regulatory_status(site: dict[str, Any], sources: dict[str, Any]) -> tuple[str, str | None]:
    bmrr = str(site.get("bmrr_permit_status") or site.get("bmrr_physical_status") or "").strip()
    if bmrr and _usable("ndep_bmrr_regulation", sources):
        lowered = bmrr.lower()
        if "reclaim" in lowered:
            return "Reclamation", "ndep_bmrr_regulation"
        if any(tok in lowered for tok in ("active", "current", "operating")):
            return "Active", "ndep_bmrr_regulation"
        if "expir" in lowered:
            return "Expired", "ndep_bmrr_regulation"
        if "closed" in lowered:
            return "Closed", "ndep_bmrr_regulation"
        return "Unknown", "ndep_bmrr_regulation"
    permit = str(site.get("permit_status") or site.get("mine_status") or "").strip()
    if permit and _usable("utah_dogm", sources):
        code = permit.upper()
        if code in {"ACT", "APP"} or any(tok in permit.lower() for tok in ("active", "current", "approved")):
            return ("Approved" if code == "APP" or "approv" in permit.lower() else "Active"), "utah_dogm"
        if any(tok in code.split() for tok in ("REC",)):
            return "Reclamation", "utah_dogm"
        if any(tok in permit.lower() for tok in ("expir", "closed", "inactive")):
            return "Closed", "utah_dogm"
    if site.get("state_permit_active") and _usable("utah_dogm", sources):
        return "Active", "utah_dogm"
    if site.get("blm_plan_present") or site.get("blm_notice_present"):
        return "Active", "blm_plans" if site.get("blm_plan_present") else "blm_notices"
    return "Unknown", None


def _independent_sources(site: dict[str, Any], sources: dict[str, Any]) -> list[str]:
    found: list[str] = []
    if site.get("state_production_confirmed") or site.get("state_permit_active") or site.get("latest_state_production_year"):
        sid = "nevada_production" if site.get("primary_source") == "nevada_production" else "utah_dogm"
        if _usable(sid, sources) or sid not in sources:
            # primary_source may be set even when SourceStatus uses the same id
            if sid in sources:
                if _usable(sid, sources):
                    found.append(sid)
            else:
                # Overlay path (tests / partial classify) — count the site-level state evidence.
                found.append(sid)
    if site.get("msha_mine_id") and (
        _usable("msha_mines", sources) or "msha_mines" not in sources
    ):
        found.append("msha_mines")
    if site.get("bmrr_project_id") or site.get("bmrr_permit_number"):
        if _usable("ndep_bmrr_regulation", sources) or "ndep_bmrr_regulation" not in sources:
            found.append("ndep_bmrr_regulation")
    if site.get("blm_plan_present") and (
        _usable("blm_plans", sources) or "blm_plans" not in sources
    ):
        found.append("blm_plans")
    # Unique while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for sid in found:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def classify_operational_status(
    site: dict[str, Any],
    *,
    current_year: int,
    sources: dict[str, Any],
    contradictions: list[dict[str, Any]],
    facility_type: str,
) -> tuple[str, list[AssertionProvenance]]:
    """Operational status. Producing requires recent structured state production."""
    assertions: list[AssertionProvenance] = []
    blocking = has_blocking_contradiction(contradictions)
    nv_ok = _usable("nevada_production", sources) if "nevada_production" in sources else True
    ut_ok = _usable("utah_dogm", sources) if "utah_dogm" in sources else True
    latest = site.get("latest_state_production_year")
    recent_nv = (
        bool(site.get("state_production_confirmed"))
        and production_year_is_current(latest, current_year=current_year)
        and nv_ok
    )
    utah_prod = bool(site.get("state_production_indicator")) and ut_ok
    # Explicit structured production only — never MSHA/BMRR/permit/hours/claim.
    if (recent_nv or utah_prod) and not blocking:
        src = "nevada_production" if recent_nv else "utah_dogm"
        assertions.append(
            assertion(
                "operational_status",
                "Producing",
                source_id=src,
                source_url=site.get("state_source_url") or (sources.get(src) or {}).get("resolved_url"),
                effective_date=str(int(latest)) if recent_nv and latest else None,
                retrieved_at=(sources.get(src) or {}).get("retrieved_at"),
                match_method=site.get("state_match_method") or "state_source",
                freshness=_freshness_for(src, sources) if src in sources else "current",
                confidence=0.85,
                notes="Structured state production evidence. Permit/MSHA/BMRR/hours were not used.",
            )
        )
        return "Producing", assertions

    if blocking:
        assertions.append(
            assertion(
                "operational_status",
                "Unknown",
                source_id="reconciliation",
                freshness="unknown",
                confidence=0.0,
                usable=False,
                contradiction={"hits": contradictions},
                notes="Fail closed: stale, missing, or contradictory evidence.",
            )
        )
        return "Unknown", assertions

    if facility_type == "Mill/processor":
        assertions.append(
            assertion(
                "operational_status",
                "Mill/processor",
                source_id=site.get("facility_source_id") or "ndep_bmrr_regulation",
                match_method="facility_type",
                freshness="current",
                confidence=0.6,
                notes="Facility type mill/processor; not a production confirmation.",
            )
        )
        return "Mill/processor", assertions

    bmrr_physical = str(site.get("bmrr_physical_status") or "").lower()
    if "reclaim" in bmrr_physical or str(site.get("bmrr_closure") or "").strip():
        assertions.append(
            assertion(
                "operational_status",
                "Reclamation",
                source_id="ndep_bmrr_reclamation"
                if site.get("bmrr_reclamation")
                else "ndep_bmrr_regulation",
                match_method=site.get("bmrr_match_method"),
                freshness=_freshness_for("ndep_bmrr_regulation", sources)
                if "ndep_bmrr_regulation" in sources
                else "current",
                confidence=0.55,
                notes="BMRR reclamation/closure evidence is not production evidence.",
            )
        )
        return "Reclamation", assertions

    msha = normalize_msha_status(site.get("msha_status"))
    hours = float(site.get("hours_last_4_quarters") or 0.0)
    if msha in {"TEMP_IDLE", "NONPRODUCING"} or (
        msha == "ACTIVE" and hours <= 0 and not site.get("state_permit_active") and not recent_nv
    ):
        # Care-and-maintenance may be suggested by MSHA corroboration but never Producing.
        if msha in {"TEMP_IDLE", "NONPRODUCING"}:
            assertions.append(
                assertion(
                    "operational_status",
                    "Care-and-maintenance",
                    source_id="msha_mines",
                    match_method="msha_status",
                    freshness=_freshness_for("msha_mines", sources) if "msha_mines" in sources else "current",
                    confidence=0.4,
                    notes="MSHA status used as corroboration only; not production proof.",
                )
            )
            return "Care-and-maintenance", assertions

    notice_only = bool(site.get("blm_notice_present")) and not site.get("blm_plan_present")
    mine_class = str(site.get("mine_class") or site.get("bmrr_site_type") or "").lower()
    if notice_only or "explor" in mine_class:
        assertions.append(
            assertion(
                "operational_status",
                "Exploration",
                source_id="blm_notices" if notice_only else "utah_dogm",
                match_method="notice_or_class",
                freshness="current",
                confidence=0.45,
                notes="Exploration/notice evidence is not production.",
            )
        )
        return "Exploration", assertions

    if site.get("state_permit_active") or str(site.get("bmrr_permit_status") or "").lower() in {
        "active",
        "current",
    }:
        src = "utah_dogm" if site.get("state_permit_active") else "ndep_bmrr_regulation"
        assertions.append(
            assertion(
                "operational_status",
                "Permitted",
                source_id=src,
                match_method="permit_active",
                freshness=_freshness_for(src, sources) if src in sources else "current",
                confidence=0.5,
                notes="Active permit is not proof of production.",
            )
        )
        return "Permitted", assertions

    assertions.append(
        assertion(
            "operational_status",
            "Unknown",
            source_id="reconciliation",
            freshness="unknown",
            confidence=0.0,
            notes="Insufficient structured production evidence; fail closed.",
        )
    )
    return "Unknown", assertions


def classify_site_evidence(
    site: dict[str, Any],
    *,
    current_year: int,
    source_status: dict[str, Any] | None = None,
    claim_rows: list[dict[str, Any]] | None = None,
    tenure_overlay: dict[str, Any] | None = None,
    human_checklist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return evidence payload to persist on a candidate site."""
    sources = _status_map(source_status)
    facility_type = _facility_type(site)
    if facility_type not in FACILITY_TYPES:
        facility_type = "Unknown"

    if tenure_overlay:
        tenure = classify_tenure(
            unpatented_intersects=bool(tenure_overlay.get("unpatented_intersects")),
            patented_intersects=bool(tenure_overlay.get("patented_intersects")),
            conveyed_intersects=bool(tenure_overlay.get("conveyed_intersects")),
            claim_count=int(tenure_overlay.get("claim_count") or 0),
            geometry_quality_groups=list(tenure_overlay.get("geometry_quality_groups") or []),
        )
    else:
        tenure = tenure_from_claim_rows(claim_rows)

    site_for_contradictions = dict(site)
    site_for_contradictions["tenure_class"] = tenure["tenure_class"]
    contradictions = detect_contradictions(
        site_for_contradictions, current_year=current_year, source_status=sources
    )
    # Source-unusable hits that are optional (BMRR/MSHA secondary) should not
    # block unless they were actually required for a Producing claim. Filter
    # supporting-only source_unusable unless nevada_production/utah_dogm/blm_claims.
    blocking_hits = [
        h
        for h in contradictions
        if h.get("resolution") == "fail_closed"
        and (
            h.get("code") != "source_unusable"
            or h.get("source_id")
            in {"nevada_production", "utah_dogm", "blm_claims", "msha_mines"}
        )
    ]
    operational, op_assertions = classify_operational_status(
        site,
        current_year=current_year,
        sources=sources,
        contradictions=blocking_hits,
        facility_type=facility_type,
    )
    if operational not in OPERATIONAL_STATUSES:
        operational = "Unknown"

    regulatory, reg_source = _regulatory_status(site, sources)
    if regulatory not in REGULATORY_STATUSES:
        regulatory = "Unknown"

    independents = _independent_sources(site, sources)
    identity_confirmed = bool(site.get("msha_mine_id") or site.get("state_source_id"))
    sources_usable = True
    for required in ("blm_claims",):
        if required in sources and not _usable(required, sources):
            sources_usable = False
            operational = "Unknown"

    verification_state = auto_verification_state(
        independent_source_count=len(independents),
        blocking_contradictions=has_blocking_contradiction(blocking_hits),
        identity_confirmed=identity_confirmed,
        tenure_known=tenure["tenure_class"] in {"Unpatented", "Patented", "Mixed"},
        sources_usable=sources_usable and not has_blocking_contradiction(blocking_hits),
        human_checklist=human_checklist,
    )

    provenance: list[dict[str, Any]] = [a.to_dict() for a in op_assertions]
    provenance.append(
        assertion(
            "tenure",
            tenure["tenure_class"],
            source_id="blm_claims",
            match_method="polygon_intersect",
            freshness=_freshness_for("blm_claims", sources) if "blm_claims" in sources else "current",
            confidence=0.7 if tenure["tenure_class"] != "Unknown" else 0.2,
            notes=tenure["tenure_notes"],
            extra={"geometry_limitations": tenure["geometry_limitations"]},
        ).to_dict()
    )
    if reg_source:
        provenance.append(
            assertion(
                "regulatory_status",
                regulatory,
                source_id=reg_source,
                match_method=site.get("bmrr_match_method") or "status_field",
                freshness=_freshness_for(reg_source, sources) if reg_source in sources else "current",
                confidence=0.55,
            ).to_dict()
        )
    provenance.append(
        assertion(
            "facility_type",
            facility_type,
            source_id=site.get("facility_source_id") or "reconciliation",
            match_method="site_type_or_class",
            freshness="current",
            confidence=0.5,
        ).to_dict()
    )
    if site.get("msha_status"):
        provenance.append(
            assertion(
                "msha_corroboration",
                str(site.get("msha_status")),
                source_id="msha_mines",
                match_method="msha_id_or_name",
                freshness=_freshness_for("msha_mines", sources) if "msha_mines" in sources else "current",
                confidence=0.4,
                notes="MSHA activity, inspections, and hours are corroboration only.",
            ).to_dict()
        )

    fail_closed = operational == "Unknown" and (
        has_blocking_contradiction(blocking_hits) or not sources_usable
    )

    return {
        "operational_status": operational,
        "regulatory_status": regulatory,
        "facility_type": facility_type,
        "tenure_class": tenure["tenure_class"],
        "tenure_json": tenure,
        "verification_state": verification_state,
        "contradictions_json": blocking_hits,
        "assertions_json": provenance,
        "independent_sources": independents,
        "fail_closed": fail_closed,
        "identity_confirmed": identity_confirmed,
        # payment_status is intentionally absent — claim_rollup owns it.
    }
