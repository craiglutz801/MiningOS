"""
Query BLM MLRS mining claims by PLSS (Township, Range, Section).
Uses the same ArcGIS REST API as BLM_ClaimAgent get_mlrs_from_PLSS.py.
Payment status is not fetched here; use blm_check.check_payment_status(serial_number) for that.
"""

from __future__ import annotations

import json
import re
import time
import logging
from typing import List

import requests

log = logging.getLogger("mining_os.blm_plss")

BASE_API_URL = "https://gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Mining_Claims_Not_Closed/FeatureServer/0/query"
DEFAULT_MERIDIAN = "26"


def _blm_request_with_retry(params: dict, retries: int = 2) -> dict | None:
    """GET the MLRS FeatureServer with retry on transient 500s and network errors."""
    backoff = 0.4
    for attempt in range(retries + 1):
        try:
            resp = requests.get(BASE_API_URL, params=params, timeout=30)
            resp.raise_for_status()
            body = (resp.text or "").strip()
            if not body:
                return None
            data = json.loads(body)
            if not isinstance(data, dict):
                return None
        except (json.JSONDecodeError, ValueError, requests.RequestException) as e:
            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2
                continue
            log.warning("BLM MLRS query failed: %s", e)
            return None

        err = data.get("error")
        if err:
            if attempt < retries and isinstance(err, dict) and err.get("code") == 500:
                time.sleep(backoff)
                backoff *= 2
                continue
            log.warning("BLM MLRS API error: %s", err)
            return None
        return data
    return None


def _encode_tr_value(value: str | None, directions: str) -> str | None:
    """
    Encode a township or range value to BLM's 4-digit `×10` format.

    Handles both conventions seen in the DB:
      - 4-char already-encoded: '0280S', '0080N' (pass through, zero-padded)
      - 1-3 char human form:    '28S', '8N', '149N' (multiply by 10, pad)

    ``directions`` should be 'NS' for township or 'EW' for range so the regex
    rejects the wrong orientation.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip().upper().replace(" ", "")
    m = re.match(rf"^(\d+)\s*([{directions}])$", s)
    if not m:
        return s
    num_str, direction = m.groups()
    if len(num_str) >= 4:
        return num_str.zfill(4) + direction
    try:
        n = int(num_str) * 10
        return str(n).zfill(4) + direction
    except (ValueError, TypeError):
        return num_str.zfill(4) + direction


def _normalize_township(value: str | None) -> str | None:
    """
    e.g. '28S' -> '0280S', '8N' -> '0080N', '0280S' -> '0280S'.

    See ``_encode_tr_value`` for the dual-convention rationale. This is the
    format expected by BLM MLRS CSE_META prefixes.
    """
    return _encode_tr_value(value, "NS")


def _normalize_range(value: str | None) -> str | None:
    """e.g. '14E' -> '0140E', '0140E' -> '0140E'."""
    return _encode_tr_value(value, "EW")


def _normalize_section(value: str | None) -> str | None:
    """1-36 -> 001, 023, etc."""
    if value is None or value == "":
        return None
    s = str(value).strip()
    if not s.isdigit():
        return None
    num = int(s)
    if 1 <= num <= 36:
        return str(num).zfill(3)
    return None


def normalize_plss_field(value: str | None, kind: str) -> str | None:
    """
    Robust per-field normalizer for user-entered PLSS components.

    Unlike ``parse_plss_string`` (which expects a full PLSS string and bails
    when a leading "T"/"R" prefixes a field), this strips the common labels
    (``T``, ``Twp``, ``Township``, ``R``, ``Rng``, ``Range``, ``Sec``,
    ``Section`` + their long-form direction words) and normalizes to the
    BLM-encoded format used everywhere else in the codebase.

    Accepts inputs like ``T12S``, ``12S``, ``Township 12 South``, ``t. 12 s``,
    ``0120S``, ``12 s``. Returns:
      - township/range: 4-digit ×10 encoded form like ``0120S`` / ``0120W``
      - section: zero-padded 3-digit like ``035``
      - state: 2-letter upper like ``UT``
      - meridian: 2-digit string like ``26`` (or the raw digits if provided)

    Returns ``None`` for unparseable / empty input.
    ``kind`` must be one of ``"township" | "range" | "section" | "state" | "meridian"``.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    if kind == "state":
        s = re.sub(r"[^A-Za-z]", "", s).upper()
        return s[:2] if len(s) >= 2 else None

    if kind == "meridian":
        digits = re.sub(r"\D", "", s)
        if not digits:
            return None
        return digits.zfill(2)[:2]

    up = s.upper()
    up = re.sub(r"\bNORTH\b", "N", up)
    up = re.sub(r"\bSOUTH\b", "S", up)
    up = re.sub(r"\bEAST\b", "E", up)
    up = re.sub(r"\bWEST\b", "W", up)

    if kind == "section":
        up = re.sub(r"\b(SECTION|SEC)\b\.?", "", up)
        digits = re.sub(r"\D", "", up)
        return _normalize_section(digits) if digits else None

    if kind == "township":
        up = re.sub(r"\b(?:TOWNSHIP|TWP)\b\.?", "", up)
        up = re.sub(r"^\s*T\s*\.?\s*(?=\d)", "", up)
        up = re.sub(r"[\s\.]+", "", up)
        m = re.fullmatch(r"(\d+)([NS])", up)
        if not m:
            return None
        return _normalize_township(m.group(1) + m.group(2))

    if kind == "range":
        up = re.sub(r"\b(?:RANGE|RNG)\b\.?", "", up)
        up = re.sub(r"^\s*R\s*\.?\s*(?=\d)", "", up)
        up = re.sub(r"[\s\.]+", "", up)
        m = re.fullmatch(r"(\d+)([EW])", up)
        if not m:
            return None
        return _normalize_range(m.group(1) + m.group(2))

    return None


def parse_plss_string(plss: str, default_state: str = "UT") -> dict | None:
    """
    Smart PLSS parser — extracts state, township, range, section from many formats.

    Handled formats include:
      T30S R18W Sec10, T30S R18W Sec 10, T28S R11W S18, T4S R13W S2
      T. 30 S., R. 18 W., Sec. 10
      Township 30 South Range 18 West Section 10
      Twp 30S Rng 18W Sec 10
      30S 18W 10, 12N 57E 23
      T30S-R18W-S10, T30S/R18W/S10
      UT T28S R11W S18 (state prefix)
      NE1/4 Sec 10 T30S R18W (quarter-section stripped)
      Sec 10, T30S, R18W (any order)

    Returns dict with state, township, range, section (section may be None).
    """
    if not plss or not isinstance(plss, str):
        return None
    raw = plss.strip()
    if not raw:
        return None

    s = raw.upper()

    # ── 1. Extract optional 2-letter state prefix ──
    state = (default_state or "UT").upper()[:2]
    st_m = re.match(r"^([A-Z]{2})\s*[,;\-]?\s+(.+)", s)
    if st_m and re.search(r"\d", st_m.group(2)):
        state = st_m.group(1)
        s = st_m.group(2).strip()

    # ── 2. Strip quarter-section notation (NE1/4, SW1/2, etc.) ──
    s = re.sub(r"\b[NS]?[EW]?\s*\d\s*/\s*\d\b", " ", s)

    # ── 3. Normalize verbose labels (keep SEC distinct from S-for-South) ──
    s = re.sub(r"\bTOWNSHIP\b", "T", s)
    s = re.sub(r"\bTWP\.?\b", "T", s)
    s = re.sub(r"\bRANGE\b", "R", s)
    s = re.sub(r"\bRNG\.?\b", "R", s)
    s = re.sub(r"\bSECTION\b", "SEC", s)
    s = re.sub(r"\bNORTH\b", "N", s)
    s = re.sub(r"\bSOUTH\b", "S", s)
    s = re.sub(r"\bEAST\b", "E", s)
    s = re.sub(r"\bWEST\b", "W", s)
    s = s.replace(".", "")

    township = range_val = section = None

    # ── 4. Extract section FIRST (before stripping "SEC"/"S" labels) ──
    # "SEC" is unambiguous — always means section
    sec_m = re.search(r"\bSEC\s*\.?\s*(\d{1,2})\b", s)
    if sec_m:
        section = _normalize_section(sec_m.group(1))
        s = s[:sec_m.start()] + " " + s[sec_m.end():]
    else:
        # Standalone "S" before 1-2 digits — not part of township "30S".
        # Allow delimiters (space, hyphen, slash, comma, start) before S.
        for sm in re.finditer(r"(?:^|(?<=[\s,;\-/]))S\s?(\d{1,2})(?=[\s,;\-/]|$)", s):
            candidate = int(sm.group(1))
            if 1 <= candidate <= 36:
                section = _normalize_section(sm.group(1))
                s = s[:sm.start()] + " " + s[sm.end():]
                break

    # ── 5. Extract township: T + digits + N/S ──
    twp_m = re.search(r"\bT\s*(\d+)\s*([NS])\b", s)
    if twp_m:
        township = _normalize_township(twp_m.group(1) + twp_m.group(2))

    # ── 6. Extract range: R + digits + E/W ──
    rng_m = re.search(r"\bR\s*(\d+)\s*([EW])\b", s)
    if rng_m:
        range_val = _normalize_range(rng_m.group(1) + rng_m.group(2))

    # ── 7. Fallback: bare number+direction (no T/R prefix) ──
    # Normalize delimiters to spaces for fallback scanning
    rest = re.sub(r"[,;\-\t/]+", " ", s)
    rest = re.sub(r"\s+", " ", rest).strip()
    # Merge "30 S" → "30S", "18 W" → "18W"
    rest = re.sub(r"(\d+)\s+([NS])(?=\s|$)", r"\1\2", rest)
    rest = re.sub(r"(\d+)\s+([EW])(?=\s|$)", r"\1\2", rest)

    if not township:
        for m in re.finditer(r"(\d+)([NS])(?=\s|\d|$)", rest):
            township = _normalize_township(m.group(1) + m.group(2))
            break
    if not range_val:
        for m in re.finditer(r"(\d+)([EW])(?=\s|\d|$)", rest):
            range_val = _normalize_range(m.group(1) + m.group(2))
            break

    # ── 8. Section fallback: bare number 1-36 not yet consumed ──
    if section is None and township and range_val:
        # Only consider tokens that are PURE digits (no direction letter attached).
        # Tokens like "30S" or "18W" are township/range — skip them.
        for tok in re.split(r"\s+", rest):
            tok = tok.strip()
            if tok.isdigit():
                n = int(tok)
                if 1 <= n <= 36:
                    section = _normalize_section(str(n))
                    break
        if section is None:
            # Allow trailing section digits to follow a direction letter too,
            # so compact forms like "12S18W10" yield section=010.
            trail = re.search(r"(?:^|(?<=\s)|(?<=[NSEW]))(\d{1,2})$", rest)
            if trail and 1 <= int(trail.group(1)) <= 36:
                section = _normalize_section(trail.group(1))

    # ── 9. Last resort: three bare numbers → T(S) R(W) Section ──
    if not township or not range_val:
        nums = [int(x) for x in re.findall(r"\b(\d+)\b", rest)]
        if len(nums) >= 2:
            if not township:
                township = _normalize_township(f"{nums[0]}S")
            if not range_val:
                range_val = _normalize_range(f"{nums[1]}W")
            if section is None and len(nums) >= 3 and 1 <= nums[2] <= 36:
                section = _normalize_section(str(nums[2]))

    if township and range_val:
        return {"state": state, "township": township, "range": range_val, "section": section}
    return None


def query_claims_by_plss(
    state: str,
    township: str,
    range_val: str,
    section: str | None = None,
    meridian: str = DEFAULT_MERIDIAN,
) -> List[dict]:
    """
    Query BLM API for mining claims in the given PLSS location.
    township/range_val should be normalized (e.g. 0120S, 0140E). section optional (001-036).
    Returns list of dicts with: claim_name, serial_number, case_page, payment_report, state_abbr, plss, geometry.
    """
    state = (state or "UT").strip().upper()
    meridian = str(meridian or DEFAULT_MERIDIAN).strip()
    township = _normalize_township(township) or township
    range_val = _normalize_range(range_val) or range_val

    if section is not None:
        sec = _normalize_section(section)
        if not sec:
            return []
        mtrs_prefix = f"{state} {meridian} {township} {range_val} {sec}"
        where = f"CSE_META LIKE '{mtrs_prefix}%'"
    else:
        mtrs_prefix = f"{state} {meridian} {township} {range_val}"
        where = f"CSE_META LIKE '{mtrs_prefix} %'"

    params = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }
    data = _blm_request_with_retry(params)
    if data is None:
        return []
    return _extract_claims_from_response(data, state)


def query_claims_by_coords(
    lat: float,
    lon: float,
    radius_meters: int = 2000,
) -> List[dict]:
    """
    Spatial query: find BLM mining claims within `radius_meters` of (lat, lon).
    Uses the same ArcGIS FeatureServer with a geometry envelope query.
    """
    # Build a bounding box envelope in WGS84 around the point
    # ~0.00001° ≈ 1.1m at equator; 2000m ≈ 0.018° lat, wider for lon at higher latitudes
    import math
    deg_lat = radius_meters / 111_320.0
    deg_lon = radius_meters / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    envelope = f"{lon - deg_lon},{lat - deg_lat},{lon + deg_lon},{lat + deg_lat}"

    params = {
        "geometry": envelope,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }
    data = _blm_request_with_retry(params)
    if data is None:
        return []
    return _extract_claims_from_response(data, "")


def _extract_claims_from_response(data: dict, default_state: str) -> List[dict]:
    """Parse ArcGIS FeatureServer JSON response into claim dicts."""
    claims = []
    seen = set()
    for feature in data.get("features", []):
        attrs = feature.get("attributes", {})
        geom = feature.get("geometry", {})
        sf_id = attrs.get("SF_ID")
        case_nr = attrs.get("CSE_NR")
        if not sf_id or not case_nr or case_nr in seen:
            continue
        seen.add(case_nr)
        case_url = f"https://mlrs.blm.gov/s/blm-case/{sf_id}/{case_nr}"
        report_url = f"https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number={case_nr}"
        cse_meta = attrs.get("CSE_META") or ""
        state = cse_meta[:2].strip() if len(cse_meta) >= 2 else default_state
        row: dict = {
            "claim_name": attrs.get("CSE_NAME") or "Unknown",
            "serial_number": case_nr,
            "case_page": case_url,
            "payment_report": report_url,
            "state_abbr": state,
            "plss": cse_meta,
            "geometry": geom,
        }
        prod = attrs.get("BLM_PROD") or attrs.get("BLM_PROD_DESC") or attrs.get("PRODUCTION_TYPE")
        if prod is not None and str(prod).strip():
            row["BLM_PROD"] = str(prod).strip()
        claims.append(row)
    return claims
