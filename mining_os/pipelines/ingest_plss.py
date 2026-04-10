"""
Ingest BLM National PLSS Sections from ArcGIS REST.

Root service:
  https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
from sqlalchemy import text

from mining_os.db import get_engine, vacuum_analyze
from mining_os.geo import ensure_geom, to_wgs84
from mining_os.pipelines.arcgis import arcgis_layer_fields, arcgis_query_geojson, best_field

log = logging.getLogger("mining_os.ingest_plss")

PLSS_SECTIONS_LAYER = (
    "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer/2"
)


def _normalize_plss(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = to_wgs84(ensure_geom(gdf))

    fields = arcgis_layer_fields(PLSS_SECTIONS_LAYER)

    def pick(*names: str) -> str | None:
        hit = best_field(fields, *names) if fields else None
        if hit and hit in gdf.columns:
            return hit
        cols_lower = {c.lower(): c for c in gdf.columns}
        for n in names:
            if n.lower() in cols_lower:
                return cols_lower[n.lower()]
        return None

    state = pick("STATE", "state", "st_abbr", "state_abbr", "STATEABBR")
    mer = pick("MERIDIAN", "meridian", "principalmeridian", "pm", "PRINMERCD")
    twn = pick("TOWNSHIP", "township", "twp", "twpnum", "TWNSHPNO")
    rng = pick("RANGE", "range", "rng", "rngnum", "RANGENO")
    sec = pick("SECTION", "section", "sec", "secnum", "FRSTDIVNO")

    out = gdf.copy()
    out["state_abbr"] = out[state] if state else None
    out["meridian"] = out[mer] if mer else None
    out["township"] = out[twn] if twn else None
    out["range"] = out[rng] if rng else None
    out["section"] = out[sec] if sec else None

    out["trs"] = (
        out["township"].astype(str).fillna("")
        + "-" + out["range"].astype(str).fillna("")
        + "-" + out["section"].astype(str).fillna("")
    ).str.replace("--", "-", regex=False)

    keep = ["state_abbr", "meridian", "township", "range", "section", "trs", "geometry"]
    out = out[[c for c in keep if c in out.columns]].rename(columns={"geometry": "geom"})
    out = gpd.GeoDataFrame(out, geometry="geom", crs="EPSG:4326")
    return out


def ingest(max_records: int | None = None) -> None:
    log.info("Downloading PLSS sections...")
    gdf = arcgis_query_geojson(PLSS_SECTIONS_LAYER, where="1=1", out_fields="*", max_records=max_records)
    if gdf.empty:
        log.warning("No PLSS sections returned — skipping write.")
        return
    gdf = _normalize_plss(gdf)
    log.info("PLSS section rows: %s", len(gdf))

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE plss_sections;"))

    gdf["geom_wkt"] = gdf["geom"].to_wkt()
    df = pd.DataFrame(gdf.drop(columns=["geom"]))
    df.to_sql("plss_sections", eng, if_exists="append", index=False, method="multi", chunksize=2000)

    with eng.begin() as conn:
        conn.execute(text("UPDATE plss_sections SET geom = ST_Multi(ST_GeomFromText(geom_wkt, 4326));"))
        conn.execute(text("ALTER TABLE plss_sections DROP COLUMN IF EXISTS geom_wkt;"))

    vacuum_analyze("plss_sections")
    log.info("PLSS sections ingested.")
