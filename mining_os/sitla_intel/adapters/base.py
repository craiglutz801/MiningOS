"""Typed SITLA source adapter contract (sync)."""

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
class RawSitlaRecord:
    source_record_key: str
    title: str
    reference_number: str | None = None
    opportunity_type_raw: str | None = None
    status_raw: str | None = None
    county_name: str | None = None
    commodity_raw: str | None = None
    commodities: list[str] = field(default_factory=list)
    legal_description_raw: str | None = None
    acreage: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    announcement_date: datetime | None = None
    application_deadline: datetime | None = None
    bidding_start_at: datetime | None = None
    bidding_end_at: datetime | None = None
    minimum_bid: float | None = None
    winning_bid: float | None = None
    winning_bidder: str | None = None
    offering_cycle: str | None = None
    detail_url: str | None = None
    external_bid_url: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


class SitlaSourceAdapter(ABC):
    source_key: str = ""

    def __init__(self, source: dict[str, Any]):
        self.source = source
        self.source_key = str(source.get("source_key") or "")
        self.config = dict(source.get("configuration_json") or {})

    @abstractmethod
    def discover(self) -> list[str]:
        ...

    @abstractmethod
    def fetch(self, url: str) -> SourceArtifact:
        ...

    @abstractmethod
    def parse(self, artifact: SourceArtifact) -> Iterable[RawSitlaRecord]:
        ...

    def validate(self, records: list[RawSitlaRecord]) -> list[str]:
        errors: list[str] = []
        for i, r in enumerate(records):
            if not r.source_record_key:
                errors.append(f"record[{i}]: missing source_record_key")
            if not r.title:
                errors.append(f"record[{i}]: missing title")
        return errors
