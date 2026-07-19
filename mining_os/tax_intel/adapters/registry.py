"""Resolve a source_registry row to a concrete adapter."""

from __future__ import annotations

from typing import Any

from mining_os.tax_intel.adapters.arcgis_adapter import ArcgisFeatureServerAdapter
from mining_os.tax_intel.adapters.base import TaxSourceAdapter
from mining_os.tax_intel.adapters.csv_adapter import CsvTaxAdapter
from mining_os.tax_intel.adapters.fixture_adapter import FixtureJsonAdapter
from mining_os.tax_intel.adapters.html_table import HtmlTableAdapter


def build_adapter(source: dict[str, Any]) -> TaxSourceAdapter:
    cfg = dict(source.get("configuration_json") or {})
    adapter_class = (
        source.get("adapter_class")
        or cfg.get("adapter_class")
        or ""
    ).strip()
    parser_kind = str(source.get("parser_kind") or "").upper()

    # Prefer explicit adapter, then fixture when configured / packaged.
    if adapter_class in ("FixtureJsonAdapter", "FIXTURE_JSON") or cfg.get("use_fixture"):
        return FixtureJsonAdapter(source)
    if adapter_class in ("ArcgisFeatureServerAdapter",) or parser_kind in (
        "ARC_GIS_FEATURE_SERVER",
        "ARC_GIS_MAP_SERVER",
    ):
        return ArcgisFeatureServerAdapter(source)
    if adapter_class in ("CsvTaxAdapter",) or parser_kind in ("CSV", "TSV", "XLSX"):
        return CsvTaxAdapter(source)
    if adapter_class in ("HtmlTableAdapter",) or parser_kind in (
        "HTML_TABLE",
        "HTML_DETAIL",
        "CIVICPLUS_PAGE",
        "CIVICPLUS_DOCUMENT_CENTER",
        "PUBLIC_NOTICE_PAGE",
        "AUCTION_PLATFORM",
    ):
        # Live HTML is opt-in; default pilot sources use fixtures for reliability.
        if cfg.get("allow_live_html"):
            return HtmlTableAdapter(source)
        return FixtureJsonAdapter(source)
    if parser_kind in ("MANUAL_UPLOAD", "PDF", "TEXT_PDF", "SCANNED_PDF") or source.get("manual_only"):
        return FixtureJsonAdapter(source)
    return FixtureJsonAdapter(source)
