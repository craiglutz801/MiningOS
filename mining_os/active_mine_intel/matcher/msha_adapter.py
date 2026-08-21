"""MSHA adapter: mines, inspections, and quarterly employment datasets.

Retrieval order per dataset:
  A. Configured direct URL (.env override).
  B. Portal link discovery on the MSHA open-data pages.
  C. Manual fallback file in data/manual/common/.

MSHA is a core source: if the mines dataset cannot be loaded at all, the run
must stop with an actionable message. Metal/nonmetal employee hours are
activity evidence, never production quantity.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from mining_os.active_mine_intel.matcher.arcgis_client import resolve_field
from mining_os.active_mine_intel.matcher.config import PipelineConfig
from mining_os.active_mine_intel.matcher.download_client import (
    CacheManager,
    discover_msha_url,
    download_bytes,
    extract_tabular_bytes,
    find_manual_file,
)
from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.models import SourceStatus, SourceUnavailableError
from mining_os.active_mine_intel.matcher.source_registry import MSHA_PORTAL_KEYWORDS, MSHA_PORTAL_URLS, get_source
from mining_os.active_mine_intel.matcher.utilities import coerce_float, dataframe_from_delimited, utc_now, utc_now_iso

log = get_logger("mcm.msha")

_MINES_FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "msha_mine_id": (["MINE_ID", "MINEID", "msha_mine_id"], ["mine id"]),
    "mine_name": (["CURRENT_MINE_NAME", "MINE_NAME", "mine_name"], ["mine name"]),
    "operator_name": (
        ["CURRENT_OPERATOR_NAME", "OPERATOR_NAME", "operator_name"],
        ["operator"],
    ),
    "controller_name": (
        ["CURRENT_CONTROLLER_NAME", "CONTROLLER_NAME", "controller_name"],
        ["controller"],
    ),
    "mine_status": (["CURRENT_MINE_STATUS", "MINE_STATUS", "mine_status"], ["status"]),
    "mine_status_date": (
        ["CURRENT_STATUS_DT", "STATUS_DATE", "mine_status_date"],
        ["status dt", "status date"],
    ),
    "mine_type": (["CURRENT_MINE_TYPE", "MINE_TYPE", "mine_type"], ["mine type"]),
    "coal_metal_indicator": (
        ["COAL_METAL_IND", "C_M_IND", "coal_metal_indicator"],
        ["coal metal"],
    ),
    "primary_commodity": (
        ["PRIMARY_SIC", "PRIMARY_CANVASS", "COMMODITY", "primary_commodity"],
        ["sic", "commodity", "canvass"],
    ),
    "state": (["STATE", "STATE_ABBR", "state"], ["state"]),
    "county": (["FIPS_CNTY_NM", "COUNTY", "county"], ["county", "cnty"]),
    "latitude": (["LATITUDE", "LAT", "latitude"], ["latitude"]),
    "longitude": (["LONGITUDE", "LONG", "LON", "longitude"], ["longitude"]),
}

_QUARTERLY_FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "msha_mine_id": (["MINE_ID", "msha_mine_id"], ["mine id"]),
    "calendar_year": (["CAL_YR", "CALENDAR_YR", "calendar_year", "YEAR"], ["yr", "year"]),
    "calendar_quarter": (["CAL_QTR", "QTR", "calendar_quarter", "QUARTER"], ["qtr", "quarter"]),
    "average_employee_count": (
        ["AVG_EMPLOYEE_CNT", "AVG_EMP_TOTAL", "average_employee_count", "EMPLOYEE_COUNT"],
        ["employee"],
    ),
    "hours_worked": (["HOURS_WORKED", "EMP_HRS_TOTAL", "hours_worked"], ["hours", "hrs"]),
    "subunit": (["SUBUNIT", "subunit"], ["subunit"]),
    "coal_metal_indicator": (["COAL_METAL_IND", "coal_metal_indicator"], ["coal metal"]),
}

_INSPECTIONS_FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "msha_mine_id": (["MINE_ID", "msha_mine_id"], ["mine id"]),
    "inspection_event_id": (
        ["EVENT_NO", "EVENT_NUM", "inspection_event_id"],
        ["event"],
    ),
    "inspection_start_date": (
        ["INSPECTION_BEGIN_DT", "BEGIN_DT", "inspection_start_date", "START_DATE"],
        ["begin"],
    ),
    "inspection_end_date": (
        ["INSPECTION_END_DT", "END_DT", "inspection_end_date"],
        ["end"],
    ),
    "inspection_type": (
        ["INSPECTION_TYPE", "ACTIVITY_CODE", "inspection_type", "ACTIVITY"],
        ["type", "activity"],
    ),
}

_MANUAL_KEYWORDS: dict[str, list[str]] = {
    "msha_mines": ["mines"],
    "msha_inspections": ["inspections"],
    "msha_quarterly": ["quarterly", "employment"],
}


def _rename(df: pd.DataFrame, spec: dict[str, tuple[list[str], list[str]]]) -> pd.DataFrame:
    fields = [{"name": c, "alias": c} for c in df.columns]
    mapping: dict[str, str] = {}
    for target, (candidates, keywords) in spec.items():
        actual = resolve_field(fields, candidates, keywords)
        if actual:
            mapping[actual] = target
    out = df.rename(columns=mapping)
    for target in spec:
        if target not in out.columns:
            out[target] = None
    return out[list(spec.keys())].copy()


def _fetch_dataset_bytes(
    source_id: str,
    cache: CacheManager,
    manual_dir: Path,
    use_cache_only: bool,
    refresh: bool,
    status: SourceStatus,
) -> bytes:
    source = get_source(source_id)

    def fetch() -> bytes:
        url = source.resolved_override()
        if url:
            log.info("%s: using configured URL %s", source_id, url)
        else:
            url = discover_msha_url(MSHA_PORTAL_URLS, MSHA_PORTAL_KEYWORDS[source_id])
        if not url:
            raise SourceUnavailableError(
                f"No configured or discovered URL for {source_id}"
            )
        status.resolved_url = url
        # Inspections/quarterly national dumps are large; fail faster than mines.
        timeout = (10.0, 300.0)
        retries = 3
        if source_id in ("msha_inspections", "msha_quarterly"):
            timeout = (10.0, 120.0)
            retries = 1
        raw = download_bytes(url, timeout=timeout, max_retries=retries)
        return extract_tabular_bytes(raw, url)

    try:
        content, info = cache.get(
            key=source_id,
            suffix=".txt",
            fetch=fetch,
            ttl_hours=source.cache_ttl_hours,
            use_cache_only=use_cache_only,
            refresh=refresh,
        )
        status.cache_used = info["cache_used"]
        status.cache_age_hours = info["cache_age_hours"]
        status.status = "degraded" if info.get("stale") else (
            "cached" if info["cache_used"] else "success"
        )
        return content
    except SourceUnavailableError as exc:
        manual = find_manual_file(manual_dir, _MANUAL_KEYWORDS[source_id])
        if manual is not None:
            log.warning("%s: falling back to manual file %s", source_id, manual)
            status.resolved_url = str(manual)
            status.status = "degraded"
            status.message = f"Loaded from manual fallback after: {exc}"
            return extract_tabular_bytes(manual.read_bytes(), manual.name)
        raise


def load_mines(
    cfg: PipelineConfig,
    cache: CacheManager,
    manual_dir: Path,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[pd.DataFrame, SourceStatus]:
    status = SourceStatus(source_id="msha_mines", retrieved_at=utc_now_iso())
    if fixture_path is not None:
        raw = fixture_path.read_bytes()
        status.resolved_url = str(fixture_path)
        status.status = "success"
        status.message = "Loaded from local fixture"
    else:
        try:
            raw = _fetch_dataset_bytes(
                "msha_mines", cache, manual_dir, use_cache_only, refresh, status
            )
        except SourceUnavailableError as exc:
            raise SourceUnavailableError(
                "MSHA mines dataset could not be loaded from a configured URL, portal "
                "discovery, cache, or a manual file. Set MSHA_MINES_URL in .env or place "
                "a file containing 'mines' in its name under data/manual/common/. "
                f"Underlying error: {exc}"
            ) from exc
    df = _rename(dataframe_from_delimited(raw), _MINES_FIELDS)

    df["latitude"] = df["latitude"].map(coerce_float)
    df["longitude"] = df["longitude"].map(coerce_float)
    df["state"] = df["state"].astype(str).str.strip().str.upper()
    indicator = df["coal_metal_indicator"].astype(str).str.strip().str.upper()

    lon_min, lat_min, lon_max, lat_max = cfg.bounds
    mask = (
        (df["state"] == cfg.state_code)
        & df["latitude"].notna()
        & df["longitude"].notna()
        & df["latitude"].between(lat_min, lat_max)
        & df["longitude"].between(lon_min, lon_max)
    )
    # Exclude coal operations. Keep rows only when the indicator is missing or
    # explicitly metal/nonmetal.
    has_indicator = indicator.isin(["C", "M", "COAL", "METAL", "MNM"])
    mask &= ~(indicator.isin(["C", "COAL"]))
    out_of_bounds = int(
        ((df["state"] == cfg.state_code) & (~mask) & df["latitude"].notna()).sum()
    )
    if out_of_bounds:
        log.info("MSHA mines: dropped %d rows with invalid/out-of-bounds coordinates", out_of_bounds)
    df = df.loc[mask].copy()
    df["msha_mine_id"] = df["msha_mine_id"].astype(str).str.strip()
    df = df.drop_duplicates(subset=["msha_mine_id"])
    status.record_count = len(df)
    log.info("MSHA mines in %s (metal/nonmetal, valid coords): %d", cfg.state_code, len(df))
    return df.reset_index(drop=True), status


def load_quarterly(
    cfg: PipelineConfig,
    cache: CacheManager,
    manual_dir: Path,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[pd.DataFrame, SourceStatus]:
    status = SourceStatus(source_id="msha_quarterly", retrieved_at=utc_now_iso())
    if fixture_path is not None:
        raw = fixture_path.read_bytes()
        status.resolved_url = str(fixture_path)
        status.status = "success"
    else:
        raw = _fetch_dataset_bytes(
            "msha_quarterly", cache, manual_dir, use_cache_only, refresh, status
        )
    df = _rename(dataframe_from_delimited(raw), _QUARTERLY_FIELDS)
    df["msha_mine_id"] = df["msha_mine_id"].astype(str).str.strip()
    df["calendar_year"] = pd.to_numeric(df["calendar_year"], errors="coerce")
    df["calendar_quarter"] = pd.to_numeric(df["calendar_quarter"], errors="coerce")
    df["hours_worked"] = pd.to_numeric(df["hours_worked"], errors="coerce").fillna(0.0)
    df["average_employee_count"] = pd.to_numeric(
        df["average_employee_count"], errors="coerce"
    ).fillna(0.0)
    df = df.dropna(subset=["calendar_year", "calendar_quarter"])
    status.record_count = len(df)
    return df.reset_index(drop=True), status


def load_inspections(
    cfg: PipelineConfig,
    cache: CacheManager,
    manual_dir: Path,
    use_cache_only: bool = False,
    refresh: bool = True,
    fixture_path: Path | None = None,
) -> tuple[pd.DataFrame, SourceStatus]:
    status = SourceStatus(source_id="msha_inspections", retrieved_at=utc_now_iso())
    if fixture_path is not None:
        raw = fixture_path.read_bytes()
        status.resolved_url = str(fixture_path)
        status.status = "success"
    else:
        raw = _fetch_dataset_bytes(
            "msha_inspections", cache, manual_dir, use_cache_only, refresh, status
        )
    df = _rename(dataframe_from_delimited(raw), _INSPECTIONS_FIELDS)
    df["msha_mine_id"] = df["msha_mine_id"].astype(str).str.strip()
    for col in ("inspection_start_date", "inspection_end_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
    df = df.dropna(subset=["inspection_start_date"])
    # Keep recent history only — national dump is huge and stalls Pull on small hosts.
    cutoff = pd.Timestamp(utc_now().replace(tzinfo=None)) - pd.DateOffset(months=36)
    before = len(df)
    df = df[df["inspection_start_date"] >= cutoff]
    if before and len(df) < before:
        log.info(
            "msha_inspections: kept %d/%d rows from last 36 months",
            len(df),
            before,
        )
    status.record_count = len(df)
    return df.reset_index(drop=True), status


def aggregate_evidence(
    mines: pd.DataFrame,
    inspections: pd.DataFrame | None,
    quarterly: pd.DataFrame | None,
    cfg: PipelineConfig,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Aggregate per-mine MSHA activity evidence.

    Quarter windows are anchored to the latest quarter present in the quarterly
    dataset so results are deterministic for a given data vintage.
    """
    now = now or utc_now()
    out = mines.copy()
    out["latest_inspection_date"] = pd.NaT
    out["inspections_last_18_months"] = 0
    out["inspections_last_36_months"] = 0
    out["latest_reported_quarter"] = None
    out["hours_last_4_quarters"] = 0.0
    out["hours_last_8_quarters"] = 0.0
    out["employees_last_4_quarters"] = 0.0
    out["has_recent_positive_hours"] = False

    if inspections is not None and not inspections.empty:
        insp = inspections.copy()
        cutoff_18 = pd.Timestamp(now.replace(tzinfo=None)) - pd.DateOffset(
            months=cfg.recent_inspection_months
        )
        cutoff_36 = pd.Timestamp(now.replace(tzinfo=None)) - pd.DateOffset(months=36)
        grouped = insp.groupby("msha_mine_id")["inspection_start_date"]
        latest = grouped.max()
        last18 = insp[insp["inspection_start_date"] >= cutoff_18].groupby("msha_mine_id").size()
        last36 = insp[insp["inspection_start_date"] >= cutoff_36].groupby("msha_mine_id").size()
        out["latest_inspection_date"] = out["msha_mine_id"].map(latest)
        out["inspections_last_18_months"] = (
            out["msha_mine_id"].map(last18).fillna(0).astype(int)
        )
        out["inspections_last_36_months"] = (
            out["msha_mine_id"].map(last36).fillna(0).astype(int)
        )

    if quarterly is not None and not quarterly.empty:
        q = quarterly.copy()
        q["quarter_index"] = q["calendar_year"] * 4 + (q["calendar_quarter"] - 1)
        anchor = int(q["quarter_index"].max())
        q4 = q[q["quarter_index"] > anchor - 4]
        q8 = q[q["quarter_index"] > anchor - 8]
        q5to8 = q[(q["quarter_index"] <= anchor - 4) & (q["quarter_index"] > anchor - 8)]

        hours4 = q4.groupby("msha_mine_id")["hours_worked"].sum()
        hours8 = q8.groupby("msha_mine_id")["hours_worked"].sum()
        emp4 = q4.groupby("msha_mine_id")["average_employee_count"].mean()
        latest_q = q[q["hours_worked"] > 0].groupby("msha_mine_id")["quarter_index"].max()

        out["hours_last_4_quarters"] = out["msha_mine_id"].map(hours4).fillna(0.0)
        out["hours_last_8_quarters"] = out["msha_mine_id"].map(hours8).fillna(0.0)
        out["employees_last_4_quarters"] = out["msha_mine_id"].map(emp4).fillna(0.0)
        out["has_recent_positive_hours"] = out["hours_last_4_quarters"] > 0
        out["latest_reported_quarter"] = out["msha_mine_id"].map(
            latest_q.map(lambda qi: f"{int(qi) // 4} Q{int(qi) % 4 + 1}")
        )
    return out
