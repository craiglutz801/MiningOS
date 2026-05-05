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


class TestAreaSummaryRoutes:
    def test_summary_returns_200(self, client, monkeypatch):
        payload = {
            "total_count": 642,
            "target_status_counts": {
                "monitoring_high": 12,
                "monitoring_med": 34,
                "monitoring_low": 501,
                "negotiation": 41,
                "due_diligence": 21,
                "ownership": 33,
            },
        }
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.areas_summary",
            lambda: payload,
        )
        r = client.get("/api/areas-of-focus/summary")
        assert r.status_code == 200
        assert r.json() == payload

    def test_list_forwards_target_status(self, client, monkeypatch):
        captured: dict[str, object] = {}

        def _list_areas(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.list_areas",
            _list_areas,
        )
        r = client.get("/api/areas-of-focus?target_status=monitoring_high&limit=5000")
        assert r.status_code == 200
        assert r.json() == []
        assert captured["target_status"] == "monitoring_high"
        assert captured["limit"] == 5000


class TestDiagnostics:
    """Production diagnostics — must always return 200 with useful JSON."""

    def test_environment_returns_200(self, client):
        r = client.get("/api/diag/environment")
        assert r.status_code == 200
        body = r.json()
        assert "env" in body
        assert "blm_claim_agent" in body
        assert "database" in body
        assert "blm_arcgis" in body
        # env presence flags
        assert isinstance(body["env"]["DATABASE_URL"], bool)
        assert isinstance(body["env"]["OPENAI_API_KEY"], bool)

    def test_area_returns_200_when_missing(self, client, monkeypatch):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: None,
        )
        r = client.get("/api/diag/area/9999")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "not found" in body["error"]

    def test_area_reports_stored_fields(self, client, monkeypatch, fake_area):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: fake_area,
        )
        r = client.get("/api/diag/area/1")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["has_stored_plss_components"] is True
        assert body["has_coords"] is True
        assert body["fields"]["state_abbr"] == "UT"

    def test_diag_fetch_claim_records_proxies_safe_wrapper(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: None,
        )
        r = client.post("/api/diag/fetch-claim-records/1")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "Area not found" in body["error"]


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
        # Built-in API returns claims (production path uses query_claims_by_plss_with_status)
        monkeypatch.setattr(
            "mining_os.services.blm_plss.query_claims_by_plss_with_status",
            lambda **kw: (
                True,
                [
                    {"claim_name": "C1", "serial_number": "S1", "payment_status": "paid"}
                ],
            ),
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


class TestClearCharacteristicSnapshots:
    """Stored MLRS / LR2000 JSON snapshots can be removed without touching the target row."""

    def test_clear_claim_records_returns_200(self, client, monkeypatch, fake_area):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: fake_area if area_id == 1 else None,
        )
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.remove_area_characteristic_keys",
            lambda area_id, keys: area_id == 1 and keys == ["claim_records"],
        )
        r = client.post("/api/areas-of-focus/1/clear-claim-records")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["removed"] == ["claim_records"]

    def test_clear_lr2000_returns_200_when_missing_area(self, client, monkeypatch):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: None,
        )
        r = client.post("/api/areas-of-focus/999/clear-lr2000-report")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "not found" in (body.get("error") or "").lower()


class TestAreaEditEndpoints:
    def test_update_minerals_returns_200(self, client, monkeypatch):
        captured: dict[str, object] = {}

        def _update(area_id, minerals):
            captured["area_id"] = area_id
            captured["minerals"] = minerals
            return True

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_minerals",
            _update,
        )
        r = client.post("/api/areas-of-focus/1/minerals", json={"minerals": ["Gold", "Silver"]})
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == 1
        assert body["minerals"] == ["Gold", "Silver"]
        assert captured == {"area_id": 1, "minerals": ["Gold", "Silver"]}

    def test_update_minerals_returns_404_when_target_missing(self, client, monkeypatch):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_minerals",
            lambda area_id, minerals: False,
        )
        r = client.post("/api/areas-of-focus/999/minerals", json={"minerals": ["Gold"]})
        assert r.status_code == 404
        assert "Target not found" in r.text


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
