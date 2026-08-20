"""Resolve or create section Targets for Active Mine Search candidates."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.services.areas_of_focus import _normalize_plss, upsert_area

log = logging.getLogger("mining_os.active_mine_intel.targets")


def find_target_by_plss(account_id: int, plss_normalized: str) -> dict[str, Any] | None:
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, name, plss_normalized, source, retrieval_type, state_abbr,
                       township, range, section, meridian, latitude, longitude
                FROM areas_of_focus
                WHERE account_id = :aid AND plss_normalized = :key
                LIMIT 1
                """
            ),
            {"aid": account_id, "key": plss_normalized},
        ).mappings().first()
    return dict(row) if row else None


def resolve_or_create_section_target(
    account_id: int,
    *,
    plss: dict[str, Any],
    mine_name: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    commodity: str | None = None,
) -> tuple[int, bool]:
    """
    Returns (area_of_focus_id, created).
    Reuses any existing section Target; creates with source=active_mine_plss when missing.
    """
    plss_norm = plss.get("plss_normalized") or _normalize_plss(
        plss.get("location_plss"), default_state=plss.get("state_abbr")
    )
    if not plss_norm or not plss.get("section"):
        raise ValueError("PLSS section key required to link a Target")

    existing = find_target_by_plss(account_id, plss_norm)
    if existing:
        return int(existing["id"]), False

    state = (plss.get("state_abbr") or "NV").upper()[:2]
    twp = plss.get("township") or ""
    rng = plss.get("range") or ""
    sec = plss.get("section") or ""
    label_mine = (mine_name or "").strip()
    if label_mine:
        name = f"{label_mine} — T{twp} R{rng} Sec {sec} ({state})"
    else:
        name = f"Active mine section — T{twp} R{rng} Sec {sec} ({state})"

    minerals: list[str] = []
    if commodity and isinstance(commodity, str):
        minerals = [c.strip() for c in commodity.replace(";", ",").split(",") if c.strip()][:8]

    area_id = upsert_area(
        name=name[:500],
        location_plss=plss.get("location_plss") or f"{state} T{twp} R{rng} Sec {sec}",
        latitude=latitude,
        longitude=longitude,
        minerals=minerals or None,
        source="active_mine_plss",
        retrieval_type="Known Mine",
        state_abbr=state,
        township=twp,
        range_val=rng,
        section=str(sec),
        meridian=str(plss.get("meridian") or ("21" if state == "NV" else "26")),
        status="unknown",
        account_id=account_id,
        skip_plss_geocode=latitude is not None and longitude is not None,
    )
    return int(area_id), True
