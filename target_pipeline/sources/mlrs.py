"""Load MLRS-style exports (CSV, GeoJSON) from a local data directory."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Optional, Union

from target_pipeline.models import RawSourceRow

log = logging.getLogger("target_pipeline.mlrs")


def _norm_key(k: str) -> str:
    return k.lstrip("\ufeff").strip().lower().replace(" ", "_")


def _normalize_row_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {_norm_key(k): v for k, v in row.items()}


def _first_plss_segment(cse_meta: str) -> str:
    if not cse_meta or not str(cse_meta).strip():
        return ""
    return str(cse_meta).split("|")[0].strip()


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


def _row_from_props(props: dict[str, Any], source_file: str) -> RawSourceRow:
    name = _pick(props, "name", "claim_name", "claimname", "cse_name", "title") or ""
    state = (_pick(props, "state", "state_abbr", "st", "admin_state") or "").strip()
    county = (_pick(props, "county", "admin_county") or "").strip()
    comm = _pick(props, "commodity", "commodities", "mineral", "minerals", "blm_prod", "cse_type_nr") or ""
    if isinstance(comm, list):
        comm = ", ".join(str(x) for x in comm)
    plss = _pick(props, "plss", "plss_raw", "location_plss", "trs", "meridian_twp_rng_sec") or ""
    cse_meta = _pick(props, "cse_meta") or ""
    if not plss and cse_meta:
        plss = _first_plss_segment(str(cse_meta))
    if not state and plss:
        tok = plss.strip().split()
        if tok and len(tok[0]) == 2 and tok[0].isalpha():
            state = tok[0].upper()
    lat = _pick(props, "latitude", "lat", "y")
    lon = _pick(props, "longitude", "lon", "long", "x")
    reports_raw = _pick(props, "reports", "report_links", "references")
    reports: list[str] = []
    if isinstance(reports_raw, list):
        reports = [str(x) for x in reports_raw if x]
    elif isinstance(reports_raw, str) and reports_raw.strip():
        reports = [reports_raw.strip()]
    status = _pick(props, "status", "case_status", "claim_status", "cse_disp") or ""
    _pick(props, "serial_num", "serial", "claim_id", "objectid")
    lat_f = _coerce_float(lat)
    lon_f = _coerce_float(lon)
    return RawSourceRow(
        source="mlrs",
        name=str(name),
        state=str(state) if state else "",
        county=str(county) if county else "",
        commodity_raw=str(comm) if comm else "",
        plss_raw=str(plss).strip() if plss else "",
        latitude=lat_f,
        longitude=lon_f,
        reports=reports,
        status=str(status) if status else "",
        record_type="claim",
        raw={
            "file": source_file,
            "properties": props,
            "cse_nr": _pick(props, "cse_nr"),
            "objectid": _pick(props, "objectid"),
        },
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


def load_mlrs_rows(data_dir: Union[str, Path], max_rows: Optional[int] = None) -> list[RawSourceRow]:
    root = Path(data_dir)
    mlrs_dir = root / "mlrs" if (root / "mlrs").is_dir() else root
    if not mlrs_dir.is_dir():
        log.warning("MLRS data directory not found: %s", mlrs_dir)
        return []

    rows: list[RawSourceRow] = []
    for path in sorted(mlrs_dir.glob("*.csv")):
        try:
            remaining = None if max_rows is None else max(0, max_rows - len(rows))
            if remaining == 0:
                break
            rows.extend(_load_csv(path, max_rows=remaining))
        except Exception as e:
            log.error("Failed to load %s: %s", path, e)
    for path in sorted(mlrs_dir.glob("*.geojson")) + sorted(mlrs_dir.glob("*.json")):
        if max_rows is not None and len(rows) >= max_rows:
            break
        try:
            rows.extend(_load_geojson(path))
        except Exception as e:
            log.error("Failed to load %s: %s", path, e)
    if max_rows is not None and len(rows) > max_rows:
        rows = rows[:max_rows]
    log.info("MLRS: loaded %s raw rows from %s", len(rows), mlrs_dir)
    return rows
