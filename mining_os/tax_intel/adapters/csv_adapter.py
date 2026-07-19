"""CSV / TSV listing adapter."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from mining_os.tax_intel.adapters.base import RawTaxRecord, SourceArtifact, TaxSourceAdapter

USER_AGENT = "MiningOS-TaxIntel/1.0 (+https://github.com/local/Mining_OS)"


def _f(row: dict[str, str], *keys: str) -> str | None:
    lower = {k.lower().strip(): v for k, v in row.items() if k}
    for key in keys:
        v = lower.get(key.lower())
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _parse_date(v: str | None) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class CsvTaxAdapter(TaxSourceAdapter):
    def discover(self) -> list[str]:
        url = self.config.get("listing_url") or self.source.get("listing_url")
        return [str(url)] if url else []

    def fetch(self, url: str) -> SourceArtifact:
        now = datetime.now(timezone.utc)
        if url.startswith("file://") or url.startswith("/"):
            path = url.replace("file://", "")
            content = open(path, "rb").read()
            return SourceArtifact(
                source_url=url,
                retrieved_at=now,
                media_type="text/csv",
                content=content,
                filename=path.rsplit("/", 1)[-1],
            )
        r = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return SourceArtifact(
            source_url=url,
            retrieved_at=now,
            media_type=r.headers.get("Content-Type", "text/csv"),
            content=r.content,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or "listing.csv",
        )

    def parse(self, artifact: SourceArtifact) -> Iterable[RawTaxRecord]:
        text = artifact.content.decode("utf-8", errors="replace")
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",\t|;")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        state = str(self.source.get("state") or self.config.get("state") or "")
        county = str(self.source.get("county_name") or self.config.get("county_name") or "")
        for i, row in enumerate(reader):
            apn = _f(row, "apn", "parcel", "parcel_id", "parcelid", "account", "pin")
            key = _f(row, "id", "source_record_key", "record_id") or apn or f"row-{i+1}"
            legal = _f(row, "legal", "legal_description", "description")
            yield RawTaxRecord(
                source_record_key=str(key),
                state=state,
                county_name=county,
                apn_raw=apn,
                owner_raw=_f(row, "owner", "owner_name", "taxpayer"),
                legal_description_raw=legal,
                raw_status=_f(row, "status", "sale_status", "stage"),
                amount_due=_float(_f(row, "amount_due", "taxes_due", "amount", "delinquent_amount")),
                minimum_bid=_float(_f(row, "minimum_bid", "min_bid", "starting_bid")),
                sale_date=_parse_date(_f(row, "sale_date", "auction_date", "auction_start")),
                property_address=_f(row, "address", "property_address", "situs"),
                acreage=_float(_f(row, "acreage", "acres")),
                latitude=_float(_f(row, "latitude", "lat", "y")),
                longitude=_float(_f(row, "longitude", "lon", "lng", "x")),
                best_name=_f(row, "name", "claim_name", "property_name") or apn,
                patent_number=_f(row, "patent_number", "patent"),
                mineral_survey_numbers=[
                    x.strip()
                    for x in (_f(row, "mineral_survey", "ms", "ms_numbers") or "").split(",")
                    if x.strip()
                ],
                commodities=[
                    x.strip()
                    for x in (_f(row, "commodities", "minerals") or "").split(",")
                    if x.strip()
                ],
                raw_payload=dict(row),
            )
