"""Geocode PLSS locations to lat/long using BLM Cadastral PLSS service."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, List

import requests

log = logging.getLogger("mining_os.plss_geocode")

PLSS_SECTION_URL = (
    "https://gis.blm.gov/arcgis/rest/services/Cadastral/"
    "BLM_Natl_PLSS_CadNSDI/MapServer/2/query"
)

# BLM Cadastral PLSSID meridian codes. NOTE: these differ from the MLRS
# CSE_META meridian codes used in `fetch_claim_records.STATE_MERIDIAN` —
# e.g. Idaho is 08 here but 01 in MLRS. See `_candidate_meridians` below
# for how we bridge callers that pass the wrong code.
STATE_MERIDIANS = {
    "UT": "26", "NV": "21", "ID": "08", "WY": "34", "MT": "24",
    "CO": "06", "AZ": "14", "NM": "22", "OR": "33", "WA": "33",
    "CA": "21", "SD": "05", "ND": "05",
}

# For states with multiple active principal meridians, list all we know of.
# Querying falls through the list until one returns data. Order matters.
STATE_MERIDIAN_CANDIDATES = {
    "UT": ["26"],
    "NV": ["21"],
    "ID": ["08"],
    "WY": ["34", "28"],   # Wind River, 6th Principal
    "MT": ["24"],
    "CO": ["06", "28", "22", "31"],  # 4th, 6th, New Mexico, Ute
    "AZ": ["14"],
    "NM": ["22"],
    "OR": ["33"],
    "WA": ["33"],
    "CA": ["21", "14", "27"],  # Mt Diablo, Humboldt, San Bernardino
    "SD": ["05", "02"],  # 5th Principal, Black Hills
    "ND": ["05"],
    "AK": ["04", "02", "03", "05", "06"],
}


def _candidate_meridians(state: str, passed: str | None) -> list[str]:
    """
    Return the ordered list of meridian codes to try for this state.
    If the caller passed a meridian we try it first, then fall back to
    the canonical Cadastral meridian(s) for that state (handles callers
    that supply an MLRS-format code by mistake).
    """
    state = (state or "").strip().upper()[:2]
    canonical = STATE_MERIDIAN_CANDIDATES.get(state) or [STATE_MERIDIANS.get(state, "26")]
    if not passed:
        return canonical
    p = str(passed).strip().zfill(2)
    return [p] + [m for m in canonical if m != p]


def _format_twp_rng(value: str) -> str:
    """
    Convert a township or range value to the BLM PLSSID 4-digit `×10` format.

    The codebase stores T/R in two conventions (both appear in the areas_of_focus
    table today):
      - 4-char already-encoded form: '0280S' (T28 South), '0080N' (T8 North)
      - 1-3 char human form:        '28S', '8N', '149N'

    We need to output the 4-char encoded form that the BLM Cadastral PLSSID
    service understands. Human form is multiplied by 10 and zero-padded;
    already-encoded form is passed through.

    Examples:
        '28S'    -> '0280S'   (human T28 South)
        '8N'     -> '0080N'   (human T8 North)
        '149N'   -> '1490N'   (human T149 North)
        '0280S'  -> '0280S'   (already encoded, pass through)
        '0080N'  -> '0080N'   (already encoded, pass through)
    """
    s = (value or "").strip().upper().replace(" ", "")
    m = re.match(r"^(\d+)([NSEW])$", s)
    if not m:
        return s
    num_str, direction = m.groups()
    if len(num_str) >= 4:
        return num_str.zfill(4) + direction
    n = int(num_str) * 10
    return str(n).zfill(4) + direction


def geocode_plss(
    state: str,
    township: str,
    range_val: str,
    section: str | None = None,
    meridian: str | None = None,
) -> dict | None:
    """
    Query BLM Cadastral PLSS service to get section centroid coordinates.
    PLSSID format: {State}{Meridian}{Township4char}{Range4char}0
    Returns {"latitude": float, "longitude": float} or None.
    """
    state = (state or "").strip().upper()[:2]
    if not state or not township or not range_val:
        return None

    twp = _format_twp_rng(township)
    rng = _format_twp_rng(range_val)

    meridians_to_try = _candidate_meridians(state, meridian)

    for mer in meridians_to_try:
        plssid = f"{state}{mer}{twp}{rng}0"

        if section:
            sec_num = re.sub(r"\D", "", str(section)).lstrip("0") or "0"
            sec_padded = sec_num.zfill(2)
            where = f"PLSSID LIKE '{plssid}%' AND FRSTDIVNO = '{sec_padded}'"
        else:
            where = f"PLSSID LIKE '{plssid}%'"

        params = {
            "where": where,
            "outFields": "PLSSID,FRSTDIVNO",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
            "resultRecordCount": "1",
        }

        data = _plss_request_with_retry(params, plssid)
        if data is None:
            continue

        features = data.get("features", [])
        if not features:
            log.debug("No PLSS features for %s sec=%s — trying next meridian", plssid, section or "?")
            continue

        geom = features[0].get("geometry", {})
        rings = geom.get("rings", [])
        if not rings:
            continue

        all_points = [pt for ring in rings for pt in ring]
        if not all_points:
            continue
        xs = [p[0] for p in all_points]
        ys = [p[1] for p in all_points]
        lat = (min(ys) + max(ys)) / 2
        lon = (min(xs) + max(xs)) / 2

        return {"latitude": round(lat, 6), "longitude": round(lon, 6)}

    return None


def _plss_request_with_retry(params: dict, plssid: str, retries: int = 2) -> dict | None:
    """
    GET the PLSS service with small exponential backoff on transient 500s and
    other errors. Returns the parsed JSON dict, or None if all attempts failed
    / the service reported an error.
    """
    backoff = 0.4
    for attempt in range(retries + 1):
        try:
            resp = requests.get(PLSS_SECTION_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2
                continue
            log.warning("BLM PLSS geocode failed for %s: %s", plssid, e)
            return None

        err = data.get("error") if isinstance(data, dict) else None
        if err:
            # ArcGIS 500 is frequently transient — retry once or twice.
            if attempt < retries and isinstance(err, dict) and err.get("code") == 500:
                time.sleep(backoff)
                backoff *= 2
                continue
            log.warning("BLM PLSS API error for %s: %s", plssid, err)
            return None

        return data
    return None


def _decode_tr_encoded_segment(enc: str) -> str | None:
    """
    Decode a PLSSID 5-char segment (4-digit + N/S/E/W) to the storage form
    used by areas_of_focus.

    BLM PLSSID encodes township/range as ``human_value × 10`` zero-padded to 4 digits
    (e.g. T8 → ``"0080"``, T12 → ``"0120"``, T149 → ``"1490"``). The storage form
    in areas_of_focus.township / .range is the same ``×10`` integer string, and
    ``_human_tr_label`` divides by 10 for display. So decoding simply strips the
    leading zeros and re-attaches the direction.

    Examples:
        "0080S" -> "80S"   (display: T8S)
        "0120S" -> "120S"  (display: T12S)
        "1490S" -> "1490S" (display: T149S)
    """
    m = re.match(r"^(\d{4})([NSEW])$", (enc or "").strip().upper())
    if not m:
        return None
    n = int(m.group(1))
    d = m.group(2)
    return f"{n}{d}"


def _human_tr_label(storage: str) -> str:
    """Township/range label for location_plss text (inverse of zero-padded PLSSID segment)."""
    m = re.match(r"^(\d+)([NSEW])$", (storage or "").strip().upper())
    if not m:
        return storage or ""
    return f"{int(m.group(1)) // 10}{m.group(2)}"


def _parse_plssid_attrs(plssid: str | None, frst_div_no: str | None) -> dict[str, Any] | None:
    """Parse BLM CadNSDI PLSSID + FRSTDIVNO into state, meridian, T/R/S display strings."""
    p = (plssid or "").strip().upper()
    if len(p) < 14:
        return None
    state = p[0:2]
    meridian = p[2:4]
    twp = _decode_tr_encoded_segment(p[4:9])
    rng = _decode_tr_encoded_segment(p[9:14])
    if not twp or not rng:
        return None
    sec_display: str | None = None
    raw_sec = (frst_div_no is not None) and str(frst_div_no).strip()
    if raw_sec and raw_sec.isdigit():
        n = int(raw_sec, 10)
        if 1 <= n <= 36:
            sec_display = str(n)
    return {
        "state_abbr": state,
        "meridian": meridian,
        "township": twp,
        "range": rng,
        "section": sec_display,
        "plssid": p,
        "frstdivno": raw_sec,
    }


def reverse_geocode_plss(latitude: float, longitude: float) -> dict[str, Any] | None:
    """
    Resolve PLSS section at a WGS84 point (BLM Cadastral PLSS, same layer as forward geocode).
    Returns location_plss, state_abbr, meridian, township, range, section, etc., or None.
    """
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None

    params = {
        "f": "json",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PLSSID,FRSTDIVNO",
        "returnGeometry": "false",
        "resultRecordCount": "5",
    }
    try:
        resp = requests.get(PLSS_SECTION_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("BLM PLSS reverse geocode failed for %.6f,%.6f: %s", lat, lon, e)
        return None
    if data.get("error"):
        log.warning("BLM PLSS reverse API error: %s", data["error"])
        return None

    features = data.get("features") or []
    for feat in features:
        attrs = feat.get("attributes") or {}
        parts = _parse_plssid_attrs(attrs.get("PLSSID"), attrs.get("FRSTDIVNO"))
        if not parts:
            continue
        st = parts["state_abbr"]
        twp, rng, sec = parts["township"], parts["range"], parts["section"]
        twp_h, rng_h = _human_tr_label(twp), _human_tr_label(rng)
        if sec:
            location_plss = f"{st} T{twp_h} R{rng_h} Sec {sec}"
        else:
            location_plss = f"{st} T{twp_h} R{rng_h}"
        return {**parts, "location_plss": location_plss}

    log.debug("No parseable PLSS at %.6f,%.6f (%d features)", lat, lon, len(features))
    return None


def batch_geocode_targets(targets: list) -> int:
    """Geocode targets missing lat/long. Updates DB in place. Returns count updated."""
    from mining_os.db import get_engine
    from sqlalchemy import text

    eng = get_engine()

    with eng.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT id, state_abbr, township, "range", section, meridian
                FROM areas_of_focus
                WHERE (latitude IS NULL OR longitude IS NULL)
                  AND township IS NOT NULL AND "range" IS NOT NULL
            """)
        ).mappings().fetchall()

    log.info("Found %d targets to geocode", len(rows))
    updated = 0

    for row in rows:
        result = geocode_plss(
            state=row["state_abbr"] or "UT",
            township=row["township"],
            range_val=row["range"],
            section=row.get("section"),
            meridian=row.get("meridian"),
        )
        if result:
            with eng.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE areas_of_focus
                        SET latitude = :lat, longitude = :lon, updated_at = now()
                        WHERE id = :id
                    """),
                    {"lat": result["latitude"], "lon": result["longitude"], "id": row["id"]},
                )
                updated += 1
                log.info("Geocoded target %d -> %.6f, %.6f", row["id"], result["latitude"], result["longitude"])

    log.info("Geocoded %d of %d targets", updated, len(rows))
    return updated
