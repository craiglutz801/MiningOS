"""SITLA scoring unit tests."""

from __future__ import annotations

from mining_os.sitla_intel.normalize import canonical_key, map_lifecycle, map_opportunity_type
from mining_os.sitla_intel.scoring import SCORE_VERSION, compute_scores, priority_tier


def test_priority_tiers():
    assert priority_tier(90) == "A"
    assert priority_tier(75) == "B"
    assert priority_tier(60) == "C"
    assert priority_tier(45) == "D"
    assert priority_tier(10) == "E"


def test_compute_scores_explainable():
    result = compute_scores(
        {
            "mine_inside_acreage": True,
            "commodity_evidence": True,
            "strategic_mineral": True,
            "official_active": True,
            "deadline_clear": True,
            "geometry_resolved": True,
            "commercial_terms": True,
            "rights_unclear": True,
        }
    )
    assert result["score_version"] == SCORE_VERSION
    assert result["overall_priority_score"] > 0
    assert result["priority_tier"] in {"A", "B", "C", "D", "E"}
    assert "top_positive_factors" in result["explanation_json"]
    assert any("Rights" in r for r in result["explanation_json"]["top_risks"])


def test_normalize_lifecycle_and_type():
    assert map_lifecycle("Bidding open") == "BIDDING_OPEN"
    assert map_opportunity_type("Metalliferous mineral lease") == "METALLIFEROUS_MINERAL_LEASE"
    assert len(canonical_key("ML-1", "Juab", "T12S R2W", "2026-JUN", "row-1")) == 40
