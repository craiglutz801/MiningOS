"""Local JSON fixture adapter — reliable pilot ingest without fragile scraping."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from mining_os.tax_intel.adapters.base import RawTaxRecord, SourceArtifact, TaxSourceAdapter
from mining_os.tax_intel.adapters.csv_adapter import _float, _parse_date

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


class FixtureJsonAdapter(TaxSourceAdapter):
    """Reads packaged or configured JSON listing files for a county source."""

    def discover(self) -> list[str]:
        configured = self.config.get("fixture_path") or self.config.get("fixture_file")
        if configured:
            return [str(configured)]
        key = self.source_key or "unknown"
        path = FIXTURES_DIR / f"{key}.json"
        return [str(path)] if path.exists() else []

    def fetch(self, url: str) -> SourceArtifact:
        path = Path(url)
        if not path.is_absolute():
            path = FIXTURES_DIR / path
        content = path.read_bytes()
        return SourceArtifact(
            source_url=f"fixture://{path.name}",
            retrieved_at=datetime.now(timezone.utc),
            media_type="application/json",
            content=content,
            filename=path.name,
            metadata={"fixture": True},
        )

    def parse(self, artifact: SourceArtifact) -> Iterable[RawTaxRecord]:
        data = json.loads(artifact.content.decode("utf-8"))
        rows = data if isinstance(data, list) else data.get("records") or data.get("items") or []
        state = str(self.source.get("state") or "")
        county = str(self.source.get("county_name") or "")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            apn = row.get("apn") or row.get("primary_apn")
            key = str(row.get("source_record_key") or row.get("id") or apn or f"row-{i+1}")
            ms = row.get("mineral_survey_numbers") or row.get("ms_numbers") or []
            if isinstance(ms, str):
                ms = [x.strip() for x in ms.split(",") if x.strip()]
            commodities = row.get("commodities") or []
            if isinstance(commodities, str):
                commodities = [x.strip() for x in commodities.split(",") if x.strip()]
            yield RawTaxRecord(
                source_record_key=key,
                state=str(row.get("state") or state),
                county_name=str(row.get("county_name") or county),
                apn_raw=str(apn) if apn else None,
                owner_raw=row.get("owner") or row.get("owner_raw"),
                legal_description_raw=row.get("legal_description") or row.get("legal"),
                raw_status=row.get("status") or row.get("raw_status"),
                amount_due=_float(str(row["amount_due"]) if row.get("amount_due") is not None else None),
                minimum_bid=_float(str(row["minimum_bid"]) if row.get("minimum_bid") is not None else None),
                sale_date=_parse_date(str(row["sale_date"]) if row.get("sale_date") else None),
                property_address=row.get("property_address") or row.get("address"),
                acreage=_float(str(row["acreage"]) if row.get("acreage") is not None else None),
                latitude=_float(str(row["latitude"]) if row.get("latitude") is not None else None),
                longitude=_float(str(row["longitude"]) if row.get("longitude") is not None else None),
                best_name=row.get("best_name") or row.get("name") or (str(apn) if apn else key),
                patent_number=row.get("patent_number"),
                mineral_survey_numbers=list(ms),
                commodities=list(commodities),
                raw_payload=row,
            )
