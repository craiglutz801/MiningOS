"""
Production-safety tests for the LR2000 / MLRS Geographic Index report.

Specifically covers the production failure that returned `Internal Server
Error` (HTTP 500). The service is now wrapped in a defensive try/except so
callers always receive a structured `{ok, error, claims, ...}` response.
"""

from __future__ import annotations

import pytest

from mining_os.services import mlrs_geographic_index as mlrs


SAMPLE_AREA = {
    "id": 1,
    "name": "Demo target",
    "location_plss": "12S 14E 23",
    "state_abbr": "UT",
    "meridian": "26",
    "township": "12S",
    "range": "14E",
    "section": "23",
    "latitude": 39.0,
    "longitude": -111.0,
}


@pytest.fixture
def patched_persist(monkeypatch):
    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.merge_area_characteristics",
        lambda area_id, updates: True,
    )


class TestStructuredResponses:
    def test_happy_path(self, monkeypatch, patched_persist):
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss",
            lambda **kw: [{"claim_name": "C1", "serial_number": "S1"}],
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords",
            lambda *a, **kw: [],
        )

        result = mlrs.run_lr2000_geographic_index_for_area(1, SAMPLE_AREA)

        assert result["ok"] is True
        assert result["error"] is None
        assert len(result["claims"]) == 1
        assert result["query_method"]
        assert result["fetched_at"]

    def test_no_plss_returns_structured_error_not_500(self, patched_persist):
        result = mlrs.run_lr2000_geographic_index_for_area(
            1,
            {"id": 1, "name": "x", "location_plss": None},
        )

        assert result["ok"] is False
        assert result.get("error")
        assert result["claims"] == []

    def test_db_persist_failure_does_not_break_response(self, monkeypatch):
        """If merge_area_characteristics raises, user still gets the claims."""
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss",
            lambda **kw: [{"claim_name": "C1", "serial_number": "S1"}],
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords",
            lambda *a, **kw: [],
        )

        def _explode(area_id, updates):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.merge_area_characteristics", _explode
        )

        result = mlrs.run_lr2000_geographic_index_for_area(1, SAMPLE_AREA)

        # Service should still return ok:True with claims; DB warning logged separately.
        assert result["ok"] is True
        assert len(result["claims"]) == 1

    def test_unexpected_exception_returned_as_structured_error(self, monkeypatch):
        """Any unhandled exception must return ok:False JSON, never bubble to 500."""
        def _kaboom(**kw):
            raise RuntimeError("BLM ArcGIS service exploded")

        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss", _kaboom
        )

        result = mlrs.run_lr2000_geographic_index_for_area(1, SAMPLE_AREA)

        assert result["ok"] is False
        assert "LR2000 report failed" in (result.get("error") or "")
        assert result["claims"] == []

    def test_empty_area_returns_structured_error(self):
        result = mlrs.run_lr2000_geographic_index_for_area(1, {})
        assert result["ok"] is False
        assert "Area not found" in (result.get("error") or "")
