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


@pytest.fixture
def patched_persist(monkeypatch):
    """Stub out the DB writers so unit tests don't touch Postgres."""
    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.merge_area_characteristics",
        lambda area_id, updates: True,
    )
    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.update_area_state_meridian",
        lambda area_id, state, meridian: True,
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

        def fake_query_by_plss(**kwargs):
            api_called["plss"] += 1
            return [
                {
                    "claim_name": "TEST CLAIM",
                    "serial_number": "UMC123",
                    "payment_status": "paid",
                    "BLM_PROD": "Lode",
                }
            ]

        def fake_query_by_coords(*args, **kwargs):
            api_called["coords"] += 1
            return []

        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss", fake_query_by_plss
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
            latitude=39.0,
            longitude=-111.0,
        )

        assert result["ok"] is True
        assert "BLM_ClaimAgent not found" not in (result.get("error") or "")
        assert len(result["claims"]) == 1
        assert result["claims"][0]["claim_name"] == "TEST CLAIM"
        assert api_called["plss"] >= 1, "built-in PLSS API should have been queried"

    def test_no_agent_no_results_returns_clean_error(self, monkeypatch, patched_persist):
        monkeypatch.setattr(fcr, "_blm_agent_path", lambda: None)
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss",
            lambda **kw: [],
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
            "mining_os.services.blm_plss.query_claims_by_plss",
            lambda **kw: [],
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
