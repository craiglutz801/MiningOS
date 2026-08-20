"""Resolve PLSS (township / range / section) for mine coordinates.

Primary: BLM Cadastral CadNSDI reverse intersect (same service Mining OS uses).
Fallback: parse PLSS from a matched claim's CSE_META string.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from mining_os.active_mine_intel.matcher.config import Paths
from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.utilities import utc_now_iso

log = get_logger("mcm.plss")

PLSS_SECTION_URL = (
    "https://gis.blm.gov/arcgis/rest/services/Cadastral/"
    "BLM_Natl_PLSS_CadNSDI/MapServer/2/query"
)
USER_AGENT = "MineClaimMatcher/0.1 (research tool; local use)"


def _decode_tr_encoded_segment(enc: str) -> str | None:
    m = re.match(r"^(\d{4})([NSEW])$", (enc or "").strip().upper())
    if not m:
        return None
    return f"{int(m.group(1))}{m.group(2)}"


def _human_tr_label(storage: str) -> str:
    m = re.match(r"^(\d+)([NSEW])$", (storage or "").strip().upper())
    if not m:
        return storage or ""
    return f"{int(m.group(1)) // 10}{m.group(2)}"


def format_location_plss(
    state: str | None,
    township: str | None,
    range_val: str | None,
    section: str | None = None,
) -> str | None:
    if not state or not township or not range_val:
        return None
    twp_h, rng_h = _human_tr_label(township), _human_tr_label(range_val)
    if section:
        return f"{state} T{twp_h} R{rng_h} Sec {section}"
    return f"{state} T{twp_h} R{rng_h}"


def parse_cse_meta(meta: str | None) -> dict[str, Any] | None:
    """Parse the first aliquot from BLM CSE_META (e.g. 'NV 21 0100N 0320E 009 A …')."""
    if not meta or not isinstance(meta, str):
        return None
    first = meta.split("|")[0].strip()
    parts = first.split()
    if len(parts) < 5:
        return None
    state = parts[0].upper()
    meridian = parts[1]
    twp = _decode_tr_encoded_segment(parts[2]) or parts[2].upper()
    rng = _decode_tr_encoded_segment(parts[3]) or parts[3].upper()
    sec_raw = parts[4]
    section = None
    if sec_raw.isdigit():
        n = int(sec_raw)
        if 1 <= n <= 36:
            section = str(n)
    location = format_location_plss(state, twp, rng, section)
    if not location:
        return None
    return {
        "state_abbr": state,
        "meridian": meridian,
        "township": twp,
        "range": rng,
        "section": section,
        "location_plss": location,
        "plss_source": "claim_cse_meta",
    }


def _parse_plssid_attrs(plssid: str | None, frst_div_no: str | None) -> dict[str, Any] | None:
    p = (plssid or "").strip().upper()
    if len(p) < 14:
        return None
    state = p[0:2]
    meridian = p[2:4]
    twp = _decode_tr_encoded_segment(p[4:9])
    rng = _decode_tr_encoded_segment(p[9:14])
    if not twp or not rng:
        return None
    sec_display = None
    raw_sec = str(frst_div_no).strip() if frst_div_no is not None else ""
    if raw_sec.isdigit():
        n = int(raw_sec, 10)
        if 1 <= n <= 36:
            sec_display = str(n)
    location = format_location_plss(state, twp, rng, sec_display)
    return {
        "state_abbr": state,
        "meridian": meridian,
        "township": twp,
        "range": rng,
        "section": sec_display,
        "location_plss": location,
        "plssid": p,
        "plss_source": "blm_cadastral_reverse",
    }


def reverse_geocode_plss(latitude: float, longitude: float) -> dict[str, Any] | None:
    """Resolve PLSS section at a WGS84 point via BLM Cadastral CadNSDI."""
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
        resp = requests.get(
            PLSS_SECTION_URL,
            params=params,
            timeout=20,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("PLSS reverse geocode failed for %.5f,%.5f: %s", lat, lon, exc)
        return None
    if data.get("error"):
        log.warning("PLSS reverse API error: %s", data["error"])
        return None
    for feat in data.get("features") or []:
        attrs = feat.get("attributes") or {}
        parsed = _parse_plssid_attrs(attrs.get("PLSSID"), attrs.get("FRSTDIVNO"))
        if parsed:
            return parsed
    return None


def cache_dir(paths: Paths | None = None) -> Path:
    paths = paths or Paths()
    directory = paths.root / "data" / "cache" / "plss_reverse"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_key(lat: float, lon: float) -> str:
    return f"{lat:.5f}_{lon:.5f}"


def read_plss_cache(lat: float, lon: float, paths: Paths | None = None) -> dict | None:
    path = cache_dir(paths) / f"{_cache_key(lat, lon)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_plss_cache(lat: float, lon: float, payload: dict | None, paths: Paths | None = None) -> None:
    path = cache_dir(paths) / f"{_cache_key(lat, lon)}.json"
    path.write_text(json.dumps(payload or {}, indent=2, default=str), encoding="utf-8")


def lookup_plss_for_point(
    latitude: float,
    longitude: float,
    *,
    cse_meta_fallback: str | None = None,
    paths: Paths | None = None,
    use_network: bool = True,
) -> dict[str, Any] | None:
    """Prefer reverse geocode (cached); fall back to claim CSE_META."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return parse_cse_meta(cse_meta_fallback)

    cached = read_plss_cache(lat, lon, paths)
    if cached and cached.get("location_plss"):
        return cached

    result = None
    if use_network:
        result = reverse_geocode_plss(lat, lon)
        if result:
            result["checked_at"] = utc_now_iso()
            write_plss_cache(lat, lon, result, paths)
            return result

    fallback = parse_cse_meta(cse_meta_fallback)
    if fallback:
        return fallback
    return result


def attach_plss_to_sites(
    sites: pd.DataFrame,
    matches: pd.DataFrame | None = None,
    claims: pd.DataFrame | None = None,
    *,
    paths: Paths | None = None,
    use_network: bool = True,
    sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Add plss / township / range / section columns to every site row."""
    out = sites.copy()
    # Best-claim CSE_META lookup for fallback
    meta_by_serial: dict[str, str] = {}
    if claims is not None and not claims.empty and "cse_meta" in claims.columns:
        for _, row in claims.dropna(subset=["claim_serial_number"]).iterrows():
            serial = str(row["claim_serial_number"]).strip()
            meta = row.get("cse_meta")
            if serial and pd.notna(meta) and str(meta).strip():
                meta_by_serial[serial] = str(meta)

    meta_by_site: dict[str, str] = {}
    if matches is not None and not matches.empty:
        for site_id, group in matches.groupby("mine_site_id"):
            serial = None
            if "best_claim_serial_number" in out.columns:
                pass
            # Prefer highest-scoring match serial present in meta map
            for serial_val in group.get("claim_serial_number", pd.Series(dtype=str)).tolist():
                serial = str(serial_val or "").strip()
                if serial in meta_by_serial:
                    meta_by_site[str(site_id)] = meta_by_serial[serial]
                    break

    plss_vals, twp_vals, rng_vals, sec_vals, src_vals = [], [], [], [], []
    total = len(out)
    for i, (_, row) in enumerate(out.iterrows(), start=1):
        site_id = str(row.get("mine_site_id") or "")
        serial = str(row.get("best_claim_serial_number") or "").strip()
        fallback = meta_by_site.get(site_id) or meta_by_serial.get(serial)
        result = None
        try:
            lat = float(row.get("latitude"))
            lon = float(row.get("longitude"))
        except (TypeError, ValueError):
            lat = lon = None

        if lat is not None and lon is not None:
            cached = read_plss_cache(lat, lon, paths)
            if cached and cached.get("location_plss"):
                result = cached
            elif use_network:
                result = reverse_geocode_plss(lat, lon)
                if result:
                    result["checked_at"] = utc_now_iso()
                    write_plss_cache(lat, lon, result, paths)
                    time.sleep(sleep_s)
        if result is None:
            result = parse_cse_meta(fallback)

        if result:
            plss_vals.append(result.get("location_plss"))
            twp_vals.append(result.get("township"))
            rng_vals.append(result.get("range"))
            sec_vals.append(result.get("section"))
            src_vals.append(result.get("plss_source"))
        else:
            plss_vals.append(None)
            twp_vals.append(None)
            rng_vals.append(None)
            sec_vals.append(None)
            src_vals.append(None)

        if i == 1 or i % 25 == 0 or i == total:
            log.info(
                "PLSS progress %d/%d (latest: %s)",
                i,
                total,
                plss_vals[-1] or "missing",
            )

    out["plss"] = plss_vals
    out["township"] = twp_vals
    out["range"] = rng_vals
    out["section"] = sec_vals
    out["plss_source"] = src_vals
    filled = int(pd.Series(plss_vals).notna().sum())
    log.info("PLSS attached to %d / %d site(s)", filled, len(out))
    return out


def backfill_plss_outputs(
    state_code: str,
    *,
    paths: Paths | None = None,
    use_network: bool = True,
) -> dict[str, Any]:
    """Attach PLSS to existing candidate_sites.csv for a state."""
    from claim_payment import output_dir_for_state

    paths = paths or Paths()
    out_dir = output_dir_for_state(state_code, paths)
    sites_path = out_dir / "candidate_sites.csv"
    if not sites_path.exists():
        raise FileNotFoundError(f"Missing {sites_path}")
    sites = pd.read_csv(sites_path)
    matches_path = out_dir / "candidate_matches.csv"
    matches = pd.read_csv(matches_path) if matches_path.exists() else None
    claims_path = out_dir / "active_unpatented_claims.parquet"
    claims = pd.read_parquet(claims_path) if claims_path.exists() else None

    enriched = attach_plss_to_sites(
        sites, matches, claims, paths=paths, use_network=use_network
    )
    enriched.to_csv(sites_path, index=False)

    # Mirror into geojson properties when present
    geo_path = out_dir / "candidate_sites.geojson"
    if geo_path.exists():
        try:
            payload = json.loads(geo_path.read_text(encoding="utf-8"))
            by_id = {
                str(r["mine_site_id"]): r
                for _, r in enriched.iterrows()
                if pd.notna(r.get("mine_site_id"))
            }
            for feat in payload.get("features", []):
                props = feat.get("properties") or {}
                key = str(props.get("mine_site_id") or "")
                if key in by_id:
                    row = by_id[key]
                    for col in ("plss", "township", "range", "section", "plss_source"):
                        val = row.get(col)
                        props[col] = None if pd.isna(val) else val
                    feat["properties"] = props
            geo_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not update sites geojson PLSS: %s", exc)

    filled = int(enriched["plss"].notna().sum()) if "plss" in enriched.columns else 0
    return {
        "state": state_code,
        "sites": len(enriched),
        "plss_filled": filled,
        "completed_at": utc_now_iso(),
    }
