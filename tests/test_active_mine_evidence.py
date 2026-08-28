"""Active Mine Search evidence model tests (T-041).

Covers taxonomy, recency, contradictions, source failure vs empty, mixed tenure,
deterministic matching, and verification transitions. Does not change payment-status
rollup semantics.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from mining_os.active_mine_intel.evidence.classify import classify_site_evidence
from mining_os.active_mine_intel.evidence.freshness import source_outcome, source_usable
from mining_os.active_mine_intel.evidence.reconciliation import (
    MATCH_ID_EXACT,
    MATCH_NAME_COORDS,
    MATCH_UNMATCHED,
    best_match,
    match_records,
)
from mining_os.active_mine_intel.evidence.taxonomy import OPERATIONAL_STATUSES, VERIFICATION_STATES
from mining_os.active_mine_intel.evidence.tenure import classify_tenure
from mining_os.active_mine_intel.evidence.utah_coverage import diagnose_dogm_coverage
from mining_os.active_mine_intel.evidence.verification import (
    CHECKLIST_ITEMS,
    empty_checklist,
    transition_verification,
    validate_human_checklist,
)
from mining_os.active_mine_intel.matcher.models import SourceStatus
from mining_os.active_mine_intel.staging import looks_like_production_url, staging_isolation_report


def _ok_sources(**overrides):
    base = {
        "blm_claims": {"status": "success", "outcome": "ok", "usable_for_assertions": True, "record_count": 10},
        "nevada_production": {
            "status": "success",
            "outcome": "ok",
            "usable_for_assertions": True,
            "record_count": 5,
            "resolved_url": "https://example.test/nv",
        },
        "msha_mines": {"status": "success", "outcome": "ok", "usable_for_assertions": True, "record_count": 5},
    }
    base.update(overrides)
    return base


def test_taxonomy_contains_required_labels():
    assert OPERATIONAL_STATUSES == (
        "Producing",
        "Permitted",
        "Exploration",
        "Mill/processor",
        "Care-and-maintenance",
        "Reclamation",
        "Unknown",
    )
    assert VERIFICATION_STATES == ("Candidate", "Cross-source confirmed", "Human Verified")


def test_producing_requires_recent_state_production_not_msha():
    site = {
        "msha_status": "Active",
        "hours_last_4_quarters": 12000,
        "inspections_last_18_months": 4,
        "blm_plan_present": True,
        "state_permit_active": True,
        "bmrr_permit_status": "Active",
        "primary_source": "nevada_production",
        "msha_mine_id": "2600001",
    }
    ev = classify_site_evidence(site, current_year=2026, source_status=_ok_sources())
    assert ev["operational_status"] != "Producing"
    site["latest_state_production_year"] = 2025
    site["state_production_confirmed"] = True
    ev2 = classify_site_evidence(site, current_year=2026, source_status=_ok_sources())
    assert ev2["operational_status"] == "Producing"


def test_stale_production_fails_closed():
    site = {
        "latest_state_production_year": 2025,
        "state_production_confirmed": True,
        "primary_source": "nevada_production",
        "msha_mine_id": "1",
    }
    stale = _ok_sources(
        nevada_production={
            "status": "stale",
            "outcome": "stale",
            "usable_for_assertions": False,
            "record_count": 5,
        }
    )
    ev = classify_site_evidence(site, current_year=2026, source_status=stale)
    assert ev["operational_status"] == "Unknown"
    assert ev["fail_closed"] or ev["operational_status"] == "Unknown"


def test_old_production_year_is_not_producing():
    site = {
        "latest_state_production_year": 2018,
        "state_production_confirmed": True,
        "primary_source": "nevada_production",
        "state_permit_active": True,
        "msha_mine_id": "1",
    }
    ev = classify_site_evidence(site, current_year=2026, source_status=_ok_sources())
    assert ev["operational_status"] != "Producing"
    assert ev["operational_status"] in OPERATIONAL_STATUSES


def test_contradiction_production_vs_abandoned_fails_closed():
    site = {
        "latest_state_production_year": 2025,
        "state_production_confirmed": True,
        "primary_source": "nevada_production",
        "msha_status": "Abandoned",
        "msha_mine_id": "1",
    }
    ev = classify_site_evidence(site, current_year=2026, source_status=_ok_sources())
    assert ev["operational_status"] == "Unknown"
    assert ev["fail_closed"] is True
    assert any(h["code"] == "production_vs_abandoned" for h in ev["contradictions_json"])


def test_source_failure_distinct_from_empty():
    failed = source_outcome(fetched_ok=False, record_count=0, message="timeout", failure_class="timeout")
    empty = source_outcome(fetched_ok=True, record_count=0)
    ok = source_outcome(fetched_ok=True, record_count=12)
    assert failed["outcome"] == "failed"
    assert empty["outcome"] == "empty"
    assert ok["outcome"] == "ok"
    assert failed["record_count"] == empty["record_count"] == 0
    assert failed["usable_for_assertions"] is False
    assert empty["usable_for_assertions"] is True
    status_failed = SourceStatus(source_id="utah_dogm", status="failed", outcome="failed", record_count=0)
    status_empty = SourceStatus(source_id="utah_dogm", status="empty", outcome="empty", record_count=0)
    assert source_usable(status_failed) is False
    assert source_usable(status_empty) is True
    assert status_failed.to_dict()["outcome"] != status_empty.to_dict()["outcome"]


def test_mixed_tenure_and_geometry_limitations():
    mixed = classify_tenure(
        unpatented_intersects=True,
        patented_intersects=True,
        claim_count=4,
        geometry_quality_groups=["PLSS_DIRECT", "COARSE"],
    )
    assert mixed["tenure_class"] == "Mixed"
    assert mixed["mixed_tenure"] is True
    assert mixed["geometry_approximate"] is True
    assert mixed["geometry_surveyed"] is False
    assert mixed["tenure_source"] == "blm_claims"
    assert any("tenure evidence only" in n.lower() or "not closed" in n.lower() for n in [mixed["tenure_notes"], *mixed["geometry_limitations"]])
    unpat = classify_tenure(unpatented_intersects=True, patented_intersects=False, claim_count=2)
    assert unpat["tenure_class"] == "Unpatented"


def test_deterministic_id_match_beats_name():
    left = {"msha_mine_id": "2600123", "mine_name": "Alpha", "operator_name": "A LLC", "latitude": 39.5, "longitude": -116.1}
    right = {"msha_mine_id": "2600123", "mine_name": "Completely Different", "operator_name": "B Inc", "latitude": 40.0, "longitude": -117.0}
    decision = match_records(left, right)
    assert decision["matched"] is True
    assert decision["match_method"] == MATCH_ID_EXACT


def test_coordinates_alone_do_not_match():
    left = {"mine_name": "Alpha", "operator_name": "A", "latitude": 39.5, "longitude": -116.1}
    right = {"mine_name": "Zeta", "operator_name": "Z", "latitude": 39.5001, "longitude": -116.1001}
    decision = match_records(left, right)
    assert decision["matched"] is False
    assert decision["match_method"] == MATCH_UNMATCHED


def test_name_and_tight_coords_match():
    left = {"mine_name": "Spotted Horse Mine", "operator_name": "X", "latitude": 39.5, "longitude": -116.1}
    right = {"mine_name": "Spotted Horse", "operator_name": "Y", "latitude": 39.5005, "longitude": -116.1005}
    decision = match_records(left, right)
    assert decision["matched"] is True
    assert decision["match_method"] == MATCH_NAME_COORDS


def test_ambiguous_tie_fails_closed():
    target = {"mine_name": "Twin Pit", "operator_name": "Op Co", "latitude": 39.5, "longitude": -116.1}
    c1 = {"mine_name": "Twin Pit", "operator_name": "Op Co", "latitude": 39.5001, "longitude": -116.1001}
    c2 = {"mine_name": "Twin Pit", "operator_name": "Op Co", "latitude": 39.5002, "longitude": -116.1002}
    match, decision = best_match(target, [c1, c2])
    # Distances differ slightly so this may pick one; force equal coords:
    c2["latitude"] = 39.5001
    c2["longitude"] = -116.1001
    match, decision = best_match(target, [c1, c2])
    assert match is None
    assert decision["match_method"] == MATCH_UNMATCHED
    assert decision["reason"] == "ambiguous_tie"


def test_human_verified_requires_dated_checklist():
    payload = empty_checklist()
    ok, err, _ = validate_human_checklist(payload)
    assert ok is False
    payload["reviewer_name"] = "Craig"
    payload["reviewed_at"] = "2026-08-27"
    for item in payload["items"]:
        item["checked"] = True
    ok, err, normalized = validate_human_checklist(payload)
    assert ok is True
    assert normalized["reviewed_at"] == "2026-08-27"
    assert len(CHECKLIST_ITEMS) == 5


def test_cannot_auto_promote_to_human_verified():
    ok, state, err, _ = transition_verification(
        "Candidate",
        proposed="Human Verified",
        checklist=None,
        independent_source_count=3,
        identity_confirmed=True,
        tenure_known=True,
        sources_usable=True,
    )
    assert ok is False
    assert state == "Candidate"
    assert err is not None
    assert "dated" in err.lower() or "checklist" in err.lower()


def test_cross_source_confirmed_and_candidate():
    ok, state, err, _ = transition_verification(
        "Candidate",
        proposed="Cross-source confirmed",
        independent_source_count=2,
        blocking_contradictions=False,
        identity_confirmed=True,
        tenure_known=True,
        sources_usable=True,
    )
    assert ok is True
    assert state == "Cross-source confirmed"
    ok2, state2, _, _ = transition_verification(
        "Candidate",
        proposed="Cross-source confirmed",
        independent_source_count=1,
        identity_confirmed=True,
        tenure_known=True,
        sources_usable=True,
    )
    assert ok2 is False
    assert state2 == "Candidate"


def test_classify_sets_cross_source_when_two_sources_agree():
    site = {
        "latest_state_production_year": 2025,
        "state_production_confirmed": True,
        "primary_source": "nevada_production",
        "msha_mine_id": "2600001",
        "msha_status": "Active",
    }
    ev = classify_site_evidence(
        site,
        current_year=2026,
        source_status=_ok_sources(),
        tenure_overlay={
            "unpatented_intersects": True,
            "patented_intersects": False,
            "claim_count": 2,
            "geometry_quality_groups": ["PLSS_DIRECT"],
        },
    )
    assert ev["operational_status"] == "Producing"
    assert ev["verification_state"] == "Cross-source confirmed"
    assert ev["tenure_class"] == "Unpatented"


def test_utah_coverage_reports_uranium_gap():
    diag = diagnose_dogm_coverage(
        selected_title="Utah Mineral Permit Points",
        selected_url="https://example.test/dogm",
        candidate_titles=["Utah Mineral Permit Points", "Coal Mines"],
        candidate_fields=["PERMIT_NO", "COMMODITY", "STATUS"],
        commodities=["Limestone", "Gypsum"],
        record_count=20,
        source_status="success",
    )
    codes = {g["code"] for g in diag["gaps"]}
    assert "uranium_not_in_coverage" in codes
    assert diag["source_failed"] is False
    assert diag["valid_empty"] is False


def test_utah_coverage_source_failure_not_empty():
    failed = diagnose_dogm_coverage(
        selected_title=None,
        selected_url=None,
        record_count=0,
        source_status="failed",
    )
    empty = diagnose_dogm_coverage(
        selected_title="Utah Mineral Permit Points",
        selected_url="https://example.test/dogm",
        record_count=0,
        source_status="empty",
    )
    assert failed["source_failed"] is True
    assert empty["valid_empty"] is True
    assert failed["gaps"][0]["code"] == "dogm_source_unusable"


def test_bmrr_fixture_normalizes_and_is_not_producing():
    from mining_os.active_mine_intel.matcher.config import get_config
    from mining_os.active_mine_intel.matcher.ndep_bmrr_adapter import load_fixture

    cfg = get_config("NV")
    path = Path(__file__).parent / "fixtures" / "active_mines" / "sample_bmrr.csv"
    gdf = load_fixture(path, cfg, layer_kind="regulation")
    assert not gdf.empty
    assert "Demo Copper Mine" in set(gdf["mine_name"])
    mill = gdf[gdf["mine_name"] == "Demo Mill"].iloc[0]
    site = {
        "bmrr_site_type": mill["bmrr_site_type"],
        "bmrr_permit_status": mill["bmrr_permit_status"],
        "bmrr_physical_status": mill["bmrr_physical_status"],
        "bmrr_project_id": mill["bmrr_project_id"],
        "facility_source_id": "ndep_bmrr_regulation",
    }
    ev = classify_site_evidence(
        site,
        current_year=2026,
        source_status=_ok_sources(
            ndep_bmrr_regulation={
                "status": "success",
                "outcome": "ok",
                "usable_for_assertions": True,
                "record_count": 3,
            }
        ),
    )
    assert ev["operational_status"] != "Producing"
    assert ev["facility_type"] == "Mill/processor"


def test_claim_rollup_unchanged_paid_unpaid_rules():
    from mining_os.active_mine_intel.claim_rollup import rollup_from_claims

    total, unpaid, paid, unknown, rollup = rollup_from_claims(
        [
            {"payment_status": "paid"},
            {"payment_status": "unpaid"},
            {"payment_status": "unknown"},
        ]
    )
    assert (total, unpaid, paid, unknown, rollup) == (3, 1, 1, 1, "unpaid")


def test_staging_isolation_blocks_production_host(monkeypatch):
    monkeypatch.setenv("MINING_OS_ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@miningos.onrender.com/miningos")
    report = staging_isolation_report()
    assert report["ok"] is False
    assert any("DATABASE_URL" in v for v in report["violations"])
    assert looks_like_production_url("https://miningos.onrender.com/api") is True
    assert looks_like_production_url("https://mining-os-api-staging.onrender.com") is False


def test_vercel_preview_rewrite_is_not_production():
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parents[1] / "frontend" / "vercel.json").read_text())
    dest = data["rewrites"][0]["destination"]
    assert "miningos.onrender.com" not in dest
    assert "${API_ORIGIN}" not in dest  # preview must pin an isolated staging origin
    assert "trycloudflare.com" in dest or "staging" in dest.lower()


def test_source_status_to_dict_distinguishes_empty():
    empty = SourceStatus(source_id="x", status="empty", record_count=0, outcome="empty")
    failed = SourceStatus(source_id="x", status="failed", record_count=0, outcome="failed")
    assert empty.to_dict()["outcome"] == "empty"
    assert failed.to_dict()["outcome"] == "failed"
    assert empty.to_dict()["usable_for_assertions"] is not False or empty.status == "empty"
