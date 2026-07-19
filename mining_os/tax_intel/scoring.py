"""Deterministic scoring for Tax Opportunities (tax-v1.0)."""

from __future__ import annotations

from typing import Any


SCORE_VERSION = "tax-v1.0"


def priority_tier(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "E"


def compute_scores(inputs: dict[str, Any]) -> dict[str, Any]:
    """
    Compute mineral potential, acquisition readiness, and overall priority.

    ``inputs`` is a flat dict of boolean/numeric evidence flags. Missing keys
    are treated as absent (zero contribution).
    """
    mineral = 0.0
    mineral_positives: list[str] = []

    if inputs.get("mine_inside_parcel"):
        mineral += 20
        mineral_positives.append("Documented mine occurrence inside parcel")
    if inputs.get("commodity_evidence"):
        mineral += 15
        mineral_positives.append("Documented commodity evidence")
    if inputs.get("historic_production"):
        mineral += 15
        mineral_positives.append("Historic production evidence")
    if inputs.get("patent_tied_to_mine"):
        mineral += 10
        mineral_positives.append("Patent/claim identity tied to mine")
    if inputs.get("mineral_survey_coherence"):
        mineral += 10
        mineral_positives.append("Mineral Survey / claim-name coherence")
    if inputs.get("technical_docs"):
        mineral += 10
        mineral_positives.append("Strong technical documentation")
    if inputs.get("favorable_district"):
        mineral += 5
        mineral_positives.append("Favorable mining district")
    if inputs.get("nearby_active_claims"):
        mineral += 5
        mineral_positives.append("Nearby active claim activity")
    if inputs.get("nearby_occurrences"):
        mineral += 5
        mineral_positives.append("Nearby supporting occurrences")
    if inputs.get("geological_support"):
        mineral += 5
        mineral_positives.append("Geological support")

    acquisition = 0.0
    acq_positives: list[str] = []
    if inputs.get("clear_sale_stage"):
        acquisition += 20
        acq_positives.append("Clear sale stage/date")
    if inputs.get("patent_confirmed"):
        acquisition += 20
        acq_positives.append("Patent confirmation")
    elif inputs.get("patent_probable"):
        acquisition += 12
        acq_positives.append("Probable patent match")
    if inputs.get("geometry_confirmed"):
        acquisition += 10
        acq_positives.append("Parcel geometry confirmed")
    if inputs.get("bid_or_amount_known"):
        acquisition += 10
        acq_positives.append("Bid/amount known")
    if inputs.get("owner_or_legal_known"):
        acquisition += 10
        acq_positives.append("Owner/legal description known")
    if inputs.get("title_progress"):
        acquisition += 10
        acq_positives.append("Title/mineral review progress")
    if inputs.get("apparent_access"):
        acquisition += 10
        acq_positives.append("Apparent mapped access")
    if inputs.get("source_fresh"):
        acquisition += 5
        acq_positives.append("Source freshness")
    if inputs.get("data_complete"):
        acquisition += 5
        acq_positives.append("Data completeness")

    penalty = 0.0
    risks: list[str] = []
    if inputs.get("mineral_severance"):
        penalty += 20
        risks.append("Confirmed mineral severance")
    if inputs.get("patent_contradiction"):
        penalty += 20
        risks.append("Major patent/parcel contradiction")
    if inputs.get("severe_environmental"):
        penalty += 15
        risks.append("Severe environmental flag")
    if inputs.get("no_mapped_access"):
        penalty += 8
        risks.append("No apparent mapped access")
    if inputs.get("title_conflict"):
        penalty += 12
        risks.append("Title conflict")
    if inputs.get("partial_interest"):
        penalty += 8
        risks.append("Partial undivided interest")
    if inputs.get("source_stale"):
        penalty += 5
        risks.append("Source stale beyond SLA")
    if inputs.get("approx_geometry"):
        penalty += 5
        risks.append("Approximate geometry only")
    if not risks and inputs.get("title_not_reviewed"):
        risks.append("Mineral-rights chain not reviewed")

    mineral = min(100.0, mineral)
    acquisition = min(100.0, acquisition)
    overall = max(0.0, min(100.0, 0.55 * mineral + 0.45 * acquisition - penalty))
    tier = priority_tier(overall)

    return {
        "score_version": SCORE_VERSION,
        "mineral_potential_score": round(mineral, 1),
        "acquisition_readiness_score": round(acquisition, 1),
        "risk_penalty": round(penalty, 1),
        "overall_priority_score": round(overall, 1),
        "priority_tier": tier,
        "explanation_json": {
            "overall_priority_score": round(overall, 1),
            "mineral_potential_score": round(mineral, 1),
            "acquisition_readiness_score": round(acquisition, 1),
            "risk_penalty": round(penalty, 1),
            "top_positive_factors": (mineral_positives + acq_positives)[:5],
            "top_risks": risks[:5],
            "score_version": SCORE_VERSION,
        },
    }
