"""Load USGS / MRDS-style exports (CSV, GeoJSON) from a local data directory."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Optional, Union

from target_pipeline.models import RawSourceRow

log = logging.getLogger("target_pipeline.usgs")


def _norm_key(k: str) -> str:
    return k.lstrip("\ufeff").strip().lower().replace(" ", "_")


def _normalize_row_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {_norm_key(k): v for k, v in row.items()}


def _coerce_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        lk = _norm_key(k)
        if lk in d:
            return d[lk]
    return None


def _mrds_commodity_bundle(props: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("commod1", "commod2", "commod3", "ore", "commodity", "commodities"):
        v = _pick(props, key)
        if v is None or (isinstance(v, float) and str(v) == "nan"):
            continue
        s = str(v).strip()
        if not s or s.upper() in ("NA", "N/A", "-", "UNKNOWN"):
            continue
        if s not in parts:
            parts.append(s)
    return ", ".join(parts)


def _row_from_props(props: dict[str, Any], source_file: str) -> RawSourceRow:
    name = _pick(props, "name", "site_name", "dep_name", "title") or ""
    state = _pick(props, "state", "state_abbr", "st") or ""
    county = _pick(props, "county", "cnty_name") or ""
    comm = _pick(props, "commodity", "commodities", "ore", "dep_type") or ""
    if isinstance(comm, list):
        comm = ", ".join(str(x) for x in comm)
    if not str(comm).strip():
        comm = _mrds_commodity_bundle(props)
    plss = _pick(props, "plss", "plss_raw", "location_plss", "meridian", "town_range_sec") or ""
    lat = _pick(props, "latitude", "lat", "y")
    lon = _pick(props, "longitude", "lon", "long", "x")
    ref = _pick(props, "reference_text", "refs", "url", "link")
    reports: list[str] = []
    if isinstance(ref, list):
        reports = [str(x) for x in ref if x]
    elif isinstance(ref, str) and ref.strip():
        reports = [ref.strip()]
    status = _pick(props, "status", "dev_stat") or ""
    lat_f = _coerce_float(lat)
    lon_f = _coerce_float(lon)
    return RawSourceRow(
        source="usgs",
        name=str(name),
        state=str(state) if state else "",
        county=str(county) if county else "",
        commodity_raw=str(comm) if comm else "",
        plss_raw=str(plss).strip() if plss else "",
        latitude=lat_f,
        longitude=lon_f,
        reports=reports,
        status=str(status) if status else "",
        record_type="deposit",
        raw={"file": source_file, "properties": props, "dep_id": _pick(props, "dep_id")},
    )


def _load_csv(path: Path, max_rows: Optional[int] = None) -> list[RawSourceRow]:
    out: list[RawSourceRow] = []
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if not row:
                continue
            if max_rows is not None and i >= max_rows:
                break
            out.append(_row_from_props(_normalize_row_keys(dict(row)), str(path)))
    return out


def _load_geojson(path: Path) -> list[RawSourceRow]:
    out: list[RawSourceRow] = []
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    features = data.get("features") or []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        geom = feat.get("geometry") or {}
        coords = None
        if isinstance(geom, dict) and geom.get("type") == "Point":
            c = geom.get("coordinates")
            if isinstance(c, list) and len(c) >= 2:
                coords = (c[1], c[0])
        row = _row_from_props(props, str(path))
        if coords and row.get("latitude") is None:
            row = dict(row)
            row["latitude"] = coords[0]
            row["longitude"] = coords[1]
        out.append(row)
    return out


def load_usgs_rows(data_dir: Union[str, Path], max_rows: Optional[int] = None) -> list[RawSourceRow]:
    root = Path(data_dir)
    usgs_dir = root / "usgs" if (root / "usgs").is_dir() else root
    if not usgs_dir.is_dir():
        log.warning("USGS data directory not found: %s", usgs_dir)
        return []

    rows: list[RawSourceRow] = []
    for path in sorted(usgs_dir.glob("*.csv")):
        try:
            remaining = None if max_rows is None else max(0, max_rows - len(rows))
            if remaining == 0:
                break
            rows.extend(_load_csv(path, max_rows=remaining))
        except Exception as e:
            log.error("Failed to load %s: %s", path, e)
    for path in sorted(usgs_dir.glob("*.geojson")) + sorted(usgs_dir.glob("*.json")):
        if max_rows is not None and len(rows) >= max_rows:
            break
        try:
            rows.extend(_load_geojson(path))
        except Exception as e:
            log.error("Failed to load %s: %s", path, e)
    if max_rows is not None and len(rows) > max_rows:
        rows = rows[:max_rows]
    log.info("USGS: loaded %s raw rows from %s", len(rows), usgs_dir)
    return rows
