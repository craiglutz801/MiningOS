from __future__ import annotations

import geopandas as gpd


def to_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to WGS 84 (EPSG:4326).  Assumes 4326 if CRS is unknown."""
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf.to_crs("EPSG:4326")


def ensure_geom(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop rows with null geometry; raise if the column is missing."""
    if "geometry" not in gdf.columns:
        raise ValueError("GeoDataFrame missing geometry column")
    return gdf[gdf.geometry.notnull()].copy()
