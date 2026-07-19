"""Unit tests for Tax Sales deterministic scoring."""

from mining_os.tax_intel.scoring import compute_scores, priority_tier


def test_priority_tiers():
    assert priority_tier(90) == "A"
    assert priority_tier(75) == "B"
    assert priority_tier(60) == "C"
    assert priority_tier(45) == "D"
    assert priority_tier(10) == "E"


def test_high_value_confirmed_patent_scores_tier_a_or_b():
    result = compute_scores(
        {
            "mine_inside_parcel": True,
            "commodity_evidence": True,
            "historic_production": True,
            "patent_tied_to_mine": True,
            "mineral_survey_coherence": True,
            "technical_docs": True,
            "favorable_district": True,
            "nearby_active_claims": True,
            "nearby_occurrences": True,
            "geological_support": True,
            "clear_sale_stage": True,
            "patent_confirmed": True,
            "geometry_confirmed": True,
            "bid_or_amount_known": True,
            "owner_or_legal_known": True,
            "title_progress": True,
            "apparent_access": True,
            "source_fresh": True,
            "data_complete": True,
        }
    )
    assert result["mineral_potential_score"] == 100
    assert result["acquisition_readiness_score"] == 100
    assert result["overall_priority_score"] >= 85
    assert result["priority_tier"] == "A"
    assert result["score_version"] == "tax-v1.0"
    assert "Confirmed" in " ".join(result["explanation_json"]["top_positive_factors"]) or any(
        "patent" in f.lower() or "mine" in f.lower()
        for f in result["explanation_json"]["top_positive_factors"]
    )


def test_risk_penalties_reduce_score():
    base = compute_scores(
        {
            "clear_sale_stage": True,
            "geometry_confirmed": True,
            "bid_or_amount_known": True,
            "owner_or_legal_known": True,
            "apparent_access": True,
            "source_fresh": True,
            "data_complete": True,
            "commodity_evidence": True,
        }
    )
    penalized = compute_scores(
        {
            "clear_sale_stage": True,
            "geometry_confirmed": True,
            "bid_or_amount_known": True,
            "owner_or_legal_known": True,
            "apparent_access": True,
            "source_fresh": True,
            "data_complete": True,
            "commodity_evidence": True,
            "severe_environmental": True,
            "no_mapped_access": True,
        }
    )
    assert penalized["risk_penalty"] >= 20
    assert penalized["overall_priority_score"] < base["overall_priority_score"]


def test_llm_never_required_for_scores():
    # Empty inputs still return a valid explainable payload.
    result = compute_scores({})
    assert result["overall_priority_score"] == 0
    assert result["priority_tier"] == "E"
    assert "explanation_json" in result
