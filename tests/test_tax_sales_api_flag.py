"""Tax Sales API stays inert when the feature flag is off."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_tax_sales_meta_reports_disabled_by_default(monkeypatch):
    monkeypatch.setattr("mining_os.config.settings.ENABLE_TAX_SALES_API", False)
    from mining_os.api.main import api_app

    client = TestClient(api_app)
    # Meta is public-ish on api_app; auth middleware is on outer app.
    res = client.get("/tax-sales/meta")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["enabled"] is False


def test_tax_sales_summary_disabled_payload(monkeypatch):
    monkeypatch.setattr("mining_os.config.settings.ENABLE_TAX_SALES_API", False)
    from mining_os.api.main import api_app

    client = TestClient(api_app)
    res = client.get("/tax-sales/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["enabled"] is False
    assert "disabled" in (body.get("error") or "").lower()
