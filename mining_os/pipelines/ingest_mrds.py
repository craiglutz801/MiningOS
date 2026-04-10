"""
Ingest USGS MRDS (Mineral Resource Data System) occurrences from ArcGIS REST.

Service root:
  https://energy.usgs.gov/arcgis/rest/services/MRData/Mineral_Resource_Data_System/MapServer

MRDS is global; MVP limits to 20 k rows by default.  Constrain via --max-records
or add a state/bbox WHERE clause for production runs.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
from sqlalchemy import text

from mining_os.db import get_engine, vacuum_analyze
from mining_os.geo import ensure_geom, to_wgs84
from mining_os.pipelines.arcgis import arcgis_layer_fields, arcgis_query_geojson, best_field

log = logging.getLogger("mining_os.ingest_mrds")

MRDS_LAYER = (
    "https://energy.usgs.gov/arcgis/rest/services/MRData/Mineral_Resource_Data_System/MapServer/0"
)


def _normalize_mrds(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = to_wgs84(ensure_geom(gdf))

    fields = arcgis_layer_fields(MRDS_LAYER)

    def pick(*names: str) -> str | None:
        hit = best_field(fields, *names) if fields else None
        if hit and hit in gdf.columns:
            return hit
        cols_lower = {c.lower(): c for c in gdf.columns}
        for n in names:
            if n.lower() in cols_lower:
                return cols_lower[n.lower()]
        return None

    mrds_id = pick("MRDS_ID", "mrds_id", "dep_id", "id", "DEP_ID")
    name = pick("NAME", "name", "site_name", "SITE_NAME")
    state = pick("STATE", "state", "state_abbr", "st", "STATE_LOCI")
    comm = pick("COMMODITY", "commodity", "commodities", "comm", "COMMOD_ALL", "COMMODITIES")
    ref = pick("REFERENCE", "reference", "ref_text", "references", "source", "URL")

    out = gdf.copy()
    out["mrds_id"] = out[mrds_id] if mrds_id else None
    out["name"] = out[name] if name else None
    out["state_abbr"] = out[state] if state else None

    # Commodities may arrive as a single delimited string — normalise to list
    if comm and comm in out.columns:
        raw_comm = out[comm].astype(str).fillna("")
    else:
        raw_comm = pd.Series("", index=out.index)

    out["commodities"] = (
        raw_comm.str.lower()
        .str.replace(";", ",", regex=False)
        .str.replace("|", ",", regex=False)
        .str.split(",")
        .apply(lambda lst: [s.strip() for s in lst if s.strip()] if isinstance(lst, list) else [])
    )

    out["reference_text"] = out[ref] if ref else None

    keep = ["mrds_id", "name", "state_abbr", "commodities", "reference_text", "geometry"]
    out = out[[c for c in keep if c in out.columns]].rename(columns={"geometry": "geom"})
    out = gpd.GeoDataFrame(out, geometry="geom", crs="EPSG:4326")
    return out


def _pg_array_literal(lst: list) -> str:
    """Convert a Python list to a PostgreSQL array literal string."""
    if not lst:
        return "{}"
    escaped = [str(v).replace("\\", "\\\\").replace('"', '\\"') for v in lst]
    return "{" + ",".join(f'"{e}"' for e in escaped) + "}"


def ingest(max_records: int | None = 20000) -> None:
    log.info("Downloading MRDS occurrences...")
    gdf = arcgis_query_geojson(MRDS_LAYER, where="1=1", out_fields="*", max_records=max_records)
    if gdf.empty:
        log.warning("No MRDS records returned — skipping write.")
        return
    gdf = _normalize_mrds(gdf)
    log.info("MRDS rows: %s", len(gdf))

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE mrds_occurrences;"))

    # Serialise commodity lists as PG array literals so to_sql stores them
    # correctly into a TEXT[] column.
    gdf["geom_wkt"] = gdf["geom"].to_wkt()
    gdf["commodities"] = gdf["commodities"].apply(_pg_array_literal)
    df = pd.DataFrame(gdf.drop(columns=["geom"]))
    df.to_sql("mrds_occurrences", eng, if_exists="append", index=False, method="multi", chunksize=2000)

    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE mrds_occurrences "
            "SET geom = ST_GeogFromText('SRID=4326;' || geom_wkt);"
        ))
        conn.execute(text("ALTER TABLE mrds_occurrences DROP COLUMN IF EXISTS geom_wkt;"))

    vacuum_analyze("mrds_occurrences")
    log.info("MRDS occurrences ingested.")
