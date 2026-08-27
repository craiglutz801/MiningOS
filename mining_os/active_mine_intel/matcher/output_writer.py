"""Writes all pipeline outputs: CSV, GeoJSON, Parquet, QC report, run manifest."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from mining_os.active_mine_intel.matcher.config import SOFTWARE_VERSION
from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.models import PipelineResult
from mining_os.active_mine_intel.matcher.utilities import compact_json, write_json

log = get_logger("mcm.output")

SITE_CSV_COLUMNS = [
    "rank",
    "total_score",
    "confidence_category",
    "mine_name",
    "commodity",
    "operator_name",
    "county",
    "plss",
    "township",
    "range",
    "section",
    "plss_source",
    "mine_activity_label",
    "latest_production_activity_year",
    "claim_count",
    "claim_payment_status",
    "claims_paid",
    "claims_unpaid",
    "claims_unknown",
    "claims_checked",
    "best_match_type",
    "best_claim_serial_number",
    "best_claim_name",
    "best_distance_meters",
    "blm_plan_present",
    "blm_notice_present",
    "msha_status",
    "verification_status",
    "operational_status",
    "regulatory_status",
    "facility_type",
    "tenure_class",
    "verification_state",
    "fail_closed",
    "tenure_json",
    "contradictions_json",
    "assertions_json",
    "mine_site_id",
    "latitude",
    "longitude",
    "activity_score",
    "claim_match_score",
    "data_quality_score",
    "penalty_score",
    "all_claim_serial_numbers",
    "recommended_next_action",
    "score_breakdown_json",
    "evidence_summary_json",
]


def _stringify_complex(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col == "geometry":
            continue
        if out[col].map(lambda v: isinstance(v, (list, dict, tuple, set))).any():
            out[col] = out[col].map(
                lambda v: compact_json(list(v) if isinstance(v, (set, tuple)) else v)
                if isinstance(v, (list, dict, tuple, set))
                else v
            )
        elif str(out[col].dtype).startswith("datetime"):
            out[col] = out[col].astype(str)
    return out


def _write_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    _stringify_complex(gdf).to_file(path, driver="GeoJSON")


def write_outputs(
    output_dir: Path,
    site_summary: gpd.GeoDataFrame,
    matches: pd.DataFrame,
    mine_sites: gpd.GeoDataFrame,
    claims: gpd.GeoDataFrame,
    operations: gpd.GeoDataFrame,
    qc: dict,
    result: PipelineResult,
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    # --- Candidate sites (one row per mine site), ranked ---
    sites_df = pd.DataFrame(site_summary.drop(columns="geometry", errors="ignore"))
    for col in SITE_CSV_COLUMNS:
        if col not in sites_df.columns:
            sites_df[col] = None
    csv_path = output_dir / "candidate_sites.csv"
    _stringify_complex(sites_df[SITE_CSV_COLUMNS]).to_csv(csv_path, index=False)
    written.append(str(csv_path))

    geojson_path = output_dir / "candidate_sites.geojson"
    if isinstance(site_summary, gpd.GeoDataFrame) and not site_summary.empty:
        _write_geojson(site_summary, geojson_path)
    else:
        write_json(geojson_path, {"type": "FeatureCollection", "features": []})
    written.append(str(geojson_path))

    # --- Candidate matches (one row per mine x claim pair) ---
    matches_csv = output_dir / "candidate_matches.csv"
    matches_df = pd.DataFrame(matches).drop(columns=["geometry"], errors="ignore")
    _stringify_complex(matches_df).to_csv(matches_csv, index=False)
    written.append(str(matches_csv))

    matches_geojson = output_dir / "candidate_matches.geojson"
    if not matches_df.empty and "mine_longitude" in matches_df.columns:
        from shapely.geometry import Point

        match_gdf = gpd.GeoDataFrame(
            matches_df,
            geometry=[
                Point(xy) for xy in zip(matches_df["mine_longitude"], matches_df["mine_latitude"])
            ],
            crs="EPSG:4326",
        )
        _write_geojson(match_gdf, matches_geojson)
    else:
        write_json(matches_geojson, {"type": "FeatureCollection", "features": []})
    written.append(str(matches_geojson))

    # --- Parquet reference tables ---
    parquet_targets = [
        ("mine_sites.parquet", mine_sites),
        ("active_unpatented_claims.parquet", claims),
        ("blm_operations.parquet", operations),
    ]
    for name, frame in parquet_targets:
        path = output_dir / name
        try:
            if isinstance(frame, gpd.GeoDataFrame) and "geometry" in frame.columns:
                _stringify_complex(frame).to_parquet(path)
            else:
                _stringify_complex(pd.DataFrame(frame)).to_parquet(path)
            written.append(str(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not write %s: %s", name, exc)

    # --- QC report and manifest ---
    qc_path = output_dir / "quality_control.json"
    write_json(qc_path, qc)
    written.append(str(qc_path))

    manifest_path = output_dir / "run_manifest.json"
    write_json(manifest_path, result.manifest(SOFTWARE_VERSION))
    written.append(str(manifest_path))

    log.info("Wrote %d output files to %s", len(written), output_dir)
    return written
