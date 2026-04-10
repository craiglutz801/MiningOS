"""Parse and normalize PLSS strings for grouping (optionally uses Mining OS parser if installed)."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def _try_app_parse(plss: str, default_state: str) -> Optional[Dict[str, Any]]:
    try:
        from mining_os.services.blm_plss import parse_plss_string

        return parse_plss_string(plss.strip(), default_state=default_state)
    except Exception:
        return None


def _local_parse(plss: str, default_state: str) -> Optional[Dict[str, Any]]:
    s = plss.strip().upper()
    if not s:
        return None
    st = default_state.upper()[:2]
    m_state = re.match(r"^([A-Z]{2})\s+(.+)$", s)
    if m_state:
        st = m_state.group(1)
        s = m_state.group(2).strip()
    twp_m = re.search(r"T\s*(\d+)\s*([NS])\b", s, re.I)
    rng_m = re.search(r"R\s*(\d+)\s*([EW])\b", s, re.I)
    sec_m = re.search(r"(?:SEC\.?|S(?:EC)?)\s*(\d{1,2})\b", s, re.I) or re.search(
        r"\b(\d{1,2})\s*$", s
    )
    if not twp_m or not rng_m:
        return None
    twp = f"{int(twp_m.group(1)):04d}{twp_m.group(2).upper()}"
    rng = f"{int(rng_m.group(1)):04d}{rng_m.group(2).upper()}"
    sec = None
    if sec_m:
        sn = int(sec_m.group(1))
        if 1 <= sn <= 36:
            sec = str(sn).zfill(3)
    return {"state": st, "township": twp, "range": rng, "section": sec}


def parse_plss_components(plss: Optional[str], default_state: str = "UT") -> Optional[Dict[str, Any]]:
    if not plss or not str(plss).strip():
        return None
    raw = str(plss).strip()
    parsed = _try_app_parse(raw, default_state) or _local_parse(raw, default_state)
    if not parsed:
        return None
    state = (parsed.get("state") or default_state).strip().upper()[:2]
    twp = (parsed.get("township") or "").strip() or None
    rng = (parsed.get("range") or "").strip() or None
    sec = parsed.get("section")
    if sec is not None and sec != "":
        sec = str(sec).strip()
    else:
        sec = None
    if not twp or not rng:
        return None
    meridian = "26"
    try:
        from mining_os.services.fetch_claim_records import DEFAULT_MERIDIAN, STATE_MERIDIAN

        meridian = STATE_MERIDIAN.get(state, DEFAULT_MERIDIAN)
    except Exception:
        pass
    return {
        "state_abbr": state,
        "township": twp,
        "range": rng,
        "section": sec,
        "meridian": meridian,
    }


def normalize_plss_key(plss: Optional[str], default_state: str = "UT") -> Optional[str]:
    """Section-level key aligned with Mining OS plss_normalized when app parser is available."""
    if not plss or not str(plss).strip():
        return None
    comp = parse_plss_components(plss, default_state=default_state)
    if not comp:
        collapsed = re.sub(r"\s+", " ", str(plss).strip()).upper()
        return collapsed if collapsed else None
    state = comp["state_abbr"]
    twp = comp["township"]
    rng = comp["range"]
    sec = comp.get("section")
    parts = [state, twp, rng]
    if sec:
        parts.append(sec)
    return " ".join(parts)
