"""Normalization helpers for APN, owner, legal description, PLSS, Mineral Survey."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_WS = re.compile(r"\s+")
_APN_KEEP = re.compile(r"[^A-Z0-9]")
_MS = re.compile(
    r"\b(?:MS|M\.?S\.?|MINERAL\s+SURVEY)\s*#?\s*(\d{1,6}[A-Z]?)\b",
    re.IGNORECASE,
)
_PATENT = re.compile(
    r"\b(?:PAT(?:ENT)?\.?\s*(?:NO\.?|#)?|ACCESSION)\s*[:#]?\s*([A-Z0-9\-]{4,20})\b",
    re.IGNORECASE,
)
_PLSS = re.compile(
    r"\bT(?:ownship)?\.?\s*(\d{1,3})\s*([NS])\b"
    r".{0,24}?"
    r"\bR(?:ange)?\.?\s*(\d{1,3})\s*([EW])\b"
    r"(?:.{0,40}?\b(?:Sec(?:tion)?|S)\.?\s*(\d{1,2})\b)?",
    re.IGNORECASE,
)


def normalize_apn(raw: str | None) -> str | None:
    if not raw:
        return None
    s = _APN_KEEP.sub("", str(raw).upper())
    return s or None


def normalize_owner(raw: str | None) -> str | None:
    if not raw:
        return None
    s = _WS.sub(" ", str(raw).strip().upper())
    for junk in (" ET AL", " ETUX", " ET VIR", " LLC", " L.L.C.", " INC", " INC."):
        s = s.replace(junk, "")
    return s.strip() or None


def normalize_ms_number(raw: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(raw).upper())


def extract_mineral_surveys(legal: str | None) -> list[str]:
    if not legal:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _MS.finditer(legal):
        n = normalize_ms_number(m.group(1))
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract_patent_number(legal: str | None) -> str | None:
    if not legal:
        return None
    m = _PATENT.search(legal)
    return m.group(1).upper() if m else None


def extract_plss(legal: str | None) -> dict[str, str | None]:
    empty = {"township": None, "range": None, "section": None, "plss_key": None}
    if not legal:
        return empty
    m = _PLSS.search(legal)
    if not m:
        return empty
    t_num, t_dir, r_num, r_dir, sec = m.groups()
    twp = f"{int(t_num)}{t_dir.upper()}"
    rng = f"{int(r_num)}{r_dir.upper()}"
    section = f"{int(sec):02d}" if sec else None
    key = f"T{twp} R{rng}" + (f" Sec {section}" if section else "")
    return {"township": twp, "range": rng, "section": section, "plss_key": key}


def map_lifecycle(raw_status: str | None, publication_scope: str | None = None) -> str:
    s = (raw_status or "").strip().upper()
    mapping = {
        "AUCTION": "AUCTION_SCHEDULED",
        "AUCTION SCHEDULED": "AUCTION_SCHEDULED",
        "SCHEDULED": "AUCTION_SCHEDULED",
        "SALE": "SALE_ELIGIBLE",
        "SALE ELIGIBLE": "SALE_ELIGIBLE",
        "TAX SALE": "SALE_ELIGIBLE",
        "DELINQUENT": "DELINQUENT",
        "PENDING": "PENDING_TAX_DEED",
        "PENDING TAX DEED": "PENDING_TAX_DEED",
        "TAX DEED": "TAX_DEED_ISSUED",
        "TRUSTEE": "COUNTY_OR_TRUSTEE_HELD",
        "COUNTY HELD": "COUNTY_OR_TRUSTEE_HELD",
        "NOTICE": "NOTICE_PUBLISHED",
        "REDEEMED": "REDEEMED",
        "WITHDRAWN": "WITHDRAWN",
        "SOLD": "SOLD",
    }
    for k, v in mapping.items():
        if k in s:
            return v
    scope = (publication_scope or "").upper()
    scope_map = {
        "AUCTION_ONLY": "AUCTION_SCHEDULED",
        "SALE_ELIGIBLE_ONLY": "SALE_ELIGIBLE",
        "PENDING_TAX_DEED": "PENDING_TAX_DEED",
        "TAX_DEEDED_ONLY": "TAX_DEED_ISSUED",
        "TRUST_INVENTORY": "COUNTY_OR_TRUSTEE_HELD",
        "DELINQUENT_SUBSET": "DELINQUENT",
        "ALL_UNPAID": "DELINQUENT",
    }
    return scope_map.get(scope, "DISCOVERED")


def canonical_key(state: str, county: str, apn: str | None, source_record_key: str) -> str:
    apn_n = normalize_apn(apn) or "NOAPN"
    base = f"{state.upper()}:{county.strip().upper()}:{apn_n}:{source_record_key}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:40]


def record_hash(payload: dict[str, Any]) -> str:
    import json

    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
