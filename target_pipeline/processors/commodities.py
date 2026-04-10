"""Map commodity / mineral aliases to canonical labels."""

from __future__ import annotations

import re
from typing import Optional

# Canonical output uses Title Case for compatibility with Mining OS mineral tags.
_ALIASES: dict[str, str] = {
    "au": "Gold",
    "gold": "Gold",
    "ag": "Silver",
    "silver": "Silver",
    "cu": "Copper",
    "copper": "Copper",
    "pb": "Lead",
    "lead": "Lead",
    "zn": "Zinc",
    "zinc": "Zinc",
    "u": "Uranium",
    "u3o8": "Uranium",
    "uranium": "Uranium",
    "w": "Tungsten",
    "tungsten": "Tungsten",
    "sc": "Scandium",
    "scandium": "Scandium",
    "be": "Beryllium",
    "beryllium": "Beryllium",
    "f": "Fluorspar",
    "fluorite": "Fluorspar",
    "fluorspar": "Fluorspar",
    "ge": "Germanium",
    "germanium": "Germanium",
    "mo": "Molybdenum",
    "molybdenum": "Molybdenum",
    "fe": "Iron",
    "iron": "Iron",
    "ree": "Rare Earth Elements",
    "reo": "Rare Earth Elements",
}


def canonical_commodity(raw: Optional[str]) -> Optional[str]:
    if raw is None or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    key = re.sub(r"\s+", " ", s.lower()).strip()
    key = re.sub(r"[,;].*$", "", key).strip()
    if key in _ALIASES:
        return _ALIASES[key]
    # Title-case unknowns (simple)
    return key.title() if key else None


def register_alias(alias: str, canonical: str) -> None:
    _ALIASES[alias.strip().lower()] = canonical.strip()
