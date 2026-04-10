"""
Generic ArcGIS REST helpers: count, paged GeoJSON download, and layer
introspection (logs available fields so you can adapt to schema drift).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import geopandas as gpd
import requests

log = logging.getLogger("mining_os.arcgis")


# ------------------------------------------------------------------
# Introspect layer metadata (field names, types)
# ------------------------------------------------------------------

def arcgis_layer_fields(layer_url: str) -> List[Dict[str, Any]]:
    """Fetch field metadata from an ArcGIS MapServer / FeatureServer layer."""
    r = requests.get(layer_url, params={"f": "json"}, timeout=60)
    r.raise_for_status()
    meta = r.json()
    fields = meta.get("fields", [])
    if fields:
        log.info(
            "Layer %s — %d fields: %s",
            layer_url.rsplit("/", 1)[-1],
            len(fields),
            ", ".join(f["name"] for f in fields),
        )
    else:
        log.warning("No field metadata returned for %s", layer_url)
    return fields


def best_field(fields: List[Dict[str, Any]], *candidates: str) -> str | None:
    """Return the first field name that matches any candidate (case-insensitive)."""
    names_lower = {f["name"].lower(): f["name"] for f in fields}
    for c in candidates:
        if c.lower() in names_lower:
            return names_lower[c.lower()]
    return None


# ------------------------------------------------------------------
# Query helpers
# ------------------------------------------------------------------

def arcgis_query_count(layer_url: str, where: str = "1=1") -> int:
    params = {"where": where, "returnCountOnly": "true", "f": "json"}
    r = requests.get(f"{layer_url}/query", params=params, timeout=60)
    r.raise_for_status()
    j = r.json()
    if "count" not in j:
        raise RuntimeError(f"Unexpected count response: {j}")
    return int(j["count"])


def arcgis_query_geojson(
    layer_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    chunk: int = 2000,
    max_records: int | None = None,
) -> gpd.GeoDataFrame:
    """
    Pull ALL records from an ArcGIS REST Feature/MapServer layer using
    offset-based paging.  Returns a GeoDataFrame in EPSG:4326.
    """
    total = arcgis_query_count(layer_url, where=where)
    if max_records is not None:
        total = min(total, max_records)
    log.info("Fetching up to %s features from %s", total, layer_url)

    features: list = []
    for offset in range(0, total, chunk):
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": min(chunk, total - offset),
        }
        r = requests.get(f"{layer_url}/query", params=params, timeout=180)
        r.raise_for_status()
        gj = r.json()
        feats = gj.get("features", [])
        features.extend(feats)
        log.info("Fetched %s features (offset=%s, cumulative=%s)", len(feats), offset, len(features))

    if not features:
        log.warning("Zero features returned from %s", layer_url)
        return gpd.GeoDataFrame()

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    return gdf
