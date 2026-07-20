"""Normalization helpers for SITLA legal descriptions, lifecycle, and identity."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Reuse Tax Sales PLSS extraction patterns
from mining_os.tax_intel.normalize import extract_plss as _extract_plss

_WS = re.compile(r"\s+")


def extract_plss(legal: str | None) -> dict[str, str | None]:
    return _extract_plss(legal)


def map_opportunity_type(raw: str | None) -> str:
    s = (raw or "").strip().upper()
    mapping = [
        ("COMPETING APPLICATION", "COMPETING_APPLICATION_NOTICE"),
        ("SAND AND GRAVEL", "SAND_GRAVEL_PERMIT"),
        ("MINERAL MATERIAL", "MINERAL_MATERIAL_PERMIT"),
        ("METALLIFEROUS", "METALLIFEROUS_MINERAL_LEASE"),
        ("INDUSTRIAL", "INDUSTRIAL_MINERAL_LEASE"),
        ("OIL AND GAS", "OIL_GAS_MINERAL_LEASE"),
        ("OIL & GAS", "OIL_GAS_MINERAL_LEASE"),
        ("LITHIUM", "LITHIUM_LEASE"),
        ("POTASH", "POTASH_LEASE"),
        ("PHOSPHATE", "PHOSPHATE_LEASE"),
        ("HELIUM", "HELIUM_LEASE"),
        ("COAL", "COAL_LEASE"),
        ("GEOTHERMAL", "GEOTHERMAL_ARRANGEMENT"),
        ("OTHER BUSINESS", "OTHER_BUSINESS_ARRANGEMENT"),
        ("NOMINATION", "LAND_NOMINATION"),
        ("REOFFER", "REOFFERING"),
        ("COMPETITIVE", "COMPETITIVE_MINERAL_LEASE"),
        ("MINERAL LEASE", "COMPETITIVE_MINERAL_LEASE"),
    ]
    for needle, code in mapping:
        if needle in s:
            return code
    return "UNKNOWN"


def map_lifecycle(raw_status: str | None) -> str:
    s = (raw_status or "").strip().upper()
    mapping = [
        ("BIDDING OPEN", "BIDDING_OPEN"),
        ("OPEN FOR BID", "BIDDING_OPEN"),
        ("BIDDING CLOSED", "BIDDING_CLOSED"),
        ("COMPETING APPLICATION", "COMPETING_APPLICATION_OPEN"),
        ("PUBLIC NOTICE", "PUBLIC_NOTICE_OPEN"),
        ("NOMINATION", "NOMINATION_OPEN"),
        ("SCHEDULED", "SCHEDULED"),
        ("AWARDED", "AWARDED"),
        ("NO BID", "NO_BID"),
        ("NOT AWARDED", "NOT_AWARDED"),
        ("WITHDRAWN", "WITHDRAWN"),
        ("CANCELLED", "CANCELLED"),
        ("CANCELED", "CANCELLED"),
        ("EXPIRED", "EXPIRED"),
        ("REOFFER", "REOFFERED"),
        ("ANNOUNCED", "ANNOUNCED"),
        ("UNDER REVIEW", "UNDER_REVIEW"),
        ("LEASE ACTIVE", "LEASE_ACTIVE"),
    ]
    for needle, code in mapping:
        if needle in s:
            return code
    return "DISCOVERED"


def canonical_key(
    reference: str | None,
    county: str | None,
    legal: str | None,
    cycle: str | None,
    source_record_key: str,
) -> str:
    base = "|".join(
        [
            (reference or "").strip().upper(),
            (county or "").strip().upper(),
            _WS.sub(" ", (legal or "").strip().upper())[:200],
            (cycle or "").strip().upper(),
            source_record_key.strip(),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:40]


def record_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
