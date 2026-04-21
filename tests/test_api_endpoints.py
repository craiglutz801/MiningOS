"""
End-to-end API tests using FastAPI TestClient.

These test the production-facing routes for the two actions that broke
in production:
  * POST /api/areas-of-focus/{id}/fetch-claim-records
  * POST /api/areas-of-focus/{id}/lr2000-geographic-report

The goal is: regardless of underlying service success/failure,
the HTTP response is always 200 OK with a structured `{ok, error, ...}`
JSON body — never an Internal Server Error and never the
"BLM_ClaimAgent not found" leak.

Tests run without a real database — services are monkey-patched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    # Import inside the fixture so monkeypatching modules works cleanly.
    from mining_os.api.main import app
    # No context manager → lifespan/startup events (e.g. automation
    # scheduler) are skipped so tests don't need a real DB.
    return TestClient(app)


@pytest.fixture
def fake_area():
    return {
        "id": 1,
        "name": "Test target",
        "location_plss": "12S 14E 23",
        "state_abbr": "UT",
        "meridian": "26",
        "township": "12S",
        "range": "14E",
        "section": "23",
        "latitude": 39.0,
        "longitude": -111.0,
    }


def _patch_persist(monkeypatch):
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


class TestHealth:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestFetchClaimRecordsEndpoint:
    def test_returns_200_when_area_missing(self, client, monkeypatch):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: None,
        )
        r = client.post("/api/areas-of-focus/9999/fetch-claim-records")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "Area not found" in body["error"]

    def test_returns_200_in_production_without_blm_agent(
        self, client, monkeypatch, fake_area
    ):
        """The exact bug we are fixing: prod has no BLM_ClaimAgent.
        Endpoint must NOT return the 'BLM_ClaimAgent not found' error."""
        _patch_persist(monkeypatch)
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: fake_area,
        )
        # Force the service to behave as if BLM_ClaimAgent is missing
        monkeypatch.setattr(
            "mining_os.services.fetch_claim_records._blm_agent_path",
            lambda: None,
        )
        # Built-in API returns claims
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss",
            lambda **kw: [
                {"claim_name": "C1", "serial_number": "S1", "payment_status": "paid"}
            ],
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords",
            lambda *a, **kw: [],
        )

        r = client.post("/api/areas-of-focus/1/fetch-claim-records")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "BLM_ClaimAgent not found" not in (body.get("error") or "")
        assert len(body["claims"]) == 1

    def test_returns_200_even_if_service_throws(
        self, client, monkeypatch
    ):
        def _kaboom(area_id):
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area", _kaboom
        )
        r = client.post("/api/areas-of-focus/1/fetch-claim-records")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "Fetch Claim Records failed" in body["error"]


class TestLr2000Endpoint:
    def test_returns_200_when_area_missing(self, client, monkeypatch):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: None,
        )
        r = client.post("/api/areas-of-focus/9999/lr2000-geographic-report")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "Area not found" in body["error"]

    def test_returns_200_on_happy_path(self, client, monkeypatch, fake_area):
        _patch_persist(monkeypatch)
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: fake_area,
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss",
            lambda **kw: [{"claim_name": "C1", "serial_number": "S1"}],
        )
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_coords",
            lambda *a, **kw: [],
        )

        r = client.post("/api/areas-of-focus/1/lr2000-geographic-report")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert len(body["claims"]) == 1

    def test_returns_200_when_service_throws_no_500(
        self, client, monkeypatch, fake_area
    ):
        """The exact bug we are fixing: LR2000 was returning Internal Server Error.
        Now it must always return a structured JSON body."""
        _patch_persist(monkeypatch)
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: fake_area,
        )

        def _kaboom(**kw):
            raise RuntimeError("BLM ArcGIS service down")

        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss", _kaboom
        )

        r = client.post("/api/areas-of-focus/1/lr2000-geographic-report")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "LR2000 report failed" in body["error"]
        assert body["claims"] == []
