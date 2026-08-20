"""Deterministic name/entity normalization and similarity scoring."""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

LEGAL_SUFFIXES = [
    "LLC",
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "LP",
    "LLP",
    "LTD",
]

GENERIC_MINE_SUFFIXES = [
    "MINE",
    "MINES",
    "PROJECT",
    "PIT",
    "QUARRY",
    "OPERATION",
    "OPERATIONS",
]

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _base_clean(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.upper()
    text = text.replace("&", " AND ")
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _strip_trailing_suffixes(text: str, suffixes: list[str]) -> str:
    tokens = text.split()
    while len(tokens) > 1 and tokens[-1] in suffixes:
        tokens = tokens[:-1]
    return " ".join(tokens)


def normalize_entity_name(value: str | None) -> str:
    """Normalize a company/operator/claimant name. Never mutates the source value."""
    if value is None:
        return ""
    text = _base_clean(str(value))
    if not text:
        return ""
    return _strip_trailing_suffixes(text, LEGAL_SUFFIXES)


def normalize_mine_name(value: str | None, strip_generic: bool = True) -> str:
    """Normalize a mine/claim/operation name for comparison."""
    if value is None:
        return ""
    text = _base_clean(str(value))
    if not text:
        return ""
    text = _strip_trailing_suffixes(text, LEGAL_SUFFIXES)
    if strip_generic:
        text = _strip_trailing_suffixes(text, GENERIC_MINE_SUFFIXES)
    return text


def normalize_commodity(value: str | None) -> str:
    if value is None:
        return ""
    return _base_clean(str(value)).title()


def similarity_score(a: str | None, b: str | None) -> float:
    """Deterministic 0-100 similarity between two already- or not-yet-normalized names."""
    left = normalize_mine_name(a)
    right = normalize_mine_name(b)
    if not left or not right:
        return 0.0
    return float(max(fuzz.token_set_ratio(left, right), fuzz.WRatio(left, right)))
