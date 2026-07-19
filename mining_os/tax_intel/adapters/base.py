"""Typed tax source adapter contract (sync; matches Mining OS style)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


@dataclass(frozen=True)
class SourceArtifact:
    source_url: str
    retrieved_at: datetime
    media_type: str
    content: bytes
    filename: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class RawTaxRecord:
    source_record_key: str
    state: str
    county_name: str
    apn_raw: str | None = None
    owner_raw: str | None = None
    legal_description_raw: str | None = None
    raw_status: str | None = None
    amount_due: float | None = None
    minimum_bid: float | None = None
    sale_date: datetime | None = None
    hearing_date: datetime | None = None
    property_address: str | None = None
    acreage: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    best_name: str | None = None
    patent_number: str | None = None
    mineral_survey_numbers: list[str] = field(default_factory=list)
    commodities: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)


class TaxSourceAdapter(ABC):
    """County adapters are thin configuration over reusable parsers."""

    source_key: str = ""

    def __init__(self, source: dict[str, Any]):
        self.source = source
        self.source_key = str(source.get("source_key") or "")
        self.config = dict(source.get("configuration_json") or {})

    @abstractmethod
    def discover(self) -> list[str]:
        """Return listing URLs / keys to fetch."""

    @abstractmethod
    def fetch(self, url: str) -> SourceArtifact:
        """Fetch one artifact. Never raise past adapter.run(); return empty content on soft fail."""

    @abstractmethod
    def parse(self, artifact: SourceArtifact) -> Iterable[RawTaxRecord]:
        """Parse artifact into raw tax records."""

    def validate(self, records: list[RawTaxRecord]) -> list[str]:
        errors: list[str] = []
        for i, r in enumerate(records):
            if not r.source_record_key:
                errors.append(f"record[{i}]: missing source_record_key")
            if not r.state or not r.county_name:
                errors.append(f"record[{i}]: missing state/county")
        return errors
