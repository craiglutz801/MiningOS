"""State and critical-mineral filters for the target pipeline."""

from __future__ import annotations

import re
from typing import Any, Optional

from target_pipeline.models import RawSourceRow

# Default focus states (overridable via TARGET_PIPELINE_STATES / TARGET_FOCUS_STATES in .env)
DEFAULT_TARGET_STATES = frozenset({"UT", "ID", "WY", "NV", "AZ", "MT"})

# Canonical mineral name (lowercase key) -> aliases to match in commodity text (lowercase)
TARGET_MINERALS: dict[str, list[str]] = {
    "tungsten": ["tungsten", "wolfram", "w"],
    "scandium": ["scandium", "sc"],
    "beryllium": ["beryllium", "be"],
    "uranium": ["uranium", "u3o8", "u"],
    "fluorspar": ["fluorspar", "fluorite", "f"],
    "germanium": ["germanium", "ge"],
    "gallium": ["gallium", "ga"],
}


def _title_mineral(canonical_lower: str) -> str:
    return canonical_lower.title()


def gather_commodity_text(raw: RawSourceRow) -> str:
    """Collect all commodity-related strings from a raw loader row for matching."""
    parts: list[str] = []
    cr = raw.get("commodity_raw")
    if cr is not None and str(cr).strip():
        parts.append(str(cr))
    r = raw.get("raw") or {}
    props = r.get("properties") if isinstance(r.get("properties"), dict) else {}
    if isinstance(props, dict):
        for k in (
            "commod1",
            "commod2",
            "commod3",
            "ore",
            "commodities",
            "mineral",
            "minerals",
            "commodity",
            "blm_prod",
            "cse_type_nr",
        ):
            v = props.get(k)
            if v is not None and str(v).strip() and str(v).strip().upper() not in ("NA", "N/A", "-"):
                parts.append(str(v))
    return " | ".join(parts)


def match_target_mineral(text: str) -> Optional[str]:
    """
    Return Title Case mineral name (e.g. Tungsten, Fluorspar) if any TARGET_MINERALS alias matches.
    Short aliases (1–2 chars) match as whole tokens only to reduce false positives.
    """
    if not text or not str(text).strip():
        return None
    blob = str(text).lower()
    tokens: set[str] = set()
    for chunk in re.split(r"[\s|;,/]+", blob):
        t = chunk.strip().strip(".").lower()
        if t:
            tokens.add(t)
    for m in re.finditer(r"\b[a-z]{1,3}\b", blob):
        tokens.add(m.group(0))

    for canonical, aliases in TARGET_MINERALS.items():
        title = _title_mineral(canonical)
        for al in aliases:
            a = al.lower()
            if len(a) <= 2:
                if a in tokens:
                    return title
            else:
                if a in blob:
                    return title
    return None
