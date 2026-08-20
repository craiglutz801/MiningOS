"""Nevada state adapter: Division of Minerals production data (plus optional active mines).

The strongest Nevada activity evidence is the state production-reporting record
and its most recent reported year.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from mining_os.active_mine_intel.matcher.arcgis_client import ArcGISClient, resolve_field
from mining_os.active_mine_intel.matcher.config import PipelineConfig
from mining_os.active_mine_intel.matcher.download_client import CacheManager
from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.models import SourceSchemaError, SourceStatus, SourceUnavailableError
from mining_os.active_mine_intel.matcher.source_registry import get_source
from mining_os.active_mine_intel.matcher.utilities import coerce_float, compact_json, parse_years, utc_now_iso

log = get_logger("mcm.nevada")

_FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "state_mine_id": (["MINE_ID", "SITE_ID", "NDOM_ID", "state_mine_id", "ID"], ["mine id", "site id"]),
    "msha_number": (
        ["MSHA_Number", "MshaNo", "MSHA__", "MSHA_NO", "MSHA"],
        ["msha"],
    ),
    "mine_name": (
        ["MINE_NAME", "Operation_Name", "OpName", "Opname", "NAME", "SITE_NAME", "mine_name", "MINE"],
        ["mine name", "operation name", "site name"],
    ),
    "operator_name": (
        ["OPERATOR", "OPERATOR_NAME", "Company_Name", "CompName", "COMPANY", "operator_name"],
        ["operator", "company"],
    ),
    "commodity": (["COMMODITY", "COMMODITIES", "MINERAL", "commodity"], ["commodit", "mineral"]),
    "county": (["COUNTY", "CNTY", "county"], ["county"]),
    "latitude": (["LATITUDE", "LAT", "latitude", "Y"], ["latitude"]),
    "longitude": (["LONGITUDE", "LONG", "LON", "longitude", "X"], ["longitude"]),
    "production_years": (
        ["PRODUCTION_YEARS", "PROD_YEARS", "YEARS", "production_years"],
        ["production year", "prod year"],
    ),
    "latest_production_year": (
        ["LATEST_PRODUCTION_YEAR", "LAST_YEAR", "MAX_YEAR", "latest_production_year"],
        ["latest year", "last year"],
    ),
    "first_production_year": (
        ["FIRST_PRODUCTION_YEAR", "FIRST_YEAR", "MIN_YEAR", "first_production_year"],
        ["first year"],
    ),
    "report_year": (["Report_Year", "RepYr", "REPORT_YEAR"], ["report year", "rep yr"]),
    "production_status": (
        ["STATUS", "PRODUCTION_STATUS", "MINE_STATUS", "production_status"],
        ["status"],
    ),
}


def resolve_production_layers(client: ArcGISClient) -> tuple[list[dict], dict]:
    """Resolve Nevada production feature layers (override or item discovery).

    Nevada publishes production split across several per-commodity layers
    (Metallics/NonMetallics/Clay/Aggregates plus historic points), so all
    layers passing the profile threshold are returned and later merged.
    """
    source = get_source("nevada_production")
    override = source.resolved_override()
    if override:
        log.info("Nevada production layer from NEVADA_PRODUCTION_FEATURE_URL override")
        layers = [{"url": override, "title": "env_override"}]
        return layers, {"selected": layers, "reasons": ["env override"]}
    candidates = client.resolve_item_layers(source.item_id, max_depth=5)
    diagnostics = client.select_layers(candidates, "nevada_production")
    for layer in diagnostics["selected"]:
        log.info(
            "Nevada production layer selected: %s (%s, score %s)",
            layer["url"],
            layer.get("title"),
            layer["selection_score"],
        )
    return diagnostics["selected"], diagnostics


def _extract_years(row: pd.Series, resolved: dict[str, str | None], raw: dict) -> list[int]:
    years: set[int] = set()
    for key in ("production_years", "latest_production_year", "first_production_year", "report_year"):
        col = resolved.get(key)
        if col and col in row.index:
            years.update(parse_years(row[col]))
    # Year-specific raw fields, e.g. PROD_2021 or columns whose name contains a year.
    for field_name, value in raw.items():
        field_years = parse_years(field_name)
        if field_years and value not in (None, "", 0, "0", False):
            years.update(field_years)
    # A first/last range implies the span of reporting years.
    first_col = resolved.get("first_production_year")
    last_col = resolved.get("latest_production_year")
    if first_col and last_col and first_col in row.index and last_col in row.index:
        first = parse_years(row[first_col])
        last = parse_years(row[last_col])
        if first and last and last[0] >= first[0]:
            years.update(range(first[0], last[0] + 1))
    return sorted(years)


def normalize_production(
    gdf: gpd.GeoDataFrame,
    cfg: PipelineConfig,
    source_url: str,
    layer_title: str = "",
) -> gpd.GeoDataFrame | None:
    """Normalize one production layer. Returns None when the layer has no
    resolvable mine-name field (e.g. county-level summary polygons)."""
    if gdf.empty:
        raise SourceSchemaError("Nevada production layer returned zero features")
    df = pd.DataFrame(gdf.drop(columns="geometry", errors="ignore"))
    fields = [{"name": c, "alias": c} for c in df.columns]
    resolved = {
        target: resolve_field(fields, candidates, keywords)
        for target, (candidates, keywords) in _FIELDS.items()
    }
    if not resolved.get("mine_name"):
        log.warning(
            "Nevada layer %s (%s) has no mine-name field; skipping (summary layer)",
            source_url,
            layer_title,
        )
        return None
    # Commodity fallback from the layer title (e.g. "Metallics 2023").
    title_commodity = re.sub(r"\b(19|20)\d{2}\b", "", layer_title).strip() or None

    df = df.reset_index(drop=True)
    geometries = list(gdf.geometry) if "geometry" in gdf.columns else [None] * len(df)
    records: list[dict] = []
    for idx, row in df.iterrows():
        raw = {k: v for k, v in row.items() if not isinstance(v, (bytes,))}
        years = _extract_years(row, resolved, raw)
        lat = coerce_float(row[resolved["latitude"]]) if resolved["latitude"] else None
        lon = coerce_float(row[resolved["longitude"]]) if resolved["longitude"] else None
        geom = geometries[idx]
        if geom is not None and not geom.is_empty:
            point = geom.representative_point()
            lat = lat if lat is not None else point.y
            lon = lon if lon is not None else point.x

        def get(key):
            col = resolved.get(key)
            return row[col] if col and col in row.index else None

        msha_number = _clean_msha_number(get("msha_number"))
        records.append(
            {
                "state_mine_id": str(
                    get("state_mine_id") or msha_number or f"NVROW{idx}"
                ),
                "msha_number": msha_number,
                "mine_name": get("mine_name"),
                "operator_name": get("operator_name"),
                "commodity": get("commodity") or title_commodity,
                "county": get("county"),
                "latitude": lat,
                "longitude": lon,
                "production_years": years,
                "latest_production_year": max(years) if years else None,
                "production_status": get("production_status"),
                "source_url": source_url,
                "raw_source_json": compact_json(raw),
            }
        )
    out = pd.DataFrame(records)
    out = out[out["mine_name"].notna()]
    return _finalize(out, cfg)


def _clean_msha_number(value) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value).split(".")[0])
    return digits or None


def merge_production_layers(frames: list[gpd.GeoDataFrame], cfg: PipelineConfig) -> gpd.GeoDataFrame:
    """Merge per-commodity/per-vintage layers into one record per mine.

    Records sharing an MSHA number (or, lacking one, a normalized name+county)
    are combined: production years are unioned and the most recent record wins
    for descriptive fields.
    """
    from normalize import normalize_mine_name

    combined = pd.concat([pd.DataFrame(f) for f in frames], ignore_index=True)
    if combined.empty:
        raise SourceSchemaError("No Nevada production records after normalization")

    def merge_key(row) -> str:
        if row.get("msha_number"):
            return f"msha:{row['msha_number']}"
        name = normalize_mine_name(row.get("mine_name"))
        county = str(row.get("county") or "").strip().upper()
        return f"name:{name}|{county}"

    combined["_key"] = combined.apply(merge_key, axis=1)
    combined["_latest"] = combined["latest_production_year"].fillna(0)
    merged_rows: list[dict] = []
    for _, group in combined.groupby("_key"):
        group = group.sort_values("_latest", ascending=False)
        base = group.iloc[0].to_dict()
        years: set[int] = set()
        for value in group["production_years"]:
            years.update(value or [])
        base["production_years"] = sorted(years)
        base["latest_production_year"] = max(years) if years else None
        base["commodity"] = "; ".join(
            sorted({str(c) for c in group["commodity"].dropna().unique()})
        ) or None
        base.pop("_key", None)
        base.pop("_latest", None)
        base.pop("geometry", None)
        merged_rows.append(base)
    log.info(
        "Nevada production: merged %d layer records into %d mines",
        len(combined),
        len(merged_rows),
    )
    return _finalize(pd.DataFrame(merged_rows), cfg)


def _finalize(df: pd.DataFrame, cfg: PipelineConfig) -> gpd.GeoDataFrame:
    lon_min, lat_min, lon_max, lat_max = cfg.bounds
    before = len(df)
    df = df[
        df["latitude"].notna()
        & df["longitude"].notna()
        & df["latitude"].between(lat_min, lat_max)
        & df["longitude"].between(lon_min, lon_max)
    ].copy()
    dropped = before - len(df)
    if dropped:
        log.info("Nevada production: dropped %d rows with missing/out-of-bounds coordinates", dropped)
    geometry = [Point(xy) for xy in zip(df["longitude"], df["latitude"])]
    return gpd.GeoDataFrame(df.reset_index(drop=True), geometry=geometry, crs="EPSG:4326")


def load_fixture(fixture_path: Path, cfg: PipelineConfig) -> gpd.GeoDataFrame:
    df = pd.read_csv(fixture_path, dtype=str)
    df = df[df["state"].str.upper() == "NV"].copy()
    records = []
    for idx, row in df.iterrows():
        years = parse_years(row.get("production_years"))
        records.append(
            {
                "state_mine_id": row.get("state_mine_id") or f"NVROW{idx}",
                "msha_number": _clean_msha_number(row.get("msha_number")),
                "mine_name": row.get("mine_name"),
                "operator_name": row.get("operator_name"),
                "commodity": row.get("commodity"),
                "county": row.get("county"),
                "latitude": coerce_float(row.get("latitude")),
                "longitude": coerce_float(row.get("longitude")),
                "production_years": years,
                "latest_production_year": max(years) if years else None,
                "production_status": row.get("production_status"),
                "source_url": str(fixture_path),
                "raw_source_json": compact_json(row.to_dict()),
            }
        )
    return _finalize(pd.DataFrame(records), cfg)


def load_state_mines(
    cfg: PipelineConfig,
    cache: CacheManager,
    client: ArcGISClient,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[gpd.GeoDataFrame, SourceStatus]:
    status = SourceStatus(source_id="nevada_production", retrieved_at=utc_now_iso())
    if fixture_path is not None:
        gdf = load_fixture(fixture_path, cfg)
        status.status = "success"
        status.resolved_url = str(fixture_path)
        status.record_count = len(gdf)
        status.message = "Loaded from local fixture"
        return gdf, status

    def fetch() -> bytes:
        layers, diagnostics = resolve_production_layers(client)
        fetched = []
        for layer in layers:
            try:
                fc = client.fetch_features_geojson(layer["url"])
            except Exception as exc:  # noqa: BLE001 - skip broken sub-layers
                log.warning("Nevada layer %s failed: %s", layer["url"], exc)
                continue
            if fc.get("features"):
                fetched.append(
                    {"url": layer["url"], "title": layer.get("title") or "", "collection": fc}
                )
        if not fetched:
            raise SourceUnavailableError("No Nevada production layers returned features")
        payload = {
            "layers": fetched,
            "diagnostics": {
                k: v for k, v in diagnostics.items() if k != "selected"
            },
            "selected_urls": [layer["url"] for layer in fetched],
        }
        return json.dumps(payload).encode("utf-8")

    source = get_source("nevada_production")
    content, info = cache.get(
        key="nevada_production",
        suffix=".json",
        fetch=fetch,
        ttl_hours=source.cache_ttl_hours,
        use_cache_only=use_cache_only,
        refresh=refresh,
    )
    payload = json.loads(content)
    status.resolved_url = "; ".join(payload.get("selected_urls", []))
    status.cache_used = info["cache_used"]
    status.cache_age_hours = info["cache_age_hours"]
    frames = []
    for layer in payload.get("layers", []):
        features = layer.get("collection", {}).get("features", [])
        if not features:
            continue
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        normalized = normalize_production(gdf, cfg, layer["url"], layer.get("title") or "")
        if normalized is not None and not normalized.empty:
            frames.append(normalized)
    if not frames:
        raise SourceUnavailableError("Nevada production layers contained no usable features")
    out = merge_production_layers(frames, cfg)
    status.record_count = len(out)
    status.status = "degraded" if info.get("stale") else (
        "cached" if info["cache_used"] else "success"
    )
    return out, status
