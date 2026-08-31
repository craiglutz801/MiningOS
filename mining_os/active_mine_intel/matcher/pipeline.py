"""Pipeline orchestrator: fetch -> normalize -> match -> score -> write outputs.

Also provides the shared command-line runner used by run_nevada.py / run_utah.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import geopandas as gpd
import pandas as pd

from mining_os.active_mine_intel.matcher import blm_adapter
from mining_os.active_mine_intel.matcher import msha_adapter
from mining_os.active_mine_intel.matcher import nevada_adapter
from mining_os.active_mine_intel.matcher import utah_adapter
from mining_os.active_mine_intel.matcher.arcgis_client import ArcGISClient
from mining_os.active_mine_intel.matcher.config import PROJECT_ROOT, Paths, PipelineConfig, get_config
from mining_os.active_mine_intel.matcher.download_client import CacheManager
from mining_os.active_mine_intel.matcher.logging_setup import get_logger, setup_logging
from mining_os.active_mine_intel.matcher.models import (
    DataValidationError,
    FatalPipelineError,
    PipelineResult,
    SourceStatus,
    SourceUnavailableError,
)
from mining_os.active_mine_intel.matcher.output_writer import write_outputs
from mining_os.active_mine_intel.matcher.scoring import MATCH_BASE_POINTS, score_candidate
from mining_os.active_mine_intel.matcher.spatial_matcher import (
    assign_claim_blocks,
    associate_operations,
    build_mine_sites,
    generate_candidates,
)
from mining_os.active_mine_intel.matcher.utilities import compact_json, new_run_id, utc_now, utc_now_iso

log = get_logger("mcm.pipeline")

ProgressCallback = Callable[[int, int, str], None]
PIPELINE_STAGE_TOTAL = 16


def _emit(progress_cb: ProgressCallback | None, stage: int, message: str) -> None:
    log.info("[%d/%d] %s", stage, PIPELINE_STAGE_TOTAL, message)
    if progress_cb:
        try:
            progress_cb(stage, PIPELINE_STAGE_TOTAL, message)
        except Exception:  # noqa: BLE001 — never fail the pipeline on UI progress
            pass


NEVADA_DEGRADED_WARNING = (
    "Nevada state production data was unavailable. Scores are activity-based and "
    "cannot confirm reported production."
)
UTAH_DEGRADED_WARNING = (
    "Utah DOGM permit data was unavailable. Scores are based on MSHA and BLM "
    "activity only and cannot confirm permit status."
)


def _fixture(fixture_dir: Path | None, name: str) -> Path | None:
    if fixture_dir is None:
        return None
    return fixture_dir / name


def run_pipeline(
    state_code: str,
    refresh: bool = True,
    use_cache: bool = False,
    fixture_dir: Path | None = None,
    paths: Paths | None = None,
    progress_cb: ProgressCallback | None = None,
    plss_use_network: bool = True,
) -> PipelineResult:
    cfg = get_config(state_code, paths=paths)
    result = PipelineResult(
        state_code=cfg.state_code, run_id=new_run_id(), started_at=utc_now_iso()
    )

    # Stage 1: folders and manifest skeleton.
    _emit(progress_cb, 1, f"Initialize folders and run manifest ({cfg.state_name})")
    cfg.paths.ensure(cfg.state_name)
    output_dir = cfg.paths.output_dir(cfg.state_name)
    result.output_dir = str(output_dir)

    client = ArcGISClient()
    common_cache = CacheManager(cfg.paths.raw_dir("common"), cfg.cache_ttl_hours)
    state_cache = CacheManager(
        cfg.paths.raw_dir(cfg.state_name.lower()), cfg.cache_ttl_hours
    )
    manual_common = cfg.paths.manual_dir("common")
    qc: dict = {"degraded_sources": []}

    try:
        # Stage 2: BLM active claims (core fatal).
        _emit(progress_cb, 2, "Fetch BLM active claims")
        try:
            claims, status, claim_stats = blm_adapter.load_claims(
                cfg,
                common_cache,
                client,
                use_cache_only=use_cache,
                refresh=refresh,
                fixture_path=_fixture(fixture_dir, "sample_claims.geojson"),
            )
            result.sources["blm_claims"] = status
            patented_claims = claim_stats.pop("_patented_claims", None)
            conveyed_claims = claim_stats.pop("_conveyed_claims", None)
            qc.update(claim_stats)
            qc["_patented_claims"] = patented_claims
            qc["_conveyed_claims"] = conveyed_claims
        except (SourceUnavailableError, DataValidationError) as exc:
            raise FatalPipelineError(
                "BLM active mining claims could not be loaded (core source). "
                f"Details: {exc}"
            ) from exc

        # Stages 3-4: plans and notices (supporting, nonfatal).
        _emit(progress_cb, 3, "Fetch BLM plans of operations")
        plans, plans_status = blm_adapter.load_operations(
            "plan",
            cfg,
            common_cache,
            client,
            use_cache_only=use_cache,
            refresh=refresh,
            fixture_path=_fixture(fixture_dir, "sample_plans.geojson"),
        )
        result.sources["blm_plans"] = plans_status
        _emit(progress_cb, 4, "Fetch BLM notices")
        notices, notices_status = blm_adapter.load_operations(
            "notice",
            cfg,
            common_cache,
            client,
            use_cache_only=use_cache,
            refresh=refresh,
            fixture_path=_fixture(fixture_dir, "sample_notices.geojson"),
        )
        result.sources["blm_notices"] = notices_status
        for st in (plans_status, notices_status):
            if st.status in ("failed", "degraded"):
                result.add_warning(f"{st.source_id}: {st.message or st.status}")
                qc["degraded_sources"].append(st.source_id)
        operations = pd.concat([plans, notices], ignore_index=True)
        operations = gpd.GeoDataFrame(operations, geometry="geometry", crs="EPSG:4326")

        # Stage 5: MSHA mines (core fatal).
        _emit(progress_cb, 5, "Fetch and normalize MSHA mines")
        try:
            msha_mines, msha_status = msha_adapter.load_mines(
                cfg,
                common_cache,
                manual_common,
                use_cache_only=use_cache,
                refresh=refresh,
                fixture_path=_fixture(fixture_dir, "sample_msha_mines.csv"),
            )
            result.sources["msha_mines"] = msha_status
        except SourceUnavailableError as exc:
            raise FatalPipelineError(str(exc)) from exc
        if msha_mines.empty:
            raise FatalPipelineError(
                f"Zero MSHA mines loaded for {cfg.state_code}; likely schema/filter error."
            )
        qc["msha_mines_in_state"] = len(msha_mines)

        # Stage 6: MSHA inspections and quarterly (core nonfatal).
        # These national files can be huge; bound wall time so Pull cannot stall forever.
        _emit(progress_cb, 6, "Fetch MSHA inspections and employment")
        inspections = quarterly = None
        stage6_timeout = int(os.getenv("MINING_OS_AMI_MSHA_SECONDARY_TIMEOUT_SEC") or "180")

        def _load_insp():
            return msha_adapter.load_inspections(
                cfg,
                common_cache,
                manual_common,
                use_cache_only=use_cache,
                refresh=refresh,
                fixture_path=_fixture(fixture_dir, "sample_msha_inspections.csv"),
            )

        def _load_qtr():
            return msha_adapter.load_quarterly(
                cfg,
                common_cache,
                manual_common,
                use_cache_only=use_cache,
                refresh=refresh,
                fixture_path=_fixture(fixture_dir, "sample_msha_quarterly.csv"),
            )

        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_load_insp)
                inspections, insp_status = fut.result(timeout=max(60, stage6_timeout))
            result.sources["msha_inspections"] = insp_status
        except FuturesTimeout:
            result.sources["msha_inspections"] = SourceStatus(
                source_id="msha_inspections",
                status="failed",
                message=f"Timed out after {stage6_timeout}s",
            )
            result.add_warning(
                f"MSHA inspections timed out after {stage6_timeout}s; continuing without them."
            )
            qc["degraded_sources"].append("msha_inspections")
        except SourceUnavailableError as exc:
            result.sources["msha_inspections"] = SourceStatus(
                source_id="msha_inspections", status="failed", message=str(exc)
            )
            result.add_warning(f"MSHA inspections unavailable: {exc}")
            qc["degraded_sources"].append("msha_inspections")
        except Exception as exc:  # noqa: BLE001
            result.sources["msha_inspections"] = SourceStatus(
                source_id="msha_inspections", status="failed", message=str(exc)
            )
            result.add_warning(f"MSHA inspections failed: {exc}")
            qc["degraded_sources"].append("msha_inspections")

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_load_qtr)
                quarterly, q_status = fut.result(timeout=max(60, stage6_timeout))
            result.sources["msha_quarterly"] = q_status
        except FuturesTimeout:
            result.sources["msha_quarterly"] = SourceStatus(
                source_id="msha_quarterly",
                status="failed",
                message=f"Timed out after {stage6_timeout}s",
            )
            result.add_warning(
                f"MSHA quarterly employment timed out after {stage6_timeout}s; continuing without it."
            )
            qc["degraded_sources"].append("msha_quarterly")
        except SourceUnavailableError as exc:
            result.sources["msha_quarterly"] = SourceStatus(
                source_id="msha_quarterly", status="failed", message=str(exc)
            )
            result.add_warning(f"MSHA quarterly employment unavailable: {exc}")
            qc["degraded_sources"].append("msha_quarterly")
        except Exception as exc:  # noqa: BLE001
            result.sources["msha_quarterly"] = SourceStatus(
                source_id="msha_quarterly", status="failed", message=str(exc)
            )
            result.add_warning(f"MSHA quarterly employment failed: {exc}")
            qc["degraded_sources"].append("msha_quarterly")
        msha_mines = msha_adapter.aggregate_evidence(msha_mines, inspections, quarterly, cfg)

        # Stage 7: state-specific mine/production data.
        _emit(progress_cb, 7, "Fetch state mine / production data")
        state_adapter = nevada_adapter if cfg.state_code == "NV" else utah_adapter
        state_source_id = "nevada_production" if cfg.state_code == "NV" else "utah_dogm"
        state_mines = None
        state_status = SourceStatus(source_id=state_source_id, status="pending")
        try:
            state_mines, state_status = state_adapter.load_state_mines(
                cfg,
                state_cache,
                client,
                use_cache_only=use_cache,
                refresh=refresh,
                fixture_path=_fixture(fixture_dir, "sample_mines.csv"),
            )
            result.sources[state_source_id] = state_status
            if state_status.status in {"failed", "stale"}:
                result.degraded_mode = True
                result.add_warning(
                    NEVADA_DEGRADED_WARNING if cfg.state_code == "NV" else UTAH_DEGRADED_WARNING
                )
        except Exception as exc:  # noqa: BLE001 - state primary is nonfatal
            state_status = SourceStatus(
                source_id=state_source_id,
                status="failed",
                outcome="failed",
                usable_for_assertions=False,
                failure_class="unavailable",
                message=str(exc),
            )
            result.sources[state_source_id] = state_status
            result.degraded_mode = True
            warning = (
                NEVADA_DEGRADED_WARNING if cfg.state_code == "NV" else UTAH_DEGRADED_WARNING
            )
            result.add_warning(warning)
            qc["degraded_sources"].append(state_source_id)
            log.warning("State source failed; continuing in degraded mode: %s", exc)
        qc["state_mines_loaded"] = 0 if state_mines is None else len(state_mines)
        coverage = (state_status.extra or {}).get("coverage") if state_status else None
        if coverage:
            qc["utah_dogm_coverage"] = coverage

        bmrr_regulation = bmrr_reclamation = None
        if cfg.state_code == "NV":
            try:
                from mining_os.active_mine_intel.matcher import ndep_bmrr_adapter

                (
                    bmrr_regulation,
                    bmrr_reg_status,
                    bmrr_reclamation,
                    bmrr_rec_status,
                ) = ndep_bmrr_adapter.load_bmrr_evidence(
                    cfg,
                    state_cache,
                    client,
                    use_cache_only=use_cache,
                    refresh=refresh,
                    fixture_path=_fixture(fixture_dir, "sample_bmrr.csv"),
                )
                result.sources["ndep_bmrr_regulation"] = bmrr_reg_status
                result.sources["ndep_bmrr_reclamation"] = bmrr_rec_status
                for st in (bmrr_reg_status, bmrr_rec_status):
                    if st.status in ("failed", "stale"):
                        result.add_warning(f"{st.source_id}: {st.message or st.status}")
                        qc["degraded_sources"].append(st.source_id)
            except Exception as exc:  # noqa: BLE001 — BMRR is supporting evidence
                result.sources["ndep_bmrr_regulation"] = SourceStatus(
                    source_id="ndep_bmrr_regulation",
                    status="failed",
                    outcome="failed",
                    usable_for_assertions=False,
                    failure_class="unavailable",
                    message=str(exc),
                )
                result.add_warning(f"NDEP BMRR unavailable: {exc}")
                qc["degraded_sources"].append("ndep_bmrr_regulation")

        # Stage 8: canonical mine sites.
        _emit(progress_cb, 8, "Build canonical mine sites")
        mine_sites = build_mine_sites(state_mines, msha_mines, cfg)
        mine_sites = _attach_bmrr_evidence(mine_sites, bmrr_regulation, bmrr_reclamation)
        qc["canonical_mines"] = len(mine_sites)

        # Stage 9: attach BLM operation evidence.
        _emit(progress_cb, 9, "Attach BLM operation evidence")
        associations = associate_operations(mine_sites, operations, cfg)
        plan_sites = set(
            associations.loc[associations["operation_kind"] == "plan", "mine_site_id"]
        )
        notice_sites = set(
            associations.loc[associations["operation_kind"] == "notice", "mine_site_id"]
        )
        mine_sites["blm_plan_present"] = mine_sites["mine_site_id"].isin(plan_sites)
        mine_sites["blm_notice_present"] = mine_sites["mine_site_id"].isin(notice_sites)

        # Stage 10: spatial candidates (claim blocks first, for context).
        _emit(progress_cb, 10, "Generate spatial mine–claim candidates")
        claims = assign_claim_blocks(claims, cfg)
        pairs = generate_candidates(mine_sites, claims, operations, associations, cfg)
        qc["candidate_pairs"] = len(pairs)

        # Stages 11-12: similarity is computed inside candidate generation;
        # score claim-level matches.
        _emit(progress_cb, 11, "Calculate name / operator similarities")
        _emit(progress_cb, 12, "Score claim-level matches")
        current_year = utc_now().year
        latest_available_year = None
        if cfg.state_code == "NV":
            years = pd.to_numeric(
                mine_sites["latest_state_production_year"], errors="coerce"
            ).dropna()
            latest_available_year = int(years.max()) if not years.empty else None

        sites_by_id = {
            row["mine_site_id"]: row for _, row in mine_sites.iterrows()
        }
        scored_rows: list[dict] = []
        for _, pair in pairs.iterrows():
            site = sites_by_id[pair["mine_site_id"]]
            site_dict = site.to_dict()
            pair_dict = pair.to_dict()
            scores = score_candidate(
                pair_dict,
                site_dict,
                cfg,
                latest_available_year,
                current_year,
                degraded_mode=result.degraded_mode,
            )
            row = {**pair_dict, **scores}
            row["mine_latitude"] = site_dict.get("latitude")
            row["mine_longitude"] = site_dict.get("longitude")
            row["evidence_summary_json"] = compact_json(
                _evidence_timeline(site_dict, pair_dict, cfg)
            )
            scored_rows.append(row)
        matches = pd.DataFrame(scored_rows)
        if not matches.empty:
            matches = matches.sort_values(
                ["total_score", "activity_score", "claim_match_score"],
                ascending=False,
            ).reset_index(drop=True)

        # Stage 13: aggregate mine-site summaries.
        _emit(progress_cb, 13, "Aggregate mine-site summaries")
        site_summary = _build_site_summary(matches, mine_sites, cfg)
        _emit(progress_cb, 14, "Attach PLSS (township / range / section)")
        from mining_os.active_mine_intel.matcher.plss_lookup import attach_plss_to_sites

        site_summary = attach_plss_to_sites(
            site_summary,
            matches,
            claims,
            paths=cfg.paths,
            # Mining OS passes plss_use_network=False so Pull finishes via CSE_META
            # instead of per-mine CadNSDI calls (which can hang for hours).
            use_network=bool(plss_use_network) and not bool(fixture_dir),
        )
        qc["candidate_sites"] = len(site_summary)
        qc["high_candidates"] = int((site_summary["confidence_category"] == "HIGH").sum()) if not site_summary.empty else 0
        qc["strong_candidates"] = int((site_summary["confidence_category"] == "STRONG").sum()) if not site_summary.empty else 0
        qc["unknown_claim_quality_count"] = int(
            (claims["geometry_quality_group"] == "UNKNOWN").sum()
        )
        qc["missing_operator_count"] = int(
            mine_sites["operator_name"].isna().sum()
        )
        site_summary = _attach_evidence_model(
            site_summary,
            mine_sites,
            matches,
            claims,
            qc,
            result.sources,
            cfg,
        )
        if not site_summary.empty and "operational_status" in site_summary.columns:
            qc["operational_status_counts"] = {
                str(k): int(v)
                for k, v in site_summary["operational_status"].value_counts().to_dict().items()
            }
            qc["fail_closed_sites"] = (
                int(site_summary["fail_closed"].fillna(False).sum())
                if "fail_closed" in site_summary.columns
                else 0
            )
            qc["mixed_tenure_sites"] = (
                int((site_summary["tenure_class"] == "Mixed").sum())
                if "tenure_class" in site_summary.columns
                else 0
            )

        # Stages 14-15: outputs, QC report, manifest.
        _emit(progress_cb, 15, "Write outputs and QC report")
        result.status = "partial" if (result.degraded_mode or result.warnings) else "success"
        result.completed_at = utc_now_iso()
        # GeoDataFrames in qc are in-memory tenure overlays — never serialize them.
        qc.pop("_patented_claims", None)
        qc.pop("_conveyed_claims", None)
        result.counts = {
            k: v for k, v in qc.items() if isinstance(v, int)
        }
        _emit(progress_cb, 16, "Write run manifest")
        result.site_summary = site_summary
        result.matches = matches
        result.qc = qc
        result.output_files = write_outputs(
            output_dir, site_summary, matches, mine_sites, claims, operations, qc, result
        )
        log.info(
            "Pipeline %s complete: %s (%d candidate sites, %d pairs)",
            cfg.state_code,
            result.status,
            qc.get("candidate_sites", 0),
            qc.get("candidate_pairs", 0),
        )
        return result
    except FatalPipelineError:
        result.status = "failed"
        result.completed_at = utc_now_iso()
        try:
            from mining_os.active_mine_intel.matcher.utilities import write_json

            write_json(output_dir / "run_manifest.json", result.manifest("0.1.0"))
        except Exception:  # noqa: BLE001
            pass
        raise


def _evidence_timeline(site: dict, pair: dict, cfg: PipelineConfig) -> list[dict]:
    """Compact evidence timeline, newest first; only evidence actually present."""
    events: list[dict] = []
    years = site.get("state_production_years") or []
    if isinstance(years, str):
        from mining_os.active_mine_intel.matcher.utilities import parse_years

        years = parse_years(years)
    for year in sorted(set(years), reverse=True)[:5]:
        events.append(
            {"when": str(year), "event": f"{cfg.state_name} reported mineral production"}
        )
    quarter = site.get("latest_reported_quarter")
    if quarter and float(site.get("hours_last_8_quarters") or 0) > 0:
        events.append({"when": str(quarter), "event": "MSHA positive hours reported"})
    inspection = site.get("latest_inspection_date")
    if inspection is not None and str(inspection) not in ("NaT", "None", "nan", ""):
        events.append({"when": str(inspection)[:10], "event": "MSHA inspection"})
    if site.get("state_permit_active"):
        events.append({"when": "Current", "event": "State permit active (not proof of production)"})
    if site.get("blm_plan_present"):
        events.append({"when": "Current", "event": "BLM Plan of Operations associated"})
    if site.get("blm_notice_present"):
        events.append({"when": "Current", "event": "BLM Notice associated"})
    claim_type = pair.get("claim_type")
    if claim_type:
        events.append(
            {"when": "Current", "event": f"Active unpatented {str(claim_type).lower()}"}
        )
    return events


def _build_site_summary(
    matches: pd.DataFrame, mine_sites: gpd.GeoDataFrame, cfg: PipelineConfig
) -> gpd.GeoDataFrame:
    from shapely.geometry import Point

    if matches.empty:
        return gpd.GeoDataFrame(
            {c: [] for c in ["rank", "mine_site_id", "total_score", "confidence_category"]},
            geometry=[],
            crs="EPSG:4326",
        )
    match_rank = {mt: i for i, mt in enumerate(MATCH_BASE_POINTS)}
    rows: list[dict] = []
    sites_by_id = {row["mine_site_id"]: row for _, row in mine_sites.iterrows()}
    for site_id, group in matches.groupby("mine_site_id"):
        group = group.sort_values(
            by=["total_score", "claim_match_score"], ascending=False
        )
        best = group.sort_values(
            by="match_type", key=lambda s: s.map(match_rank)
        ).iloc[0]
        top = group.iloc[0]
        site = sites_by_id[site_id]
        latest_year = site.get("latest_state_production_year")
        rows.append(
            {
                "mine_site_id": site_id,
                "mine_name": site.get("canonical_mine_name"),
                "operator_name": site.get("operator_name"),
                "commodity": site.get("commodity"),
                "county": site.get("county"),
                "latitude": site.get("latitude"),
                "longitude": site.get("longitude"),
                "total_score": float(top["total_score"]),
                "activity_score": float(top["activity_score"]),
                "claim_match_score": float(top["claim_match_score"]),
                "data_quality_score": float(top["data_quality_score"]),
                "penalty_score": float(top["penalty_score"]),
                "confidence_category": top["confidence_category"],
                "mine_activity_label": top["mine_activity_label"],
                "latest_production_activity_year": latest_year,
                "claim_count": int(group["claim_id"].nunique()),
                "best_match_type": best["match_type"],
                "best_claim_serial_number": best["claim_serial_number"],
                "best_claim_name": best["claim_name"],
                "best_distance_meters": float(best["distance_meters"]),
                "blm_plan_present": bool(site.get("blm_plan_present")),
                "blm_notice_present": bool(site.get("blm_notice_present")),
                "msha_status": site.get("msha_status"),
                "verification_status": "Not Reviewed",
                "all_claim_serial_numbers": ";".join(
                    sorted(str(s) for s in group["claim_serial_number"].dropna().unique())
                ),
                "all_claim_ids": ";".join(sorted(group["claim_id"].astype(str).unique())),
                "score_breakdown_json": top["score_breakdown_json"],
                "evidence_summary_json": top["evidence_summary_json"],
                "recommended_next_action": top["recommended_next_action"],
            }
        )
    summary = pd.DataFrame(rows).sort_values(
        ["total_score", "activity_score", "claim_match_score"], ascending=False
    ).reset_index(drop=True)
    summary.insert(0, "rank", range(1, len(summary) + 1))
    return gpd.GeoDataFrame(
        summary,
        geometry=[Point(xy) for xy in zip(summary["longitude"], summary["latitude"])],
        crs="EPSG:4326",
    )


def _attach_bmrr_evidence(
    mine_sites: gpd.GeoDataFrame,
    regulation: gpd.GeoDataFrame | None,
    reclamation: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame:
    """Overlay NDEP BMRR records via deterministic reconciliation. Never sets Producing."""
    from mining_os.active_mine_intel.evidence.reconciliation import best_match

    if mine_sites.empty:
        return mine_sites
    out = mine_sites.copy()
    for col in (
        "bmrr_project_id",
        "bmrr_permit_number",
        "bmrr_physical_status",
        "bmrr_permit_status",
        "bmrr_site_type",
        "bmrr_closure",
        "bmrr_match_method",
        "bmrr_reclamation",
    ):
        if col not in out.columns:
            out[col] = None

    def _rows(frame: gpd.GeoDataFrame | None) -> list[dict]:
        if frame is None or getattr(frame, "empty", True):
            return []
        df = frame.drop(columns=["geometry"], errors="ignore")
        return df.to_dict("records")

    reg_rows = _rows(regulation)
    rec_rows = _rows(reclamation)
    rec_ids = {str(r.get("bmrr_project_id") or "") for r in rec_rows}

    for idx, site in out.iterrows():
        target = {
            "mine_name": site.get("canonical_mine_name"),
            "name": site.get("canonical_mine_name"),
            "operator_name": site.get("operator_name"),
            "latitude": site.get("latitude"),
            "longitude": site.get("longitude"),
            "msha_mine_id": site.get("msha_mine_id"),
            "msha_number": site.get("msha_mine_id"),
            "state_mine_id": site.get("state_source_id"),
            "permit_number": site.get("state_permit_number"),
        }
        match, decision = best_match(target, reg_rows)
        if match is None:
            match, decision = best_match(target, rec_rows)
        if match is None:
            continue
        out.at[idx, "bmrr_project_id"] = match.get("bmrr_project_id")
        out.at[idx, "bmrr_permit_number"] = match.get("permit_number")
        out.at[idx, "bmrr_physical_status"] = match.get("bmrr_physical_status")
        out.at[idx, "bmrr_permit_status"] = match.get("bmrr_permit_status")
        out.at[idx, "bmrr_site_type"] = match.get("bmrr_site_type")
        out.at[idx, "bmrr_closure"] = match.get("bmrr_closure")
        out.at[idx, "bmrr_match_method"] = decision.get("match_method")
        pid = str(match.get("bmrr_project_id") or "")
        out.at[idx, "bmrr_reclamation"] = bool(
            match.get("bmrr_layer_kind") == "reclamation" or pid in rec_ids
        )
    return out


def _tenure_overlay_for_site(
    site: dict,
    claims: gpd.GeoDataFrame,
    patented: gpd.GeoDataFrame | None,
    conveyed: gpd.GeoDataFrame | None,
    match_rows: list[dict],
    cfg: PipelineConfig,
) -> dict:
    from shapely.geometry import Point

    from mining_os.active_mine_intel.matcher.spatial_matcher import to_projected

    unpatented = bool(match_rows)
    qualities = [str(r.get("geometry_quality_group") or "") for r in match_rows]
    lat, lon = site.get("latitude"), site.get("longitude")
    patented_hit = conveyed_hit = False
    if lat is not None and lon is not None:
        point = gpd.GeoDataFrame(
            {"id": [0]}, geometry=[Point(float(lon), float(lat))], crs="EPSG:4326"
        )
        point_proj = to_projected(point, cfg.projected_crs)
        for flag_name, frame in (("patented", patented), ("conveyed", conveyed)):
            if frame is None or getattr(frame, "empty", True):
                continue
            try:
                other = to_projected(frame, cfg.projected_crs)
                hits = other.sindex.query(point_proj.geometry.iloc[0], predicate="intersects")
            except Exception:  # noqa: BLE001
                hits = []
            if len(hits):
                if flag_name == "patented":
                    patented_hit = True
                else:
                    conveyed_hit = True
    from mining_os.active_mine_intel.evidence.tenure import classify_tenure

    return classify_tenure(
        unpatented_intersects=unpatented,
        patented_intersects=patented_hit,
        conveyed_intersects=conveyed_hit,
        claim_count=len(match_rows),
        geometry_quality_groups=qualities,
    )


def _attach_evidence_model(
    site_summary: gpd.GeoDataFrame,
    mine_sites: gpd.GeoDataFrame,
    matches: pd.DataFrame,
    claims: gpd.GeoDataFrame,
    qc: dict,
    sources: dict,
    cfg,
) -> gpd.GeoDataFrame:
    from mining_os.active_mine_intel.evidence.classify import classify_site_evidence
    from mining_os.active_mine_intel.matcher.utilities import utc_now

    if site_summary is None or site_summary.empty:
        return site_summary
    current_year = utc_now().year
    sites_by_id = {row["mine_site_id"]: row for _, row in mine_sites.iterrows()}
    matches_by_site: dict[str, list[dict]] = {}
    if matches is not None and not matches.empty:
        for _, row in matches.iterrows():
            matches_by_site.setdefault(str(row["mine_site_id"]), []).append(row.to_dict())
    patented = qc.get("_patented_claims")
    conveyed = qc.get("_conveyed_claims")
    out = site_summary.copy()
    for col in (
        "operational_status",
        "regulatory_status",
        "facility_type",
        "tenure_class",
        "verification_state",
        "fail_closed",
        "tenure_json",
        "contradictions_json",
        "assertions_json",
    ):
        if col not in out.columns:
            out[col] = None
    for idx, row in out.iterrows():
        site_id = str(row.get("mine_site_id"))
        site = sites_by_id.get(site_id)
        site_dict = site.to_dict() if site is not None else {}
        site_dict.setdefault("canonical_mine_name", row.get("mine_name"))
        site_dict.setdefault("latest_state_production_year", row.get("latest_production_activity_year"))
        site_dict.setdefault("msha_status", row.get("msha_status"))
        site_dict.setdefault("blm_plan_present", row.get("blm_plan_present"))
        site_dict.setdefault("blm_notice_present", row.get("blm_notice_present"))
        claim_rows = matches_by_site.get(site_id) or []
        tenure_overlay = _tenure_overlay_for_site(
            {**site_dict, "latitude": row.get("latitude"), "longitude": row.get("longitude")},
            claims,
            patented,
            conveyed,
            claim_rows,
            cfg,
        )
        evidence = classify_site_evidence(
            site_dict,
            current_year=current_year,
            source_status=sources,
            claim_rows=claim_rows,
            tenure_overlay=tenure_overlay,
        )
        out.at[idx, "operational_status"] = evidence["operational_status"]
        out.at[idx, "regulatory_status"] = evidence["regulatory_status"]
        out.at[idx, "facility_type"] = evidence["facility_type"]
        out.at[idx, "tenure_class"] = evidence["tenure_class"]
        out.at[idx, "verification_state"] = evidence["verification_state"]
        out.at[idx, "fail_closed"] = bool(evidence["fail_closed"])
        out.at[idx, "tenure_json"] = evidence["tenure_json"]
        out.at[idx, "contradictions_json"] = evidence["contradictions_json"]
        out.at[idx, "assertions_json"] = evidence["assertions_json"]
    return out


# --------------------------------------------------------------------------
# Command-line runner shared by run_nevada.py / run_utah.py
# --------------------------------------------------------------------------


def runner_main(state_code: str) -> int:
    parser = argparse.ArgumentParser(
        description=f"Mine Claim Matcher pipeline for {state_code}"
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Run the data pipeline but do not launch the Streamlit app",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Do not contact remote sources when a cached copy exists",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Launch Streamlit without opening a browser window",
    )
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="Run entirely from bundled test fixtures (offline demo mode)",
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    logger = setup_logging(PROJECT_ROOT / "logs")
    fixture_dir = PROJECT_ROOT / "tests" / "fixtures" if args.fixtures else None
    try:
        result = run_pipeline(
            state_code,
            refresh=not args.use_cache,
            use_cache=args.use_cache,
            fixture_dir=fixture_dir,
        )
    except FatalPipelineError as exc:
        logger.error("FATAL: %s", exc)
        return 2

    print()
    print(f"Run {result.run_id} finished with status: {result.status}")
    if result.degraded_mode:
        print("DEGRADED MODE: one or more primary sources were unavailable.")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")
    print(f"Candidate sites: {result.counts.get('candidate_sites', 0)}")
    print(f"Candidate pairs: {result.counts.get('candidate_pairs', 0)}")
    print(f"Outputs written to: {result.output_dir}")
    for path in result.output_files:
        print(f"  {path}")

    if args.refresh_only:
        return 0

    print("\nLaunching Streamlit app ...")
    env = os.environ.copy()
    env["MCM_STATE"] = state_code
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(PROJECT_ROOT / "app.py"),
        # Skip Streamlit's interactive first-run email prompt, which blocks
        # (and can kill) the launch when stdin is not a TTY.
        "--browser.gatherUsageStats",
        "false",
    ]
    if args.no_browser:
        command += ["--server.headless", "true"]
    try:
        subprocess.run(command, env=env, cwd=PROJECT_ROOT, check=False)
    except KeyboardInterrupt:
        pass
    return 0
