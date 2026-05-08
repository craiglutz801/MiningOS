"""
Production-safety tests for fetch_claim_records.

Specifically covers the failure mode that caused the production bug:
  "BLM_ClaimAgent not found. Set MINING_OS_BLM_AGENT_PATH or place repo at
   Agents/BLM_ClaimAgent."

In production (Render/Railway) the BLM_ClaimAgent companion repo is NOT
deployed. The service must gracefully fall back to the built-in BLM ArcGIS
API instead of returning that error.
"""

from __future__ import annotations

import pytest

from mining_os.services import fetch_claim_records as fcr


@pytest.fixture(autouse=True)
def _disable_mlrs_case_page_network(monkeypatch):
    """Fetch tests stub ArcGIS only; do not HTTP/Selenium real mlrs.blm.gov pages."""
    monkeypatch.setattr(
        "mining_os.services.mlrs_case_payment.enrich_claims_from_mlrs_case_pages",
        lambda claims: claims,
    )


@pytest.fixture
def patched_persist(monkeypatch):
    """Stub out the DB writers so unit tests don't touch Postgres."""
    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.merge_area_characteristics",
        lambda area_id, updates, **kwargs: True,
    )
    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.update_area_state_meridian",
        lambda area_id, state, meridian, **kwargs: True,
    )
    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.update_area_status",
        lambda area_id, status, **kwargs: True,
    )


class TestFallbackWhenAgentMissing:
    """When BLM_ClaimAgent is not on disk, the service must use the built-in API."""

    def test_no_agent_uses_built_in_api(self, monkeypatch, patched_persist):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)

        api_called = {"plss": 0, "coords": 0}
        seen_sections: list[str | None] = []

        def fake_query_by_plss_with_status(**kwargs):
            api_called["plss"] += 1
            seen_sections.append(kwargs.get("section"))
            return (
                True,
                [
                    {
                        "claim_name": "TEST CLAIM",
                        "serial_number": "UMC123",
                        "payment_status": "paid",
                        "BLM_PROD": "Lode",
                    }
                ],
            )

        def fake_query_by_coords(*args, **kwargs):
            api_called["coords"] += 1
            return []

        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            fake_query_by_plss_with_status,
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords", fake_query_by_coords
        )

        result = fcr.fetch_claim_records_for_area(
            area_id=42,
            area_name="Demo target",
            location_plss=None,
            state_abbr="UT",
            meridian="26",
            township="12S",
            range_val="14E",
            section="23",
            latitude=None,
            longitude=None,
        )

        assert result["ok"] is True
        assert "BLM_ClaimAgent not found" not in (result.get("error") or "")
        assert len(result["claims"]) == 1
        assert result["claims"][0]["claim_name"] == "TEST CLAIM"
        assert api_called["plss"] >= 1, "built-in PLSS API should have been queried"
        assert seen_sections == ["23"]
        assert api_called["coords"] == 0, "spatial query should not run when no coordinates were supplied"

    def test_no_agent_no_results_returns_clean_error(self, monkeypatch, patched_persist):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)
        # Simulate the BLM service being unreachable so we surface the error path.
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            lambda **kw: (False, []),
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords",
            lambda *a, **kw: [],
        )

        result = fcr.fetch_claim_records_for_area(
            area_id=1,
            area_name="No-claim area",
            location_plss=None,
            state_abbr="NV",
            meridian="21",
            township="21N",
            range_val="57E",
            section="23",
            latitude=40.0,
            longitude=-117.0,
        )

        assert result["ok"] is False
        assert "BLM_ClaimAgent not found" not in (result.get("error") or "")
        assert "no claims found" in (result.get("error") or "").lower() or \
               "unreachable" in (result.get("error") or "").lower()

    def test_no_agent_uses_spatial_when_no_plss_match(self, monkeypatch, patched_persist):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            lambda **kw: (True, []),
        )

        spatial_called = []

        def fake_spatial(lat, lon, radius_meters=2000):
            spatial_called.append((lat, lon, radius_meters))
            return [{"claim_name": "SPATIAL HIT", "serial_number": "S1", "payment_status": "paid"}]

        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords", fake_spatial
        )

        result = fcr.fetch_claim_records_for_area(
            area_id=7,
            area_name="Spatial fallback",
            location_plss=None,
            state_abbr="UT",
            meridian="26",
            township="12S",
            range_val="14E",
            section="23",
            latitude=38.5,
            longitude=-110.5,
        )

        assert result["ok"] is True
        assert len(result["claims"]) == 1
        assert result["claims"][0]["claim_name"] == "SPATIAL HIT"
        assert spatial_called, "spatial fallback should have been invoked"

    def test_no_agent_augments_plss_results_with_spatial(self, monkeypatch, patched_persist):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)

        calls: list[str] = []

        def fake_query_by_plss_with_status(**kwargs):
            calls.append("plss")
            return True, [
                {
                    "claim_name": "SECTION HIT",
                    "serial_number": "UMC777",
                    "payment_status": "paid",
                    "plss": "UT 12S 12W Sec 35",
                }
            ]

        def fake_query_by_coords(*args, **kwargs):
            calls.append("coords")
            return [
                {
                    "claim_name": "SECTION HIT",
                    "serial_number": "UMC777",
                    "payment_status": "paid",
                    "plss": "UT 12S 12W Sec 35",
                },
                {
                    "claim_name": "SPATIAL HIT",
                    "serial_number": "S1",
                    "payment_status": "paid",
                    "plss": "UT 13S 12W Sec 03",
                },
            ]

        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            fake_query_by_plss_with_status,
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords", fake_query_by_coords
        )

        result = fcr.fetch_claim_records_for_area(
            area_id=70,
            area_name="Prefer section",
            location_plss=None,
            state_abbr="UT",
            meridian="26",
            township="12S",
            range_val="12W",
            section="35",
            latitude=39.7,
            longitude=-113.1,
        )

        assert result["ok"] is True
        assert len(result["claims"]) == 2
        assert [claim["claim_name"] for claim in result["claims"]] == ["SECTION HIT", "SPATIAL HIT"]
        assert "Added 1 nearby claim" in (result["log"] or "")
        assert calls == ["plss", "coords"]
    def test_no_agent_broadens_only_after_empty_section_query(self, monkeypatch, patched_persist):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)

        seen_sections: list[str | None] = []

        def fake_query_by_plss_with_status(**kwargs):
            seen_sections.append(kwargs.get("section"))
            if kwargs.get("section") == "23":
                return True, []
            return True, [
                {
                    "claim_name": "BROAD HIT",
                    "serial_number": "UMC999",
                    "payment_status": "paid",
                }
            ]

        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            fake_query_by_plss_with_status,
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords",
            lambda *a, **kw: [],
        )

        result = fcr.fetch_claim_records_for_area(
            area_id=8,
            area_name="Broaden me",
            location_plss=None,
            state_abbr="UT",
            meridian="26",
            township="12S",
            range_val="14E",
            section="23",
            latitude=38.5,
            longitude=-110.5,
        )

        assert result["ok"] is True
        assert len(result["claims"]) == 1
        assert result["claims"][0]["claim_name"] == "BROAD HIT"
        assert seen_sections == ["23", None]


class TestNormalizeClaims:
    """The unified shape every UI/automation rule expects, regardless of source path."""

    def test_script_shape_gets_claim_name_and_serial_number(self):
        # BLM_ClaimAgent script returns CSE_NAME / CSE_NR (no claim_name / serial_number).
        normalized = fcr._normalize_claims(
            [
                {
                    "CSE_NAME": "PEBBLE # 5",
                    "CSE_NR": "UT101527746",
                    "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT101527746",
                    "payment_status": "unpaid",
                    "payment_message": "Maintenance fee payment was not received and may result in the closing of the claim.",
                    "geometry": {"rings": [[[0, 0]]]},
                    "OBJECTID": 1,
                    "Shape__Length": 1.23,
                    "project_name": "tmp",
                    "plss_state": "UT",
                }
            ]
        )
        assert len(normalized) == 1
        c = normalized[0]
        assert c["claim_name"] == "PEBBLE # 5"
        assert c["serial_number"] == "UT101527746"
        # The unpaid maintenance-fee message is preserved verbatim.
        assert "Maintenance fee payment was not received" in c["payment_message"]
        # Heavy / private fields are dropped.
        for dropped in ("geometry", "OBJECTID", "Shape__Length", "project_name", "plss_state"):
            assert dropped not in c

    def test_api_shape_keeps_existing_claim_name(self):
        # Built-in ArcGIS fallback already returns claim_name / serial_number.
        normalized = fcr._normalize_claims(
            [
                {
                    "claim_name": "ALPHA",
                    "serial_number": "UMC1",
                }
            ]
        )
        assert normalized[0]["claim_name"] == "ALPHA"
        assert normalized[0]["serial_number"] == "UMC1"
        # payment_status defaults to "unknown" so the UI badge never blanks.
        assert normalized[0]["payment_status"] == "unknown"

    def test_strips_dot_gov_banner_from_account_name(self):
        normalized = fcr._normalize_claims(
            [
                {
                    "claim_name": "X",
                    "serial_number": "Y",
                    "account_name": "An official website of the United States government\n  Here's how you know",
                }
            ]
        )
        # The .gov banner that the Selenium scraper sometimes captures is dropped.
        assert normalized[0]["account_name"] is None

    def test_keeps_real_account_name(self):
        normalized = fcr._normalize_claims(
            [
                {
                    "claim_name": "X",
                    "serial_number": "Y",
                    "account_name": "Acme Mining LLC",
                }
            ]
        )
        assert normalized[0]["account_name"] == "Acme Mining LLC"


class TestPlssMissing:
    """When PLSS is unparseable, return a structured ok:False."""

    def test_no_plss_no_components(self, monkeypatch):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)

        result = fcr.fetch_claim_records_for_area(
            area_id=99,
            area_name="x",
            location_plss=None,
            state_abbr=None,
            meridian=None,
            township=None,
            range_val=None,
        )

        assert result["ok"] is False
        assert "PLSS" in (result.get("error") or "")
        assert result["claims"] == []


class TestDerivedAreaStatus:
    def test_mixed_paid_and_unknown_rolls_up_to_paid(self, monkeypatch):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)

        captured: dict[str, str | None] = {"status": None}

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.merge_area_characteristics",
            lambda area_id, updates, **kwargs: True,
        )
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_state_meridian",
            lambda area_id, state, meridian, **kwargs: True,
        )

        def fake_update_area_status(area_id, status, **kwargs):
            captured["status"] = status
            return True

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_status",
            fake_update_area_status,
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            lambda **kwargs: (
                True,
                [
                    {"claim_name": "PAID CLAIM", "serial_number": "A1", "payment_status": "paid"},
                    {"claim_name": "UNKNOWN CLAIM", "serial_number": "A2", "payment_status": "unknown"},
                ],
            ),
        )
        monkeypatch.setattr(
            "mining_os.services.mlrs_case_payment.enrich_claims_from_mlrs_case_pages",
            lambda claims, progress_cb=None: claims,
        )

        result = fcr.fetch_claim_records_for_area(
            area_id=501,
            area_name="Mixed status target",
            location_plss=None,
            state_abbr="UT",
            meridian="26",
            township="12S",
            range_val="12W",
            section="35",
            latitude=None,
            longitude=None,
        )

        assert result["ok"] is True
        assert captured["status"] == "paid"

    def test_any_unpaid_rolls_up_to_unpaid(self, monkeypatch):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)

        captured: dict[str, str | None] = {"status": None}

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.merge_area_characteristics",
            lambda area_id, updates, **kwargs: True,
        )
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_state_meridian",
            lambda area_id, state, meridian, **kwargs: True,
        )

        def fake_update_area_status(area_id, status, **kwargs):
            captured["status"] = status
            return True

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_status",
            fake_update_area_status,
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            lambda **kwargs: (
                True,
                [
                    {"claim_name": "UNPAID CLAIM", "serial_number": "B1", "payment_status": "unpaid"},
                    {"claim_name": "PAID CLAIM", "serial_number": "B2", "payment_status": "paid"},
                    {"claim_name": "UNKNOWN CLAIM", "serial_number": "B3", "payment_status": "unknown"},
                ],
            ),
        )
        monkeypatch.setattr(
            "mining_os.services.mlrs_case_payment.enrich_claims_from_mlrs_case_pages",
            lambda claims, progress_cb=None: claims,
        )

        result = fcr.fetch_claim_records_for_area(
            area_id=502,
            area_name="Unpaid wins target",
            location_plss=None,
            state_abbr="UT",
            meridian="26",
            township="12S",
            range_val="12W",
            section="35",
            latitude=None,
            longitude=None,
        )

        assert result["ok"] is True
        assert captured["status"] == "unpaid"
