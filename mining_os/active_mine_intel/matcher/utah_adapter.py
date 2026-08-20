"""Utah state adapter: DOGM mineral mine permits and mine records.

Utah is deliberately conservative: an active DOGM permit is never treated as
proof of current production. A structured production indicator is only set when
a source field explicitly says producing/production/current extraction.
"""

from __future__ import annotations

import json
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
from mining_os.active_mine_intel.matcher.utilities import coerce_float, compact_json, utc_now_iso

log = get_logger("mcm.utah")

_FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "state_mine_id": (["MINE_ID", "SITE_ID", "state_mine_id", "ID"], ["mine id", "site id"]),
    "permit_number": (["PERMIT_NUMBER", "PERMIT_NO", "PERMITID", "permit_number"], ["permit"]),
    "mine_name": (["MINE_NAME", "NAME", "SITE_NAME", "mine_name"], ["mine name", "site name"]),
    "operator_name": (["OPERATOR", "OPERATOR_NAME", "PERMITTEE", "COMPANY", "operator_name"], ["operator", "permittee"]),
    "permit_status": (["PERMIT_STATUS", "STATUS", "permit_status"], ["permit status"]),
    "mine_status": (["MINE_STATUS", "OPER_STATUS", "ACTIVITY", "mine_status"], ["mine status", "activity"]),
    "mine_class": (["MINE_CLASS", "CLASS", "PERMIT_TYPE", "mine_class"], ["class"]),
    "commodity": (["COMMODITY", "COMMODITIES", "MINERAL", "commodity"], ["commodit", "mineral"]),
    "county": (["COUNTY", "CNTY", "county"], ["county"]),
    "latitude": (["LATITUDE", "LAT", "latitude", "Y"], ["latitude"]),
    "longitude": (["LONGITUDE", "LONG", "LON", "longitude", "X"], ["longitude"]),
}

ACTIVE_PERMIT_TOKENS = ("active", "current", "operating", "operational")
PRODUCTION_TOKENS = ("producing", "production", "current extraction", "extracting")
# DOGM publishes coded statuses: ACT = active mine, APP = approved permit.
# RET/ARC/REC/INA/NAP/NPR/FOR indicate returned/archived/reclaimed/inactive
# records and are never treated as active. "PRO" is NOT treated as producing;
# per the conservative Utah rule it stays unverified.
ACTIVE_STATUS_CODES = {"ACT", "APP"}


def resolve_dogm_layer(client: ArcGISClient) -> tuple[str, dict]:
    source = get_source("utah_dogm")
    override = source.resolved_override()
    if override:
        log.info("Utah DOGM layer from UTAH_DOGM_FEATURE_URL override")
        return override, {"selected_url": override, "reasons": ["env override"]}
    candidates = client.resolve_item_layers(source.item_id, max_depth=5)
    diagnostics = client.select_best_layer(candidates, "utah_mineral_permits")
    log.info(
        "Utah DOGM layer selected: %s (score %s)",
        diagnostics["selected_url"],
        diagnostics["selection_score"],
    )
    return diagnostics["selected_url"], diagnostics


_STATUS_FIELDS = ("permit_status", "mine_status", "mine_class", "production_status", "activity")


def _status_flags(row_dict: dict) -> tuple[bool, bool]:
    """Return (permit_active, production_indicator) from structured status fields only.

    Deliberately ignores audit blobs and name fields so that a field *name* like
    'production_years' can never masquerade as a production indicator.
    """
    status_blob = " ".join(
        str(row_dict.get(key) or "").lower() for key in _STATUS_FIELDS
    )
    permit_active = False
    for key in ("permit_status", "mine_status"):
        value = str(row_dict.get(key) or "").strip()
        if value.upper() in ACTIVE_STATUS_CODES or any(
            tok in value.lower() for tok in ACTIVE_PERMIT_TOKENS
        ):
            permit_active = True
    production_indicator = any(tok in status_blob for tok in PRODUCTION_TOKENS)
    return permit_active, production_indicator


def normalize_dogm(gdf: gpd.GeoDataFrame, cfg: PipelineConfig, source_url: str) -> gpd.GeoDataFrame:
    if gdf.empty:
        raise SourceSchemaError("Utah DOGM layer returned zero features")
    df = pd.DataFrame(gdf.drop(columns="geometry", errors="ignore")).reset_index(drop=True)
    fields = [{"name": c, "alias": c} for c in df.columns]
    resolved = {
        target: resolve_field(fields, candidates, keywords)
        for target, (candidates, keywords) in _FIELDS.items()
    }
    geometries = list(gdf.geometry) if "geometry" in gdf.columns else [None] * len(df)

    records: list[dict] = []
    for idx, row in df.iterrows():
        def get(key):
            col = resolved.get(key)
            value = row[col] if col and col in row.index else None
            if isinstance(value, str):
                value = value.strip() or None
            return value

        lat = coerce_float(get("latitude"))
        lon = coerce_float(get("longitude"))
        geom = geometries[idx]
        if geom is not None and not geom.is_empty:
            point = geom.representative_point()
            lat = lat if lat is not None else point.y
            lon = lon if lon is not None else point.x
        record = {
            "state_mine_id": str(get("state_mine_id") or get("permit_number") or f"UTROW{idx}"),
            "permit_number": get("permit_number"),
            "mine_name": get("mine_name"),
            "operator_name": get("operator_name"),
            "permit_status": get("permit_status"),
            "mine_status": get("mine_status"),
            "mine_class": get("mine_class"),
            "commodity": get("commodity"),
            "county": get("county"),
            "latitude": lat,
            "longitude": lon,
            "source_url": source_url,
            "raw_source_json": compact_json(dict(row)),
        }
        permit_active, production_indicator = _status_flags(record)
        record["state_permit_active"] = permit_active
        record["state_production_indicator"] = production_indicator
        records.append(record)
    return _finalize(pd.DataFrame(records), cfg)


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
        log.info("Utah DOGM: dropped %d rows with missing/out-of-bounds coordinates", dropped)
    geometry = [Point(xy) for xy in zip(df["longitude"], df["latitude"])]
    return gpd.GeoDataFrame(df.reset_index(drop=True), geometry=geometry, crs="EPSG:4326")


def load_fixture(fixture_path: Path, cfg: PipelineConfig) -> gpd.GeoDataFrame:
    df = pd.read_csv(fixture_path, dtype=str)
    df = df[df["state"].str.upper() == "UT"].copy()
    records = []
    for idx, row in df.iterrows():
        record = {
            "state_mine_id": row.get("state_mine_id") or f"UTROW{idx}",
            "permit_number": row.get("permit_number"),
            "mine_name": row.get("mine_name"),
            "operator_name": row.get("operator_name"),
            "permit_status": row.get("permit_status"),
            "mine_status": row.get("mine_status"),
            "mine_class": row.get("mine_class"),
            "commodity": row.get("commodity"),
            "county": row.get("county"),
            "latitude": coerce_float(row.get("latitude")),
            "longitude": coerce_float(row.get("longitude")),
            "source_url": str(fixture_path),
            "raw_source_json": compact_json(row.to_dict()),
        }
        permit_active, production_indicator = _status_flags(record)
        record["state_permit_active"] = permit_active
        record["state_production_indicator"] = production_indicator
        records.append(record)
    return _finalize(pd.DataFrame(records), cfg)


def load_state_mines(
    cfg: PipelineConfig,
    cache: CacheManager,
    client: ArcGISClient,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[gpd.GeoDataFrame, SourceStatus]:
    status = SourceStatus(source_id="utah_dogm", retrieved_at=utc_now_iso())
    if fixture_path is not None:
        gdf = load_fixture(fixture_path, cfg)
        status.status = "success"
        status.resolved_url = str(fixture_path)
        status.record_count = len(gdf)
        status.message = "Loaded from local fixture"
        return gdf, status

    def fetch() -> bytes:
        layer_url, diagnostics = resolve_dogm_layer(client)
        status.resolved_url = layer_url
        fc = client.fetch_features_geojson(layer_url)
        payload = {"layer_url": layer_url, "diagnostics": diagnostics, "collection": fc}
        return json.dumps(payload).encode("utf-8")

    source = get_source("utah_dogm")
    content, info = cache.get(
        key="utah_dogm",
        suffix=".json",
        fetch=fetch,
        ttl_hours=source.cache_ttl_hours,
        use_cache_only=use_cache_only,
        refresh=refresh,
    )
    payload = json.loads(content)
    status.resolved_url = payload.get("layer_url")
    status.cache_used = info["cache_used"]
    status.cache_age_hours = info["cache_age_hours"]
    features = payload.get("collection", {}).get("features", [])
    if not features:
        raise SourceUnavailableError("Utah DOGM layer contained no features")
    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    out = normalize_dogm(gdf, cfg, status.resolved_url or "")
    status.record_count = len(out)
    status.status = "degraded" if info.get("stale") else (
        "cached" if info["cache_used"] else "success"
    )
    return out, status
