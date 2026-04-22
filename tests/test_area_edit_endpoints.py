"""
Tests for user-initiated target edits: rename and PLSS update.

Covers both the service functions (``update_area_name``, ``update_area_plss``)
and the two pairs of API endpoints (``/areas-of-focus/{id}/name`` + the
``/api/`` mirror, same for ``/plss``).

No DB is required — all DB and BLM interactions are monkey-patched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from mining_os.api.main import app
    return TestClient(app)


@pytest.fixture
def fake_area():
    return {
        "id": 42,
        "name": "Spor Mountain (old)",
        "location_plss": "UT 14S 4E Sec 35",
        "state_abbr": "UT",
        "meridian": "26",
        "township": "14S",
        "range": "4E",
        "section": "35",
        "latitude": 39.558,
        "longitude": -111.442,
    }


# ────────────────────────────────────────────────────────────────────
# Service: update_area_name
# ────────────────────────────────────────────────────────────────────

class TestUpdateAreaName:
    def test_rejects_empty_name(self, monkeypatch):
        from mining_os.services.areas_of_focus import update_area_name
        # Should not call DB at all for empty input
        class BoomEngine:
            def begin(self):
                raise AssertionError("DB should not be touched for empty name")
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_engine",
            lambda: BoomEngine(),
        )
        assert update_area_name(1, "") is False
        assert update_area_name(1, "   ") is False
        assert update_area_name(1, None) is False


# ────────────────────────────────────────────────────────────────────
# Service: update_area_plss
# ────────────────────────────────────────────────────────────────────

class TestUpdateAreaPlss:
    def test_returns_not_found_when_area_missing(self, monkeypatch):
        from mining_os.services import areas_of_focus as svc
        monkeypatch.setattr(svc, "get_area", lambda area_id: None)
        result = svc.update_area_plss(999, "UT 13S 11W Sec 29")
        assert result == {"ok": False, "error": "not_found"}

    def test_unparseable_plss_returns_error(self, monkeypatch, fake_area):
        from mining_os.services import areas_of_focus as svc
        monkeypatch.setattr(svc, "get_area", lambda area_id: fake_area)
        result = svc.update_area_plss(42, "not a real plss string at all")
        assert result["ok"] is False
        assert result["error"] == "unparseable_plss"

    def test_empty_plss_clears_fields(self, monkeypatch, fake_area):
        """Submitting '' or None clears PLSS and all parsed components but keeps coords."""
        from mining_os.services import areas_of_focus as svc
        monkeypatch.setattr(svc, "get_area", lambda area_id: fake_area)

        captured = {}

        class FakeConn:
            def execute(self, stmt, params):
                sql = str(stmt)
                if "UPDATE" in sql:
                    captured["sql"] = sql
                    captured["params"] = params
                return SimpleNamespace(rowcount=1)

        class FakeEngine:
            def begin(self):
                class CtxMgr:
                    def __enter__(inner_self):
                        return FakeConn()

                    def __exit__(inner_self, *args):
                        return False
                return CtxMgr()

        monkeypatch.setattr(svc, "get_engine", lambda: FakeEngine())
        result = svc.update_area_plss(42, "")
        assert result["ok"] is True
        assert result["location_plss"] is None
        assert result["state_abbr"] is None
        assert "location_plss = NULL" in captured["sql"]
        assert "state_abbr = NULL" in captured["sql"]
        # Coordinates preserved
        assert result["latitude"] == fake_area["latitude"]
        assert result["longitude"] == fake_area["longitude"]

    def test_happy_path_regeocodes(self, monkeypatch, fake_area):
        """Valid PLSS → components written AND lat/lon re-derived."""
        from mining_os.services import areas_of_focus as svc
        monkeypatch.setattr(svc, "get_area", lambda area_id: fake_area)

        # Force geocoder to return a known lat/lon
        monkeypatch.setattr(
            "mining_os.services.plss_geocode.geocode_plss",
            lambda **kw: {"latitude": 39.70, "longitude": -113.24},
        )

        captured = {}

        class FakeConn:
            def execute(self, stmt, params):
                sql = str(stmt)
                if "SELECT id, name FROM areas_of_focus WHERE plss_normalized" in sql:
                    return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
                if "UPDATE" in sql:
                    captured["params"] = params
                    return SimpleNamespace(rowcount=1)
                return SimpleNamespace(rowcount=0)

        class FakeEngine:
            def begin(self):
                class CtxMgr:
                    def __enter__(inner_self):
                        return FakeConn()

                    def __exit__(inner_self, *args):
                        return False
                return CtxMgr()

        monkeypatch.setattr(svc, "get_engine", lambda: FakeEngine())

        result = svc.update_area_plss(42, "UT 13S 11W Sec 29", regeocode_coordinates=True)
        assert result["ok"] is True
        assert result["location_plss"] == "UT 13S 11W Sec 29"
        assert result["state_abbr"] == "UT"
        # Correct coords replaced the wrong stored ones
        assert result["latitude"] == 39.70
        assert result["longitude"] == -113.24
        assert result["regeocoded"] is True
        # Persisted to DB with new coords
        assert captured["params"]["lat"] == 39.70
        assert captured["params"]["lon"] == -113.24

    def test_no_regeocode_preserves_stored_coords(self, monkeypatch, fake_area):
        from mining_os.services import areas_of_focus as svc
        monkeypatch.setattr(svc, "get_area", lambda area_id: fake_area)

        def boom(**kw):
            raise AssertionError("geocoder should not be called when regeocode=False")

        monkeypatch.setattr("mining_os.services.plss_geocode.geocode_plss", boom)

        class FakeConn:
            def execute(self, stmt, params):
                sql = str(stmt)
                if "SELECT id, name FROM areas_of_focus WHERE plss_normalized" in sql:
                    return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
                return SimpleNamespace(rowcount=1)

        class FakeEngine:
            def begin(self):
                class CtxMgr:
                    def __enter__(inner_self):
                        return FakeConn()

                    def __exit__(inner_self, *args):
                        return False
                return CtxMgr()

        monkeypatch.setattr(svc, "get_engine", lambda: FakeEngine())

        result = svc.update_area_plss(42, "UT 13S 11W Sec 29", regeocode_coordinates=False)
        assert result["ok"] is True
        assert result["latitude"] == fake_area["latitude"]
        assert result["longitude"] == fake_area["longitude"]
        assert result["regeocoded"] is False

    def test_duplicate_plss_conflict(self, monkeypatch, fake_area):
        """When the parsed PLSS collides with another target, we return a
        structured error including the conflicting target's id and name."""
        from mining_os.services import areas_of_focus as svc
        monkeypatch.setattr(svc, "get_area", lambda area_id: fake_area)
        monkeypatch.setattr(
            "mining_os.services.plss_geocode.geocode_plss",
            lambda **kw: {"latitude": 39.70, "longitude": -113.24},
        )

        class FakeConn:
            def execute(self, stmt, params):
                sql = str(stmt)
                if "SELECT id, name FROM areas_of_focus WHERE plss_normalized" in sql:
                    return SimpleNamespace(mappings=lambda: SimpleNamespace(
                        first=lambda: {"id": 99, "name": "Existing Target"}
                    ))
                raise AssertionError("UPDATE must not run on conflict")

        class FakeEngine:
            def begin(self):
                class CtxMgr:
                    def __enter__(inner_self):
                        return FakeConn()

                    def __exit__(inner_self, *args):
                        return False
                return CtxMgr()

        monkeypatch.setattr(svc, "get_engine", lambda: FakeEngine())

        result = svc.update_area_plss(42, "UT 13S 11W Sec 29")
        assert result["ok"] is False
        assert result["error"] == "duplicate_plss"
        assert result["conflicting_id"] == 99
        assert result["conflicting_name"] == "Existing Target"

    def test_geocoder_failure_does_not_block_update(self, monkeypatch, fake_area):
        """If BLM Cadastral is down, PLSS save still succeeds; coords preserved."""
        from mining_os.services import areas_of_focus as svc
        monkeypatch.setattr(svc, "get_area", lambda area_id: fake_area)

        def kaboom(**kw):
            raise RuntimeError("BLM down")
        monkeypatch.setattr("mining_os.services.plss_geocode.geocode_plss", kaboom)

        class FakeConn:
            def execute(self, stmt, params):
                sql = str(stmt)
                if "SELECT id, name FROM areas_of_focus WHERE plss_normalized" in sql:
                    return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
                return SimpleNamespace(rowcount=1)

        class FakeEngine:
            def begin(self):
                class CtxMgr:
                    def __enter__(inner_self):
                        return FakeConn()

                    def __exit__(inner_self, *args):
                        return False
                return CtxMgr()

        monkeypatch.setattr(svc, "get_engine", lambda: FakeEngine())

        result = svc.update_area_plss(42, "UT 13S 11W Sec 29", regeocode_coordinates=True)
        assert result["ok"] is True
        assert result["regeocoded"] is False
        assert result["latitude"] == fake_area["latitude"]


# ────────────────────────────────────────────────────────────────────
# API: /areas-of-focus/{id}/name
# ────────────────────────────────────────────────────────────────────

class TestNameEndpoints:
    def test_rejects_empty(self, client, monkeypatch, fake_area):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: fake_area,
        )
        r = client.post("/api/areas-of-focus/42/name", json={"name": "   "})
        assert r.status_code == 400

    def test_404_when_area_missing(self, client, monkeypatch):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: None,
        )
        r = client.post("/api/areas-of-focus/999/name", json={"name": "New Name"})
        assert r.status_code == 404

    def test_renames_successfully(self, client, monkeypatch, fake_area):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.get_area",
            lambda area_id: fake_area,
        )
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_name",
            lambda area_id, name: True,
        )
        r = client.post("/api/areas-of-focus/42/name", json={"name": "Spor Mountain (fixed)"})
        assert r.status_code == 200
        body = r.json()
        assert body == {"id": 42, "name": "Spor Mountain (fixed)"}


# ────────────────────────────────────────────────────────────────────
# API: /areas-of-focus/{id}/plss
# ────────────────────────────────────────────────────────────────────

class TestPlssEndpoints:
    def test_plss_update_happy_path(self, client, monkeypatch, fake_area):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_plss",
            lambda area_id, location_plss, regeocode_coordinates=True: {
                "ok": True,
                "location_plss": location_plss,
                "state_abbr": "UT",
                "township": "13S",
                "range": "11W",
                "section": "29",
                "meridian": "26",
                "latitude": 39.70,
                "longitude": -113.24,
                "regeocoded": regeocode_coordinates,
            },
        )
        r = client.post(
            "/api/areas-of-focus/42/plss",
            json={"location_plss": "UT 13S 11W Sec 29"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["latitude"] == 39.70
        assert body["regeocoded"] is True

    def test_plss_update_unparseable_returns_200_with_error(self, client, monkeypatch):
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_plss",
            lambda area_id, location_plss, regeocode_coordinates=True: {
                "ok": False,
                "error": "unparseable_plss",
                "location_plss": location_plss,
            },
        )
        r = client.post(
            "/api/areas-of-focus/42/plss",
            json={"location_plss": "garbage"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "unparseable_plss"

    def test_plss_update_honors_regeocode_false(self, client, monkeypatch):
        captured = {}

        def fake_update(area_id, location_plss, regeocode_coordinates=True):
            captured["regeocode"] = regeocode_coordinates
            return {"ok": True, "location_plss": location_plss, "regeocoded": False,
                    "latitude": 1.0, "longitude": 2.0}

        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_plss",
            fake_update,
        )
        r = client.post(
            "/api/areas-of-focus/42/plss",
            json={"location_plss": "UT 13S 11W Sec 29", "regeocode_coordinates": False},
        )
        assert r.status_code == 200
        assert captured["regeocode"] is False

    def test_plss_update_reaches_both_registered_routes(self, client, monkeypatch):
        """Both the toplevel ``@app.post("/api/...")`` and the ``api_app``-mounted
        route at the same URL should be reachable (belt-and-suspenders against
        mount regressions)."""
        monkeypatch.setattr(
            "mining_os.services.areas_of_focus.update_area_plss",
            lambda area_id, location_plss, regeocode_coordinates=True: {"ok": True},
        )
        r = client.post(
            "/api/areas-of-focus/42/plss",
            json={"location_plss": "UT 13S 11W Sec 29"},
        )
        assert r.status_code == 200
