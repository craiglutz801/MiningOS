"""Local JSON fixture adapter for reliable SITLA pilot ingest."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from mining_os.sitla_intel.adapters.base import RawSitlaRecord, SourceArtifact, SitlaSourceAdapter
from mining_os.tax_intel.adapters.csv_adapter import _float, _parse_date

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


class FixtureJsonAdapter(SitlaSourceAdapter):
    def discover(self) -> list[str]:
        configured = self.config.get("fixture_path") or self.config.get("fixture_file")
        if configured:
            return [str(configured)]
        key = self.source_key or "sitla_offerings"
        # Prefer source-specific fixture, fall back to offerings
        for name in (f"{key}.json", "sitla_offerings.json"):
            path = FIXTURES_DIR / name
            if path.exists():
                return [str(path)]
        return []

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

    def parse(self, artifact: SourceArtifact) -> Iterable[RawSitlaRecord]:
        data = json.loads(artifact.content.decode("utf-8"))
        rows = data if isinstance(data, list) else data.get("records") or data.get("items") or []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            key = str(row.get("source_record_key") or row.get("reference_number") or row.get("id") or f"row-{i+1}")
            coms = row.get("commodities") or []
            if isinstance(coms, str):
                coms = [x.strip() for x in coms.split(",") if x.strip()]
            yield RawSitlaRecord(
                source_record_key=key,
                title=str(row.get("title") or row.get("best_title") or key),
                reference_number=row.get("reference_number"),
                opportunity_type_raw=row.get("opportunity_type") or row.get("opportunity_type_raw"),
                status_raw=row.get("status") or row.get("status_raw"),
                county_name=row.get("county_name") or row.get("county"),
                commodity_raw=row.get("commodity") or row.get("commodity_raw"),
                commodities=list(coms),
                legal_description_raw=row.get("legal_description") or row.get("legal"),
                acreage=_float(str(row["acreage"]) if row.get("acreage") is not None else None),
                latitude=_float(str(row["latitude"]) if row.get("latitude") is not None else None),
                longitude=_float(str(row["longitude"]) if row.get("longitude") is not None else None),
                application_deadline=_parse_date(str(row["application_deadline"]) if row.get("application_deadline") else None),
                bidding_start_at=_parse_date(str(row.get("bidding_start") or row.get("bidding_start_at") or "") or None),
                bidding_end_at=_parse_date(str(row.get("bidding_end") or row.get("bidding_end_at") or "") or None),
                minimum_bid=_float(str(row["minimum_bid"]) if row.get("minimum_bid") is not None else None),
                winning_bid=_float(str(row["winning_bid"]) if row.get("winning_bid") is not None else None),
                winning_bidder=row.get("winning_bidder"),
                offering_cycle=row.get("offering_cycle"),
                detail_url=row.get("detail_url") or row.get("official_detail_url"),
                external_bid_url=row.get("external_bid_url"),
                raw_payload=row,
            )
