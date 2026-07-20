"""Deterministic scoring for SITLA opportunities (sitla-v1.0)."""

from __future__ import annotations

from typing import Any

SCORE_VERSION = "sitla-v1.0"


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
    mineral = 0.0
    mineral_positives: list[str] = []

    if inputs.get("mine_inside_acreage"):
        mineral += 20
        mineral_positives.append("On-acreage mine/occurrence evidence")
    if inputs.get("commodity_evidence"):
        mineral += 15
        mineral_positives.append("Evidence-supported commodity")
    if inputs.get("historic_production"):
        mineral += 15
        mineral_positives.append("Historical production")
    if inputs.get("technical_reports"):
        mineral += 10
        mineral_positives.append("Strong technical reports")
    if inputs.get("favorable_geology"):
        mineral += 10
        mineral_positives.append("Favorable deposit geology")
    if inputs.get("district_quality"):
        mineral += 8
        mineral_positives.append("Mining district quality")
    if inputs.get("active_claims"):
        mineral += 7
        mineral_positives.append("Active claim activity")
    if inputs.get("historical_exploration"):
        mineral += 5
        mineral_positives.append("Historical exploration")
    if inputs.get("strategic_mineral"):
        mineral += 5
        mineral_positives.append("Strategic-mineral relevance")
    if inputs.get("multiple_sources"):
        mineral += 5
        mineral_positives.append("Multiple independent sources")

    acquisition = 0.0
    acq_positives: list[str] = []
    if inputs.get("official_active"):
        acquisition += 15
        acq_positives.append("Official active opportunity")
    if inputs.get("deadline_clear"):
        acquisition += 15
        acq_positives.append("Deadline/process clarity")
    if inputs.get("geometry_resolved"):
        acquisition += 10
        acq_positives.append("Geometry resolved")
    if inputs.get("rights_clear"):
        acquisition += 15
        acq_positives.append("Rights clear")
    if inputs.get("commercial_terms"):
        acquisition += 10
        acq_positives.append("Commercial terms available")
    if inputs.get("documents_complete"):
        acquisition += 10
        acq_positives.append("Official documents complete")
    if inputs.get("access_understood"):
        acquisition += 5
        acq_positives.append("Access reasonably understood")
    if inputs.get("historical_comparisons"):
        acquisition += 5
        acq_positives.append("Historical comparisons")
    if inputs.get("data_fresh"):
        acquisition += 5
        acq_positives.append("Data freshness")
    if inputs.get("diligence_progress"):
        acquisition += 10
        acq_positives.append("Diligence progress")

    penalty = 0.0
    risks: list[str] = []
    if inputs.get("rights_unclear"):
        penalty += 12
        risks.append("Rights materially unclear")
    if inputs.get("geometry_unresolved"):
        penalty += 8
        risks.append("Geometry unresolved")
    if inputs.get("lease_conflict"):
        penalty += 15
        risks.append("Existing lease conflict")
    if inputs.get("access_concern"):
        penalty += 8
        risks.append("Serious access concern")
    if inputs.get("environmental_issue"):
        penalty += 12
        risks.append("Major environmental/cultural issue")
    if inputs.get("urgent_incomplete"):
        penalty += 5
        risks.append("Deadline under 72 hours with incomplete diligence")
    if inputs.get("source_stale"):
        penalty += 5
        risks.append("Source stale")
    if inputs.get("commodity_regional_only"):
        penalty += 5
        risks.append("Commodity regional only")

    mineral = min(100.0, mineral)
    acquisition = min(100.0, acquisition)
    overall = max(0.0, min(100.0, 0.60 * mineral + 0.40 * acquisition - penalty))
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
