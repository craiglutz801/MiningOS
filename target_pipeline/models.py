"""Typed shapes for raw, standardized, and scored targets."""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict


class RawSourceRow(TypedDict, total=False):
    source: str
    name: str
    state: str
    county: str
    commodity_raw: str
    plss_raw: str
    latitude: float
    longitude: float
    reports: list[str]
    status: str
    record_type: Literal["claim", "deposit"]
    raw: dict[str, Any]


class StandardRecord(TypedDict, total=False):
    source: str
    record_type: Literal["claim", "deposit"]
    raw_name: str
    normalized_name: str
    state: Optional[str]
    county: Optional[str]
    commodity: Optional[str]
    plss: Optional[str]
    plss_normalized: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    reports: list[str]
    status: Optional[str]
    review_flags: list[str]
    raw: dict[str, Any]


class TargetGroup(TypedDict, total=False):
    plss: str
    plss_normalized: Optional[str]
    commodity: Optional[str]
    state: Optional[str]
    county: Optional[str]
    deposits: list[StandardRecord]
    claims: list[StandardRecord]
    target_name: str
    deposit_names: list[str]
    claim_ids: list[str]
    report_links: list[str]
    source_count: int
    has_report: bool
    score: int
    score_notes: list[str]
