"""Nevada NDEP BMRR Active Mine evidence adapter.

Official source: NDEP eMap BMRR FeatureServer
  https://ndep-emap.ndep.nv.gov/arcgis/rest/services/eMap_Services/eMap_BMRR/FeatureServer

Layer 1 "BMRR Regulation Sites" is described by NDEP as active regulation
sites (updated nightly). Layer 0 is reclamation sites.

BMRR status, permit, inspection, and closure fields are regulatory /
facility evidence only. They are never used to label a mine Producing.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from mining_os.active_mine_intel.evidence.freshness import apply_outcome, source_outcome
from mining_os.active_mine_intel.matcher.arcgis_client import ArcGISClient, resolve_field
from mining_os.active_mine_intel.matcher.config import PipelineConfig
from mining_os.active_mine_intel.matcher.download_client import CacheManager
from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.models import SourceStatus, SourceUnavailableError
from mining_os.active_mine_intel.matcher.source_registry import get_source
from mining_os.active_mine_intel.matcher.utilities import coerce_float, compact_json, utc_now_iso

log = get_logger("mcm.ndep_bmrr")

_FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "project_id": (["ProjectID", "Project_ID", "PROJECTID"], ["project id"]),
    "permit_number": (["PERMIT_NO", "PERMIT_NUMBER", "PermitNo"], ["permit"]),
    "mine_name": (["ProjectName", "PROJECTNAME", "NAME", "MINE_NAME"], ["project name", "mine name"]),
    "physical_status": (["PhysicalStatus", "PHYSICALSTATUS"], ["physical status"]),
    "permit_status": (["PermitStatus", "PERMITSTATUS"], ["permit status"]),
    "site_type": (["SiteType", "SITE_TYPE"], ["site type"]),
    "closure": (["Closure", "CLOSURE"], ["closure"]),
    "location_taken": (["LocationTaken", "LOCATIONTAKEN"], ["location taken"]),
    "collection_method": (["CollectionMethod", "COLLECTIONMETHOD"], ["collection"]),
    "date_effective": (["DatePermitEffective", "DATEPERMITEFFECTIVE"], ["effective"]),
    "date_expires": (["DatePermitExpires", "DATEPERMITEXPIRES"], ["expir"]),
    "latitude": (["LATITUDE", "LAT", "Y"], ["latitude"]),
    "longitude": (["LONGITUDE", "LON", "LONG", "X"], ["longitude"]),
}


def _empty_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=["bmrr_project_id", "permit_number", "mine_name", "latitude", "longitude", "geometry"],
        geometry=[],
        crs="EPSG:4326",
    )


def normalize_bmrr(
    gdf: gpd.GeoDataFrame,
    cfg: PipelineConfig,
    source_url: str,
    *,
    layer_kind: str,
) -> gpd.GeoDataFrame:
    if gdf.empty:
        return _empty_gdf()
    df = pd.DataFrame(gdf.drop(columns="geometry", errors="ignore")).reset_index(drop=True)
    fields = [{"name": c, "alias": c} for c in df.columns]
    resolved = {
        target: resolve_field(fields, candidates, keywords)
        for target, (candidates, keywords) in _FIELDS.items()
    }
    geometries = list(gdf.geometry) if "geometry" in gdf.columns else [None] * len(df)
    records: list[dict] = []
    for idx, row in df.iterrows():

        def get(key: str):
            col = resolved.get(key)
            value = row[col] if col and col in row.index else None
            if isinstance(value, str):
                value = value.strip() or None
            return value

        lat = coerce_float(get("latitude"))
        lon = coerce_float(get("longitude"))
        geom = geometries[idx]
        if geom is not None and not getattr(geom, "is_empty", True):
            point = geom.representative_point() if hasattr(geom, "representative_point") else geom
            lat = lat if lat is not None else getattr(point, "y", None)
            lon = lon if lon is not None else getattr(point, "x", None)
        records.append(
            {
                "bmrr_project_id": str(get("project_id") or get("permit_number") or f"BMRR{idx}"),
                "permit_number": get("permit_number"),
                "mine_name": get("mine_name"),
                "operator_name": None,
                "bmrr_physical_status": get("physical_status"),
                "bmrr_permit_status": get("permit_status"),
                "bmrr_site_type": get("site_type"),
                "bmrr_closure": get("closure"),
                "bmrr_layer_kind": layer_kind,
                "latitude": lat,
                "longitude": lon,
                "effective_date": str(get("date_effective") or "") or None,
                "source_url": source_url,
                "raw_source_json": compact_json(dict(row)),
            }
        )
    out = pd.DataFrame(records)
    if out.empty:
        return _empty_gdf()
    lon_min, lat_min, lon_max, lat_max = cfg.bounds
    out = out[
        out["latitude"].notna()
        & out["longitude"].notna()
        & out["latitude"].between(lat_min, lat_max)
        & out["longitude"].between(lon_min, lon_max)
    ].copy()
    geometry = [Point(xy) for xy in zip(out["longitude"], out["latitude"])]
    return gpd.GeoDataFrame(out.reset_index(drop=True), geometry=geometry, crs="EPSG:4326")


def load_fixture(
    fixture_path: Path, cfg: PipelineConfig, *, layer_kind: str
) -> gpd.GeoDataFrame:
    df = pd.read_csv(fixture_path, dtype=str)
    if "layer_kind" in df.columns:
        df = df[df["layer_kind"].str.lower() == layer_kind.lower()].copy()
    if "state" in df.columns:
        df = df[df["state"].str.upper() == "NV"].copy()
    gdf = gpd.GeoDataFrame(
        df,
        geometry=[
            Point(float(row["longitude"]), float(row["latitude"]))
            if row.get("longitude") not in (None, "") and row.get("latitude") not in (None, "")
            else None
            for _, row in df.iterrows()
        ],
        crs="EPSG:4326",
    )
    # Map CSV columns into the live schema names expected by normalize_bmrr.
    rename = {
        "project_id": "ProjectID",
        "permit_number": "PERMIT_NO",
        "mine_name": "ProjectName",
        "physical_status": "PhysicalStatus",
        "permit_status": "PermitStatus",
        "site_type": "SiteType",
        "closure": "Closure",
    }
    gdf = gdf.rename(columns={k: v for k, v in rename.items() if k in gdf.columns})
    return normalize_bmrr(gdf, cfg, str(fixture_path), layer_kind=layer_kind)


def _load_layer(
    *,
    source_id: str,
    layer_kind: str,
    cfg: PipelineConfig,
    cache: CacheManager,
    client: ArcGISClient,
    use_cache_only: bool,
    refresh: bool,
    fixture_path: Path | None,
) -> tuple[gpd.GeoDataFrame, SourceStatus]:
    status = SourceStatus(source_id=source_id, retrieved_at=utc_now_iso())
    source = get_source(source_id)
    if fixture_path is not None:
        gdf = load_fixture(fixture_path, cfg, layer_kind=layer_kind)
        outcome = source_outcome(fetched_ok=True, record_count=len(gdf), message="Loaded from local fixture")
        status.resolved_url = str(fixture_path)
        apply_outcome(status, outcome)
        if outcome["outcome"] == "empty":
            status.status = "empty"
        else:
            status.status = "success"
        status.message = outcome["message"]
        return gdf, status

    def fetch() -> bytes:
        url = source.resolved_override() or source.service_url
        if not url:
            raise SourceUnavailableError(f"{source_id} has no service URL")
        fc = client.fetch_features_geojson(url)
        return json.dumps({"layer_url": url, "collection": fc}).encode("utf-8")

    try:
        content, info = cache.get(
            key=source_id,
            suffix=".json",
            fetch=fetch,
            ttl_hours=source.cache_ttl_hours,
            use_cache_only=use_cache_only,
            refresh=refresh,
        )
    except SourceUnavailableError as exc:
        outcome = source_outcome(
            fetched_ok=False, record_count=0, message=str(exc), failure_class="unavailable"
        )
        apply_outcome(status, outcome)
        return _empty_gdf(), status

    payload = json.loads(content)
    status.resolved_url = payload.get("layer_url") or source.service_url
    status.cache_used = info["cache_used"]
    status.cache_age_hours = info["cache_age_hours"]
    features = payload.get("collection", {}).get("features", [])
    if info.get("stale"):
        outcome = source_outcome(
            fetched_ok=True,
            record_count=len(features),
            stale=True,
            cache_used=True,
        )
        apply_outcome(status, outcome)
        # Stale BMRR must not support assertions; still return geometry for overlay display.
        if features:
            gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
            out = normalize_bmrr(gdf, cfg, status.resolved_url or "", layer_kind=layer_kind)
            status.record_count = len(out)
        return (out if features else _empty_gdf()), status

    if not features:
        outcome = source_outcome(fetched_ok=True, record_count=0)
        apply_outcome(status, outcome)
        return _empty_gdf(), status

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    out = normalize_bmrr(gdf, cfg, status.resolved_url or "", layer_kind=layer_kind)
    outcome = source_outcome(
        fetched_ok=True,
        record_count=len(out),
        cache_used=info["cache_used"],
    )
    apply_outcome(status, outcome)
    if info["cache_used"] and status.status not in {"empty", "stale", "failed"}:
        status.status = "cached"
    return out, status


def load_bmrr_evidence(
    cfg: PipelineConfig,
    cache: CacheManager,
    client: ArcGISClient,
    *,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[gpd.GeoDataFrame, SourceStatus, gpd.GeoDataFrame, SourceStatus]:
    """Load regulation (active) and reclamation BMRR layers."""
    regulation, reg_status = _load_layer(
        source_id="ndep_bmrr_regulation",
        layer_kind="regulation",
        cfg=cfg,
        cache=cache,
        client=client,
        use_cache_only=use_cache_only,
        refresh=refresh,
        fixture_path=fixture_path,
    )
    reclamation, rec_status = _load_layer(
        source_id="ndep_bmrr_reclamation",
        layer_kind="reclamation",
        cfg=cfg,
        cache=cache,
        client=client,
        use_cache_only=use_cache_only,
        refresh=refresh,
        fixture_path=fixture_path,
    )
    return regulation, reg_status, reclamation, rec_status
