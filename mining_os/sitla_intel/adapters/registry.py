"""Resolve a sitla_intel.sources row to a concrete adapter."""

from __future__ import annotations

from typing import Any

from mining_os.sitla_intel.adapters.base import SitlaSourceAdapter
from mining_os.sitla_intel.adapters.fixture_adapter import FixtureJsonAdapter
from mining_os.sitla_intel.adapters.html_hub import HtmlHubAdapter


def build_adapter(source: dict[str, Any]) -> SitlaSourceAdapter:
    cfg = dict(source.get("configuration_json") or {})
    adapter_class = (source.get("adapter_class") or cfg.get("adapter_class") or "").strip()
    parser_kind = str(source.get("parser_kind") or "").upper()

    if adapter_class in ("HtmlHubAdapter",) or (
        cfg.get("allow_live_html")
        and parser_kind in ("HTML_HUB", "HTML_INDEX", "HTML_TABLE", "PUBLIC_NOTICE_PAGE")
    ):
        return HtmlHubAdapter(source)
    if adapter_class in ("FixtureJsonAdapter", "FIXTURE_JSON") or cfg.get("use_fixture") or parser_kind in (
        "FIXTURE_JSON",
        "MANUAL_UPLOAD",
        "PDF",
    ):
        return FixtureJsonAdapter(source)
    # Default: fixtures for reliability; live HTML only when explicitly allowed.
    return FixtureJsonAdapter(source)
