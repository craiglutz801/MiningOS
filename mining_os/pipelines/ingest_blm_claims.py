"""
Ingest BLM mining claims (open + closed) from ArcGIS REST.

Layer indices may change — update the constants below if BLM restructures
the MapServer.  The root service URL is:
  https://gis.blm.gov/nlsdb/rest/services/Mining_Claims/MiningClaims/MapServer
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
from sqlalchemy import text

from mining_os.db import get_engine, vacuum_analyze
from mining_os.geo import ensure_geom, to_wgs84
from mining_os.pipelines.arcgis import arcgis_layer_fields, arcgis_query_geojson, best_field

log = logging.getLogger("mining_os.ingest_blm_claims")

BLM_MINING_CLAIMS_OPEN_LAYER = (
    "https://gis.blm.gov/nlsdb/rest/services/Mining_Claims/MiningClaims/MapServer/0"
)
BLM_MINING_CLAIMS_CLOSED_LAYER = (
    "https://gis.blm.gov/nlsdb/rest/services/Mining_Claims/MiningClaims/MapServer/1"
)


def _normalize_claims(gdf: gpd.GeoDataFrame, layer_url: str) -> gpd.GeoDataFrame:
    """Map raw ArcGIS fields to our canonical schema using introspection."""
    gdf = to_wgs84(ensure_geom(gdf))

    fields = arcgis_layer_fields(layer_url)

    def pick(*names: str) -> str | None:
        """Try introspected metadata first, then fall back to column names."""
        hit = best_field(fields, *names) if fields else None
        if hit and hit in gdf.columns:
            return hit
        cols_lower = {c.lower(): c for c in gdf.columns}
        for n in names:
            if n.lower() in cols_lower:
                return cols_lower[n.lower()]
        return None

    objid = pick("OBJECTID", "objectid")
    state = pick("STATE", "state", "state_abbr", "admin_state", "ADMIN_ST")
    claim_name = pick("CLAIM_NAME", "claim_name", "name", "CLAIM_NM")
    serial = pick("SERIAL_NUM", "serial_num", "serialno", "serial_number", "SERIAL_NR")
    claim_type = pick("CLAIM_TYPE", "claim_type", "type", "CLM_TYP")
    disposition = pick("CASE_DISPOSITION", "case_disposition", "disposition", "DISP")
    status = pick("CASE_STATUS", "case_status", "status", "CASE_STAT")

    out = gdf.copy()
    out["source_objectid"] = out[objid] if objid else None
    out["state_abbr"] = out[state] if state else None
    out["claim_name"] = out[claim_name] if claim_name else None
    out["serial_num"] = out[serial] if serial else None
    out["claim_type"] = out[claim_type] if claim_type else None
    out["case_disposition"] = out[disposition] if disposition else None
    out["case_status"] = out[status] if status else None
    out["admin_state"] = out["state_abbr"]

    keep = [
        "source_objectid", "state_abbr", "claim_name", "serial_num",
        "claim_type", "case_disposition", "case_status", "admin_state",
        "geometry",
    ]
    out = out[[c for c in keep if c in out.columns]].rename(columns={"geometry": "geom"})
    out = gpd.GeoDataFrame(out, geometry="geom", crs="EPSG:4326")
    return out


def _write_claims(table: str, gdf: gpd.GeoDataFrame) -> None:
    eng = get_engine()

    with eng.begin() as conn:
        conn.execute(text(f"TRUNCATE {table};"))

    gdf["geom_wkt"] = gdf["geom"].to_wkt()
    df = pd.DataFrame(gdf.drop(columns=["geom"]))
    df.to_sql(table, eng, if_exists="append", index=False, method="multi", chunksize=2000)

    with eng.begin() as conn:
        conn.execute(text(f"UPDATE {table} SET geom = ST_Multi(ST_GeomFromText(geom_wkt, 4326));"))
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS geom_wkt;"))
        conn.execute(text(f"UPDATE {table} SET geom_centroid = ST_Centroid(geom)::geography;"))

    vacuum_analyze(table)
    log.info("Wrote %s rows to %s", len(gdf), table)


def ingest_open(max_records: int | None = None) -> None:
    log.info("Downloading BLM OPEN claims...")
    gdf = arcgis_query_geojson(BLM_MINING_CLAIMS_OPEN_LAYER, where="1=1", out_fields="*", max_records=max_records)
    if gdf.empty:
        log.warning("No open claims returned — skipping write.")
        return
    gdf = _normalize_claims(gdf, BLM_MINING_CLAIMS_OPEN_LAYER)
    log.info("Open claims rows: %s", len(gdf))
    _write_claims("blm_claims_open", gdf)


def ingest_closed(max_records: int | None = None) -> None:
    log.info("Downloading BLM CLOSED claims...")
    gdf = arcgis_query_geojson(BLM_MINING_CLAIMS_CLOSED_LAYER, where="1=1", out_fields="*", max_records=max_records)
    if gdf.empty:
        log.warning("No closed claims returned — skipping write.")
        return
    gdf = _normalize_claims(gdf, BLM_MINING_CLAIMS_CLOSED_LAYER)
    log.info("Closed claims rows: %s", len(gdf))
    _write_claims("blm_claims_closed", gdf)
