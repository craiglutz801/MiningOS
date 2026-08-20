"""Bridge matcher PLSS (CadNSDI / CSE_META ×10) to Mining OS section keys."""

from __future__ import annotations

import re
from typing import Any

from mining_os.services.areas_of_focus import _normalize_plss

# Meridians used by Fetch Claim Records / matcher payment path.
STATE_MERIDIAN = {"NV": "21", "UT": "26"}


def _decode_cse_tr(enc: str) -> str | None:
    """Decode CSE_META 4-digit×10 township/range (``0080S`` → ``8S``, ``0190N`` → ``19N``)."""
    m = re.match(r"^(\d{4})([NSEW])$", (enc or "").strip().upper())
    if not m:
        return None
    return f"{int(m.group(1)) // 10}{m.group(2)}"


def _human_tr(storage: str | None) -> str | None:
    """Convert ×10 CadNSDI storage to Mining OS display (``190N``/``0190N`` → ``19N``).

    - 4-digit CadNSDI (``0080S``, ``0280S``): always ÷10 → ``8S`` / ``28S``
    - 3+ digit ×10 intermediates (``190N``, ``800S``): ÷10 when ``>= 100`` and divisible by 10
    - Already-human values (``8S``, ``10N``, ``28S``, ``60W``): unchanged

    Note: bare ``80S`` (×10 for T8 without padding) is ambiguous with a literal T80S;
    callers should prefer 4-digit CadNSDI or humanize at the CadNSDI parse boundary
    (see ``reverse_geocode_plss`` / ``_decode_cse_tr``).
    """
    if not storage:
        return None
    s = str(storage).strip().upper()
    m = re.match(r"^(\d+)([NSEW])$", s)
    if not m:
        return s or None
    digits = m.group(1)
    num = int(digits)
    letter = m.group(2)
    if len(digits) >= 4:
        return f"{num // 10}{letter}"
    if num >= 100 and num % 10 == 0:
        return f"{num // 10}{letter}"
    return f"{num}{letter}"


def parse_cse_meta(meta: str | None) -> dict[str, Any] | None:
    """Parse first aliquot from BLM CSE_META (e.g. 'NV 21 0100N 0320E 009 A …')."""
    if not meta or not isinstance(meta, str):
        return None
    first = meta.split("|")[0].strip()
    parts = first.split()
    if len(parts) < 5:
        return None
    state = parts[0].upper()
    meridian = parts[1]
    twp_raw = _decode_cse_tr(parts[2]) or parts[2].upper()
    rng_raw = _decode_cse_tr(parts[3]) or parts[3].upper()
    twp = _human_tr(twp_raw)
    rng = _human_tr(rng_raw)
    sec_raw = parts[4]
    section = None
    if sec_raw.isdigit():
        n = int(sec_raw)
        if 1 <= n <= 36:
            section = str(n)
    if not state or not twp or not rng or not section:
        return None
    location = f"{state} T{twp} R{rng} Sec {section}"
    return {
        "state_abbr": state,
        "meridian": meridian or STATE_MERIDIAN.get(state),
        "township": twp,
        "range": rng,
        "section": section,
        "location_plss": location,
        "plss_source": "claim_cse_meta",
    }


def from_matcher_row(row: dict[str, Any], state_abbr: str) -> dict[str, Any] | None:
    """Build a Mining OS PLSS dict from a candidate site row (matcher columns)."""
    state = (row.get("state_abbr") or state_abbr or "").strip().upper()[:2] or state_abbr
    location = (row.get("location_plss") or "").strip() or None
    twp = _human_tr(row.get("township") or row.get("plss_township"))
    rng = _human_tr(row.get("range") or row.get("plss_range"))
    section = row.get("section") or row.get("plss_section")
    if section is not None:
        section = str(section).strip() or None
        if section and section.isdigit():
            section = str(int(section))
            if not (1 <= int(section) <= 36):
                section = None

    # Prefer rebuilt human location from components when available — avoids the
    # T80S bug where ×10 storage (80S) was pasted into location_plss as T80S.
    if twp and rng and section:
        location = f"{state} T{twp} R{rng} Sec {section}"
        plss_norm = _normalize_plss(location, default_state=state)
        if plss_norm:
            return {
                "state_abbr": state,
                "meridian": STATE_MERIDIAN.get(state, "26"),
                "township": twp,
                "range": rng,
                "section": section,
                "location_plss": location,
                "plss_normalized": plss_norm,
                "plss_source": row.get("plss_source") or "matcher_components",
                "plss_status": "resolved",
            }

    if location:
        plss_norm = _normalize_plss(location, default_state=state)
        if plss_norm and section:
            meridian = STATE_MERIDIAN.get(state, "26")
            return {
                "state_abbr": state,
                "meridian": meridian,
                "township": twp,
                "range": rng,
                "section": section,
                "location_plss": location,
                "plss_normalized": plss_norm,
                "plss_source": row.get("plss_source") or "matcher",
                "plss_status": "resolved",
            }
    return None


def resolve_site_plss(
    *,
    latitude: float | None,
    longitude: float | None,
    state_abbr: str,
    matcher_row: dict[str, Any] | None = None,
    cse_meta: str | None = None,
    use_network: bool = True,
) -> dict[str, Any]:
    """
    Prefer matcher / CSE_META (fast); CadNSDI reverse geocode only if still unresolved
    and use_network=True.
    """
    state = (state_abbr or "NV").upper()[:2]
    matcher_row = matcher_row or {}

    from_row = from_matcher_row(matcher_row, state)
    if from_row:
        return from_row

    cse = parse_cse_meta(cse_meta or matcher_row.get("cse_meta"))
    if cse:
        plss_norm = _normalize_plss(cse["location_plss"], default_state=cse["state_abbr"])
        if plss_norm:
            cse["plss_normalized"] = plss_norm
            cse["plss_status"] = "resolved"
            cse["township"] = _human_tr(cse.get("township"))
            cse["range"] = _human_tr(cse.get("range"))
            return cse

    if use_network and latitude is not None and longitude is not None:
        try:
            from mining_os.services.plss_geocode import reverse_geocode_plss

            geo = reverse_geocode_plss(float(latitude), float(longitude))
            if geo and geo.get("township") and geo.get("range") and geo.get("section"):
                twp = _human_tr(str(geo["township"]))
                rng = _human_tr(str(geo["range"]))
                section = str(geo["section"]).strip()
                st = (geo.get("state") or state).upper()[:2]
                location = geo.get("location_plss") or f"{st} T{twp} R{rng} Sec {section}"
                plss_norm = _normalize_plss(location, default_state=st)
                if plss_norm:
                    return {
                        "state_abbr": st,
                        "meridian": str(geo.get("meridian") or STATE_MERIDIAN.get(st, "26")),
                        "township": twp,
                        "range": rng,
                        "section": section,
                        "location_plss": location,
                        "plss_normalized": plss_norm,
                        "plss_source": "blm_cadastral_reverse",
                        "plss_status": "resolved",
                    }
        except Exception:
            pass

    return {
        "state_abbr": state,
        "meridian": STATE_MERIDIAN.get(state),
        "township": None,
        "range": None,
        "section": None,
        "location_plss": None,
        "plss_normalized": None,
        "plss_source": None,
        "plss_status": "unresolved",
    }
