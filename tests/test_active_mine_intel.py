"""Active Mine Search unit tests — scoring, PLSS bridge, API flag."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mining_os.active_mine_intel.claim_rollup import rollup_from_characteristics, rollup_from_claims
from mining_os.active_mine_intel.matcher.config import get_config
from mining_os.active_mine_intel.matcher.scoring import MATCH_BASE_POINTS, score_candidate
from mining_os.active_mine_intel.plss_bridge import (
    from_matcher_row,
    parse_cse_meta,
    resolve_site_plss,
)


def test_claim_rollup_counts_match_drilldown_statuses():
    total, unpaid, paid, unknown, rollup = rollup_from_claims(
        [
            {"payment_status": "paid"},
            {"payment_status": "Paid"},
            {"payment_status": "unpaid"},
            {"payment_status": "unknown"},
            {"payment_status": ""},
            {"serial": "x"},
        ]
    )
    assert total == 6
    assert paid == 2
    assert unpaid == 1
    assert unknown == 3
    assert rollup == "unpaid"


def test_claim_rollup_from_characteristics_string_json():
    chars = (
        '{"claim_records":{"fetched_at":"2026-08-20T00:00:00Z","claims":['
        '{"payment_status":"paid"},{"payment_status":"paid"}]}}'
    )
    out = rollup_from_characteristics(chars)
    assert out == (2, 0, 2, 0, "paid")


def test_active_mines_meta_reports_disabled_by_default(monkeypatch):
    monkeypatch.setattr("mining_os.config.settings.ENABLE_ACTIVE_MINES_API", False)
    from mining_os.api.main import api_app

    client = TestClient(api_app)
    res = client.get("/active-mines/meta")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["enabled"] is False
    assert "NV" in body.get("supported_states", [])
    assert "UT" in body.get("supported_states", [])
    assert "Producing" in (body.get("operational_statuses") or [])
    assert "Human Verified" in (body.get("verification_states") or [])
    assert "environment" in body


def test_active_mines_sites_disabled_payload(monkeypatch):
    monkeypatch.setattr("mining_os.config.settings.ENABLE_ACTIVE_MINES_API", False)
    from mining_os.api.main import api_app

    client = TestClient(api_app)
    res = client.get("/active-mines/sites")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["enabled"] is False
    assert "disabled" in (body.get("error") or "").lower()


def test_score_candidate_near_claim_baseline():
    cfg = get_config("NV")
    pair = {
        "match_type": "NEAR_CLAIM_0_250M",
        "mine_claim_name_similarity": 80.0,
        "mine_claim_operator_similarity": 0.0,
        "distance_meters": 100.0,
    }
    site = {
        "msha_status": "Active",
        "hours_last_8_quarters": 1000.0,
        "latest_inspection_date": None,
        "blm_plan_present": False,
        "blm_notice_present": False,
        "state_permit_active": False,
        "latest_state_production_year": None,
        "state_production_years": [],
    }
    scores = score_candidate(pair, site, cfg, None, 2026, degraded_mode=False)
    assert "total_score" in scores
    assert scores["total_score"] > 0
    assert scores["claim_match_score"] >= MATCH_BASE_POINTS["NEAR_CLAIM_0_250M"][0]
    assert scores["confidence_category"] in {"HIGH", "STRONG", "REVIEW", "WEAK", "LOW"}


def test_parse_cse_meta_decodes_x10_and_section():
    parsed = parse_cse_meta("NV 21 0190N 0320E 009 A LODE")
    assert parsed is not None
    assert parsed["state_abbr"] == "NV"
    assert parsed["meridian"] == "21"
    assert parsed["township"] == "19N"
    assert parsed["range"] == "32E"
    assert parsed["section"] == "9"
    assert "T19N" in parsed["location_plss"]
    assert "Sec 9" in parsed["location_plss"]


def test_parse_cse_meta_single_digit_township_not_t80():
    """CadNSDI 0080S is T8S — must not become T80S."""
    parsed = parse_cse_meta("UT 26 0080S 0170W 030 A LODE")
    assert parsed is not None
    assert parsed["township"] == "8S"
    assert parsed["range"] == "17W"
    assert parsed["section"] == "30"
    assert "T8S" in parsed["location_plss"]
    assert "T80S" not in parsed["location_plss"]


def test_from_matcher_row_rebuilds_location_from_x10_components(monkeypatch):
    """×10 township 80S + wrong T80S location → rebuild as T8S."""
    monkeypatch.setattr(
        "mining_os.active_mine_intel.plss_bridge._normalize_plss",
        lambda plss, default_state=None: "UT 0080S 0170W 030",
    )
    # After _human_tr, bare 80S stays 80S (ambiguous); 4-digit CadNSDI is required.
    row = {
        "location_plss": "UT T80S R17W Sec 30",
        "township": "0080S",
        "range": "0170W",
        "section": "30",
        "state_abbr": "UT",
        "plss_source": "matcher",
    }
    out = from_matcher_row(row, "UT")
    assert out is not None
    assert out["township"] == "8S"
    assert out["range"] == "17W"
    assert "T8S" in out["location_plss"]
    assert "T80S" not in out["location_plss"]


def test_from_matcher_row_builds_normalized_key(monkeypatch):
    monkeypatch.setattr(
        "mining_os.active_mine_intel.plss_bridge._normalize_plss",
        lambda plss, default_state=None: "NV 19N 32E 009",
    )
    row = {
        "location_plss": "NV T19N R32E Sec 9",
        "township": "190N",
        "range": "320E",
        "section": "9",
        "plss_source": "matcher",
    }
    out = from_matcher_row(row, "NV")
    assert out is not None
    assert out["plss_status"] == "resolved"
    assert out["plss_normalized"] == "NV 19N 32E 009"
    assert out["township"] == "19N"
    assert out["range"] == "32E"
    assert out["section"] == "9"
    assert out["meridian"] == "21"


def test_resolve_site_plss_unresolved_without_inputs():
    out = resolve_site_plss(
        latitude=None,
        longitude=None,
        state_abbr="UT",
        matcher_row={},
        use_network=False,
    )
    assert out["plss_status"] == "unresolved"
    assert out["plss_normalized"] is None
    assert out["meridian"] == "26"


def test_resolve_or_create_reuses_existing(monkeypatch):
    from mining_os.active_mine_intel import target_link

    monkeypatch.setattr(
        target_link,
        "find_target_by_plss",
        lambda account_id, plss_normalized: {
            "id": 42,
            "name": "Existing",
            "plss_normalized": plss_normalized,
        },
    )
    created_calls = []

    def _upsert(**kwargs):
        created_calls.append(kwargs)
        return 99

    monkeypatch.setattr(target_link, "upsert_area", _upsert)

    aof_id, created = target_link.resolve_or_create_section_target(
        1,
        plss={
            "plss_normalized": "NV 19N 32E 009",
            "location_plss": "NV T19N R32E Sec 9",
            "state_abbr": "NV",
            "township": "19N",
            "range": "32E",
            "section": "9",
            "meridian": "21",
        },
        mine_name="Test Mine",
    )
    assert aof_id == 42
    assert created is False
    assert created_calls == []


def test_resolve_or_create_creates_when_missing(monkeypatch):
    from mining_os.active_mine_intel import target_link

    monkeypatch.setattr(target_link, "find_target_by_plss", lambda *a, **k: None)

    def _upsert(**kwargs):
        assert kwargs["source"] == "active_mine_plss"
        assert kwargs["retrieval_type"] == "Known Mine"
        assert kwargs["state_abbr"] == "NV"
        assert "Spotted Horse" in kwargs["name"]
        assert "T19N" in kwargs["name"]
        return 77

    monkeypatch.setattr(target_link, "upsert_area", _upsert)

    aof_id, created = target_link.resolve_or_create_section_target(
        1,
        plss={
            "plss_normalized": "NV 19N 32E 009",
            "location_plss": "NV T19N R32E Sec 9",
            "state_abbr": "NV",
            "township": "19N",
            "range": "32E",
            "section": "9",
            "meridian": "21",
        },
        mine_name="Spotted Horse",
        latitude=39.1,
        longitude=-116.2,
    )
    assert aof_id == 77
    assert created is True
