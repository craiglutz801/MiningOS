"""Tax Sales Phase 3+ — adapters, normalize, jobs flag gating."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_normalize_legal_extracts_ms_and_plss():
    from mining_os.tax_intel.normalize import (
        canonical_key,
        extract_mineral_surveys,
        extract_plss,
        map_lifecycle,
        normalize_apn,
    )

    legal = "MS 4127 T27S R11W Sec 14 Salt Lake Meridian patented mining claim"
    assert extract_mineral_surveys(legal) == ["4127"]
    plss = extract_plss(legal)
    assert plss["township"] == "27S"
    assert plss["range"] == "11W"
    assert plss["section"] == "14"
    assert normalize_apn("01-0123-0001") == "0101230001"
    assert map_lifecycle("AUCTION SCHEDULED") == "AUCTION_SCHEDULED"
    assert len(canonical_key("UT", "Beaver", "01-0123", "row-1")) == 40


def test_fixture_adapter_parses_pilot_county():
    from mining_os.tax_intel.adapters.fixture_adapter import FixtureJsonAdapter

    source = {
        "source_key": "ut_beaver_tax_sale",
        "state": "UT",
        "county_name": "Beaver",
        "configuration_json": {"use_fixture": True, "fixture_file": "ut_beaver_tax_sale.json"},
    }
    adapter = FixtureJsonAdapter(source)
    urls = adapter.discover()
    assert urls
    artifact = adapter.fetch(urls[0])
    records = list(adapter.parse(artifact))
    assert len(records) >= 1
    assert records[0].state == "UT"
    assert records[0].apn_raw


def test_build_adapter_prefers_fixture_for_html_pilot():
    from mining_os.tax_intel.adapters.registry import build_adapter
    from mining_os.tax_intel.adapters.fixture_adapter import FixtureJsonAdapter

    adapter = build_adapter(
        {
            "source_key": "ut_beaver_tax_sale",
            "parser_kind": "HTML_TABLE",
            "configuration_json": {"use_fixture": True},
        }
    )
    assert isinstance(adapter, FixtureJsonAdapter)


def test_refresh_requires_jobs_or_admin_flag(monkeypatch):
    monkeypatch.setattr("mining_os.config.settings.ENABLE_TAX_SALES_API", True)
    monkeypatch.setattr("mining_os.config.settings.ENABLE_TAX_SALES_JOBS", False)
    monkeypatch.setattr("mining_os.config.settings.ENABLE_TAX_SALES_ADMIN", False)
    from mining_os.api.main import api_app

    client = TestClient(api_app)
    res = client.post("/tax-sales/jobs/refresh")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "ENABLE_TAX_SALES" in (body.get("error") or "")


def test_jobs_tick_noop_when_disabled(monkeypatch):
    monkeypatch.setattr("mining_os.config.settings.ENABLE_TAX_SALES_API", False)
    monkeypatch.setattr("mining_os.config.settings.ENABLE_TAX_SALES_JOBS", True)
    from mining_os.tax_intel.jobs import tick_tax_sales_jobs

    # Must not raise
    tick_tax_sales_jobs()


def test_fixture_files_exist_for_nine_pilot_counties():
    root = Path(__file__).resolve().parents[1] / "mining_os" / "tax_intel" / "fixtures"
    keys = [
        "ut_beaver_tax_sale",
        "ut_juab_tax_sale",
        "ut_tooele_tax_sale",
        "id_shoshone_tax_deed",
        "id_custer_pending_deed",
        "id_lemhi_property",
        "nv_white_pine_tax_sale",
        "nv_nye_tax_sale",
        "nv_elko_trustee",
    ]
    for key in keys:
        assert (root / f"{key}.json").exists(), key
