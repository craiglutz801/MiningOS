"""BLM adapter: active mining claims, Plans of Operations, and Notices."""

from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd

from mining_os.active_mine_intel.matcher.arcgis_client import ArcGISClient, resolve_field
from mining_os.active_mine_intel.matcher.config import PipelineConfig
from mining_os.active_mine_intel.matcher.download_client import CacheManager
from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.models import DataValidationError, SourceStatus, SourceUnavailableError
from mining_os.active_mine_intel.matcher.normalize import normalize_entity_name, normalize_mine_name
from mining_os.active_mine_intel.matcher.source_registry import (
    CLAIM_TYPE_CODES,
    DEFAULT_ANALYTICAL_CLAIM_TYPES,
    RETAINED_HIDDEN_CLAIM_TYPES,
    get_source,
)
from mining_os.active_mine_intel.matcher.utilities import coerce_float, utc_now_iso

log = get_logger("mcm.blm")

# Configurable interpretation of BLM geometry-quality codes. Codes 0-3 have
# historically indicated direct PLSS-derived geometry; higher codes or
# descriptive values indicating section/county placement are coarse.
GOOD_QUALITY_CODES = {"0", "1", "2", "3"}
COARSE_QUALITY_KEYWORDS = ("section", "county", "protracted", "approximate", "centroid")

_CLAIM_FIELD_CANDIDATES: dict[str, tuple[list[str], list[str]]] = {
    "claim_serial_number": (
        ["CSE_NR", "MC_SERIAL_NBR", "CSE_SERIAL_NBR", "SERIAL_NBR", "claim_serial_number", "SER_NBR"],
        ["serial"],
    ),
    "legacy_claim_serial_number": (
        ["LEG_CSE_NR", "MC_LEGACY_SERIAL_NBR", "LEGACY_SERIAL_NBR", "legacy_claim_serial_number"],
        ["legacy"],
    ),
    "claim_name": (["CSE_NAME", "CLAIM_NAME", "MC_NAME", "claim_name"], ["claim name", "csename"]),
    "claim_type_code": (
        ["CSE_TYPE_NR", "PROD_CODE", "CSE_PROD_CODES", "MC_PROD_CODE", "claim_type_code"],
        ["prod code", "type nr"],
    ),
    "claim_type_text": (
        ["BLM_PROD", "CASETYPE", "CSE_TYPE_TXT", "CLAIM_TYPE", "claim_type"],
        ["case type"],
    ),
    "case_disposition": (
        ["CSE_DISP", "CSE_DISP_TXT", "DISP_TXT", "CASE_DISP", "case_disposition"],
        ["disp"],
    ),
    "recorded_acres": (["RCRD_ACRS", "RECORD_ACRES", "ACRES", "recorded_acres"], ["acr"]),
    "geometry_quality": (
        ["QLTY", "GEO_QLTY", "GEOM_QUALITY", "MAP_QLTY", "geometry_quality", "QUALITY"],
        ["qlty", "quality"],
    ),
    "patented_flag": (["MC_PATENTED", "PATENTED", "patented_flag"], ["patent"]),
    "conveyed_flag": (["MC_CONVEYED", "CONVEYED", "conveyed_flag"], ["convey"]),
    "excluded_flag": (["MC_EXCLUDED", "EXCLUDED", "excluded_flag"], ["exclud"]),
    "salesforce_id": (["SF_ID", "SALESFORCE_ID", "salesforce_id"], ["salesforce", "sf id"]),
    "cse_meta": (["CSE_META", "cse_meta"], ["cse meta", "meta"]),
    "state_geo": (["GEO_STATE", "STATE", "state"], ["geo state"]),
    "state_admin": (["ADMIN_STATE", "STATE_ADMIN"], ["admin state"]),
    "object_id": (["OBJECTID", "OBJECT_ID", "FID", "object_id"], ["objectid"]),
}

_OPERATION_FIELD_CANDIDATES: dict[str, tuple[list[str], list[str]]] = {
    "case_serial_number": (
        ["CSE_NR", "CSE_SERIAL_NBR", "SERIAL_NBR", "CASE_SERIAL", "case_serial_number"],
        ["serial"],
    ),
    "operation_name": (["CSE_NAME", "CASE_NAME", "PROJECT_NAME", "operation_name", "NAME"], ["name"]),
    "operator_name": (
        ["CUST_NM_SEC", "OPERATOR", "OPERATOR_NAME", "CLAIMANT", "operator_name"],
        ["operator", "customer"],
    ),
    "case_disposition": (
        ["CSE_DISP", "CSE_DISP_TXT", "DISP_TXT", "DISPOSITION", "case_disposition"],
        ["disp"],
    ),
    "product_code": (["CSE_TYPE_NR", "PROD_CODE", "CSE_PROD_CODES", "product_code"], ["prod code"]),
    "geometry_quality": (
        ["QLTY", "GEO_QLTY", "GEOM_QUALITY", "geometry_quality", "QUALITY"],
        ["qlty", "quality"],
    ),
    "state_geo": (["GEO_STATE", "STATE", "state"], ["geo state"]),
    "state_admin": (["ADMIN_STATE", "STATE_ADMIN"], ["admin state"]),
    "object_id": (["OBJECTID", "OBJECT_ID", "FID", "object_id"], ["objectid"]),
}


def _df_fields(df: pd.DataFrame) -> list[dict]:
    return [{"name": col, "alias": col} for col in df.columns]


def _resolve_columns(
    df: pd.DataFrame, spec: dict[str, tuple[list[str], list[str]]]
) -> dict[str, str | None]:
    fields = _df_fields(df)
    return {
        target: resolve_field(fields, candidates, keywords)
        for target, (candidates, keywords) in spec.items()
    }


def _col(df: pd.DataFrame, name: str | None) -> pd.Series:
    if name and name in df.columns:
        return df[name]
    return pd.Series([None] * len(df), index=df.index, dtype=object)


def _yn(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().eq("Y")


def build_case_page(salesforce_id: str | None, serial: str | None) -> str | None:
    """MLRS public case URL used for maintenance-fee / payment status checks."""
    sf = str(salesforce_id or "").strip()
    serial_text = str(serial or "").strip()
    if not sf or not serial_text or sf.lower() in {"none", "nan", "null"}:
        return None
    return f"https://mlrs.blm.gov/s/blm-case/{sf}/{serial_text}"


def _combined_state(gdf: gpd.GeoDataFrame, cols: dict[str, str | None]) -> pd.Series:
    """Geographic state where populated, otherwise administrative state.

    The live MLRS layers leave GEO_STATE null for most records and rely on
    ADMIN_STATE instead.
    """

    def clean(series: pd.Series) -> pd.Series:
        text = series.astype(str).str.strip().str.upper()
        return text.where(~text.isin(["", "NONE", "NAN", "NULL"]), None)

    geo = clean(_col(gdf, cols.get("state_geo")))
    admin = clean(_col(gdf, cols.get("state_admin")))
    return geo.fillna(admin)


def classify_geometry_quality(value) -> str:
    """Group a BLM geometry-quality value; never guess unknown codes.

    Live MLRS layers store quality as a leading code plus verbose retrieval text
    (e.g. ``"0: \\n36 sections retrieved..."``), so the leading code is
    authoritative when present.
    """
    if value is None or (isinstance(value, float) and value != value):
        return "UNKNOWN"
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return "UNKNOWN"
    leading = re.match(r"\s*(\d+)\s*[:.]?", text)
    if leading:
        # Known good codes are direct PLSS retrievals; other codes are exposed
        # as UNKNOWN rather than guessed.
        return "PLSS_DIRECT" if leading.group(1) in GOOD_QUALITY_CODES else "UNKNOWN"
    lowered = text.lower()
    if "direct" in lowered or "plss match" in lowered:
        return "PLSS_DIRECT"
    if any(kw in lowered for kw in COARSE_QUALITY_KEYWORDS):
        return "COARSE"
    return "UNKNOWN"


def _repair_geometries(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, int]:
    from shapely.validation import make_valid

    repaired = 0
    invalid_mask = ~gdf.geometry.is_valid & gdf.geometry.notna()
    if invalid_mask.any():
        gdf.loc[invalid_mask, "geometry"] = gdf.loc[invalid_mask, "geometry"].apply(make_valid)
        repaired = int(invalid_mask.sum())
        log.info("Repaired %d invalid geometries with make_valid", repaired)
    return gdf, repaired


def _claim_type_from_row(code, text) -> str | None:
    if code is not None:
        token = str(code).strip().split(".")[0]
        if token in CLAIM_TYPE_CODES:
            return CLAIM_TYPE_CODES[token]
    if text is not None:
        lowered = str(text).lower()
        for label in DEFAULT_ANALYTICAL_CLAIM_TYPES + RETAINED_HIDDEN_CLAIM_TYPES:
            if label.lower().split()[0] in lowered:
                return label
    return None


def normalize_claims(
    gdf: gpd.GeoDataFrame, cfg: PipelineConfig, source_url: str
) -> tuple[gpd.GeoDataFrame, dict]:
    """Normalize raw BLM claim features and apply the unpatented-active filter."""
    stats = {"claims_downloaded": len(gdf), "claims_invalid_geometry_repaired": 0}
    if gdf.empty:
        raise DataValidationError("Zero BLM claims downloaded; likely schema/filter error")

    cols = _resolve_columns(pd.DataFrame(gdf.drop(columns="geometry")), _CLAIM_FIELD_CANDIDATES)
    out = gpd.GeoDataFrame(index=gdf.index, geometry=gdf.geometry, crs=gdf.crs or "EPSG:4326")
    out["object_id"] = _col(gdf, cols["object_id"])
    out["state"] = _combined_state(gdf, cols)
    out["claim_serial_number"] = _col(gdf, cols["claim_serial_number"]).astype(str).str.strip()
    out["legacy_claim_serial_number"] = _col(gdf, cols["legacy_claim_serial_number"])
    out["claim_name"] = _col(gdf, cols["claim_name"])
    out["claim_name_normalized"] = out["claim_name"].map(normalize_mine_name)
    codes = _col(gdf, cols["claim_type_code"])
    texts = _col(gdf, cols["claim_type_text"])
    out["claim_type_code"] = codes
    out["claim_type"] = [
        _claim_type_from_row(c, t) for c, t in zip(codes.tolist(), texts.tolist())
    ]
    out["case_disposition"] = _col(gdf, cols["case_disposition"])
    out["recorded_acres"] = _col(gdf, cols["recorded_acres"]).map(coerce_float)
    out["geometry_quality"] = _col(gdf, cols["geometry_quality"])
    out["geometry_quality_group"] = out["geometry_quality"].map(classify_geometry_quality)
    out["patented_flag"] = _yn(_col(gdf, cols["patented_flag"]))
    out["conveyed_flag"] = _yn(_col(gdf, cols["conveyed_flag"]))
    out["excluded_flag"] = _yn(_col(gdf, cols["excluded_flag"]))
    sf = _col(gdf, cols.get("salesforce_id"))
    out["salesforce_id"] = sf.astype(str).str.strip().where(
        ~sf.astype(str).str.strip().str.upper().isin(["", "NONE", "NAN", "NULL"]),
        None,
    )
    meta = _col(gdf, cols.get("cse_meta"))
    out["cse_meta"] = meta
    out["case_page"] = [
        build_case_page(sf_id, serial)
        for sf_id, serial in zip(out["salesforce_id"], out["claim_serial_number"])
    ]
    out["source_url"] = source_url
    out["claim_id"] = (
        cfg.state_code + "-CLM-" + out["claim_serial_number"].fillna("").astype(str)
    )

    # De-duplicate by object id and serial number.
    out = out.drop_duplicates(subset=["claim_serial_number"], keep="first")

    in_state = (out["state"] == cfg.state_code) & out.geometry.notna()
    patented_gdf = out.loc[in_state & out["patented_flag"]].copy()
    conveyed_gdf = out.loc[in_state & out["conveyed_flag"] & ~out["patented_flag"]].copy()
    stats["patented_claim_count"] = int(len(patented_gdf))
    stats["conveyed_claim_count"] = int(len(conveyed_gdf))
    # Retained for mixed-tenure overlay only — not used as operational evidence.
    stats["_patented_claims"] = patented_gdf.reset_index(drop=True)
    stats["_conveyed_claims"] = conveyed_gdf.reset_index(drop=True)

    # Filters: state, unpatented, not conveyed, not excluded, recognized type.
    # Not Closed / active unpatented polygons remain the matcher claim set
    # (tenure evidence for unpatented; never operational status).
    mask = (
        in_state
        & ~out["patented_flag"]
        & ~out["conveyed_flag"]
        & ~out["excluded_flag"]
        & out["claim_type"].notna()
    )
    out = out.loc[mask].copy()
    out, repaired = _repair_geometries(out)
    stats["claims_invalid_geometry_repaired"] = repaired
    stats["claims_after_unpatented_filter"] = len(out)
    if out.empty:
        raise DataValidationError(
            "Zero claims remained after filtering; likely schema/filter error"
        )
    log.info(
        "Claims normalized: %d downloaded -> %d active unpatented analytical/hidden claims",
        stats["claims_downloaded"],
        len(out),
    )
    return out.reset_index(drop=True), stats


def normalize_operations(
    gdf: gpd.GeoDataFrame, cfg: PipelineConfig, kind: str, source_url: str
) -> gpd.GeoDataFrame:
    if gdf.empty:
        return _empty_operations()
    cols = _resolve_columns(pd.DataFrame(gdf.drop(columns="geometry")), _OPERATION_FIELD_CANDIDATES)
    out = gpd.GeoDataFrame(index=gdf.index, geometry=gdf.geometry, crs=gdf.crs or "EPSG:4326")
    out["state"] = _combined_state(gdf, cols)
    out["operation_kind"] = kind
    out["case_serial_number"] = _col(gdf, cols["case_serial_number"]).astype(str).str.strip()
    out["operation_name"] = _col(gdf, cols["operation_name"])
    out["operation_name_normalized"] = out["operation_name"].map(normalize_mine_name)
    out["operator_name"] = _col(gdf, cols["operator_name"])
    out["operator_name_normalized"] = out["operator_name"].map(normalize_entity_name)
    out["case_disposition"] = _col(gdf, cols["case_disposition"])
    out["product_code"] = _col(gdf, cols["product_code"])
    out["geometry_quality"] = _col(gdf, cols["geometry_quality"])
    out["source_url"] = source_url
    out["operation_id"] = (
        cfg.state_code + f"-{kind.upper()}-" + out["case_serial_number"].fillna("").astype(str)
    )
    mask = (out["state"] == cfg.state_code) & out.geometry.notna()
    out = out.loc[mask].drop_duplicates(subset=["operation_id"]).copy()
    out, _ = _repair_geometries(out)
    return out.reset_index(drop=True)


def _empty_operations() -> gpd.GeoDataFrame:
    columns = [
        "operation_id",
        "state",
        "operation_kind",
        "case_serial_number",
        "operation_name",
        "operation_name_normalized",
        "operator_name",
        "operator_name_normalized",
        "case_disposition",
        "product_code",
        "geometry_quality",
        "source_url",
    ]
    return gpd.GeoDataFrame({c: [] for c in columns}, geometry=[], crs="EPSG:4326")


def _fetch_layer_geojson(
    source_id: str,
    cfg: PipelineConfig,
    cache: CacheManager,
    client: ArcGISClient,
    use_cache_only: bool,
    refresh: bool,
) -> tuple[gpd.GeoDataFrame, SourceStatus]:
    source = get_source(source_id)
    status = SourceStatus(source_id=source_id, resolved_url=source.service_url)

    def _state_where() -> str:
        """Filter on whichever state fields the layer actually exposes.

        GEO_STATE is frequently null in live MLRS layers while ADMIN_STATE is
        populated, so both are included when available.
        """
        try:
            meta = client.get_layer_metadata(source.service_url)
            names = {f.get("name", "").upper() for f in meta.get("fields", [])}
        except Exception:  # noqa: BLE001 - fall back to the documented field
            names = {"GEO_STATE"}
        clauses = [
            f"{field} = '{cfg.state_code}'"
            for field in ("GEO_STATE", "ADMIN_STATE")
            if field in names
        ]
        return " OR ".join(clauses) if clauses else "1=1"

    def fetch() -> bytes:
        fc = client.fetch_features_geojson(
            source.service_url, where=_state_where(), batch_size=1000
        )
        return json.dumps(fc).encode("utf-8")

    content, info = cache.get(
        key=f"{source_id}_{cfg.state_code}",
        suffix=".geojson",
        fetch=fetch,
        ttl_hours=source.cache_ttl_hours,
        use_cache_only=use_cache_only,
        refresh=refresh,
    )
    status.cache_used = info["cache_used"]
    status.cache_age_hours = info["cache_age_hours"]
    status.retrieved_at = utc_now_iso()
    fc = json.loads(content)
    features = fc.get("features", [])
    if features:
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    else:
        gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    status.record_count = len(gdf)
    if info.get("stale"):
        status.status = "stale"
        status.outcome = "stale"
        status.usable_for_assertions = False
        status.failure_class = "stale"
        status.message = f"Using stale cache (age {info['cache_age_hours']} h) after failed refresh"
    elif not features:
        status.status = "empty"
        status.outcome = "empty"
        status.usable_for_assertions = True
        status.message = "Valid zero-result response"
    else:
        status.status = "cached" if info["cache_used"] else "success"
        status.outcome = "ok"
        status.usable_for_assertions = True
    return gdf, status


def load_claims(
    cfg: PipelineConfig,
    cache: CacheManager,
    client: ArcGISClient,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[gpd.GeoDataFrame, SourceStatus, dict]:
    source = get_source("blm_claims")
    if fixture_path is not None:
        gdf = gpd.read_file(fixture_path)
        status = SourceStatus(
            source_id="blm_claims",
            status="success",
            resolved_url=str(fixture_path),
            retrieved_at=utc_now_iso(),
            record_count=len(gdf),
            message="Loaded from local fixture",
        )
        claims, stats = normalize_claims(gdf, cfg, str(fixture_path))
        return claims, status, stats
    gdf, status = _fetch_layer_geojson("blm_claims", cfg, cache, client, use_cache_only, refresh)
    claims, stats = normalize_claims(gdf, cfg, source.service_url or "")
    status.record_count = len(claims)
    return claims, status, stats


def load_operations(
    kind: str,
    cfg: PipelineConfig,
    cache: CacheManager,
    client: ArcGISClient,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[gpd.GeoDataFrame, SourceStatus]:
    source_id = "blm_plans" if kind == "plan" else "blm_notices"
    source = get_source(source_id)
    if fixture_path is not None:
        if fixture_path.exists():
            gdf = gpd.read_file(fixture_path)
            ops = normalize_operations(gdf, cfg, kind, str(fixture_path))
            return ops, SourceStatus(
                source_id=source_id,
                status="success",
                resolved_url=str(fixture_path),
                retrieved_at=utc_now_iso(),
                record_count=len(ops),
                message="Loaded from local fixture",
            )
        return _empty_operations(), SourceStatus(
            source_id=source_id, status="skipped", message="Fixture not present"
        )
    try:
        gdf, status = _fetch_layer_geojson(source_id, cfg, cache, client, use_cache_only, refresh)
    except SourceUnavailableError as exc:
        log.warning("%s unavailable: %s", source_id, exc)
        return _empty_operations(), SourceStatus(
            source_id=source_id, status="failed", message=str(exc)
        )
    ops = normalize_operations(gdf, cfg, kind, source.service_url or "")
    status.record_count = len(ops)
    return ops, status
