"""Geocode PLSS locations to lat/long using BLM Cadastral PLSS service."""
from __future__ import annotations

import logging
import re
from typing import Any, List

import requests

log = logging.getLogger("mining_os.plss_geocode")

PLSS_SECTION_URL = (
    "https://gis.blm.gov/arcgis/rest/services/Cadastral/"
    "BLM_Natl_PLSS_CadNSDI/MapServer/2/query"
)

STATE_MERIDIANS = {
    "UT": "26", "NV": "21", "ID": "08", "WY": "34", "MT": "24",
    "CO": "06", "AZ": "14", "NM": "22", "OR": "33", "WA": "33",
    "CA": "21", "SD": "05", "ND": "05",
}


def _format_twp_rng(value: str) -> str:
    """Convert '30S' -> '0300S' (4-digit number + direction)."""
    m = re.match(r"^0*(\d+)\s*([NSEW])$", value.strip().upper())
    if not m:
        return value
    num, d = m.groups()
    return num.zfill(4) + d


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

    if not meridian:
        meridian = STATE_MERIDIANS.get(state, "26")

    plssid = f"{state}{meridian}{twp}{rng}0"

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

    try:
        resp = requests.get(PLSS_SECTION_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("BLM PLSS geocode failed for %s: %s", plssid, e)
        return None

    if data.get("error"):
        log.warning("BLM PLSS API error for %s: %s", plssid, data["error"])
        return None

    features = data.get("features", [])
    if not features:
        log.debug("No PLSS features found for %s sec=%s", plssid, section or "?")
        return None

    geom = features[0].get("geometry", {})
    rings = geom.get("rings", [])
    if not rings:
        return None

    all_points = [pt for ring in rings for pt in ring]
    if not all_points:
        return None
    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    lat = (min(ys) + max(ys)) / 2
    lon = (min(xs) + max(xs)) / 2

    return {"latitude": round(lat, 6), "longitude": round(lon, 6)}


def _decode_tr_encoded_segment(enc: str) -> str | None:
    """
    Decode a PLSSID 5-char segment (4-digit + N/S/E/W) to a form compatible with areas_of_focus._display_trs
    (integer part is stored “×10” when < 100 so //10 display matches human T/R labels).
    """
    m = re.match(r"^(\d{4})([NSEW])$", (enc or "").strip().upper())
    if not m:
        return None
    n = int(m.group(1))
    d = m.group(2)
    if n < 100:
        n *= 10
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
