"""SITLA adapter + normalize smoke tests (no DB / network)."""

from __future__ import annotations

from mining_os.sitla_intel.adapters.fixture_adapter import FixtureJsonAdapter
from mining_os.sitla_intel.adapters.registry import build_adapter
from mining_os.sitla_intel.normalize import extract_plss, map_lifecycle


def test_fixture_adapter_parses_offerings():
    source = {
        "source_key": "sitla_fixture_offerings",
        "listing_url": "fixture://sitla_offerings.json",
        "parser_kind": "FIXTURE_JSON",
        "configuration_json": {"use_fixture": True, "fixture_file": "sitla_offerings.json"},
    }
    adapter = FixtureJsonAdapter(source)
    urls = adapter.discover()
    assert urls
    artifact = adapter.fetch(urls[0])
    records = list(adapter.parse(artifact))
    assert len(records) >= 2
    assert records[0].reference_number
    assert records[0].county_name


def test_registry_prefers_fixture_unless_live_html():
    fixture_src = {
        "source_key": "sitla_energy_minerals_hub",
        "parser_kind": "HTML_HUB",
        "listing_url": "https://trustlands.utah.gov/work-with-us/energy-minerals/",
        "configuration_json": {"use_fixture": True, "allow_live_html": False},
    }
    assert build_adapter(fixture_src).__class__.__name__ == "FixtureJsonAdapter"

    live_src = {
        **fixture_src,
        "configuration_json": {"allow_live_html": True},
    }
    assert build_adapter(live_src).__class__.__name__ == "HtmlHubAdapter"


def test_extract_plss_and_lifecycle():
    plss = extract_plss("T12S R2W Sec 16 SLM — school section")
    assert plss.get("township")
    assert plss.get("range")
    assert map_lifecycle("Awarded") == "AWARDED"
