"""Spatial matching engine: entity resolution, operation association, candidate
pair generation, and claim blocks.

Every distance/area operation happens in the configured projected CRS
(EPSG:5070 by default). Inputs already in that CRS are used as-is, which lets
tests construct meter-based fixtures directly.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from mining_os.active_mine_intel.matcher.config import PipelineConfig
from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.normalize import normalize_entity_name, normalize_mine_name, similarity_score
from mining_os.active_mine_intel.matcher.utilities import compact_json, first_non_null

log = get_logger("mcm.spatial")

MATCH_TYPE_ORDER = [
    "PLAN_AND_MINE_INTERSECT",
    "PLAN_INTERSECTS_CLAIM",
    "MINE_POINT_INTERSECTS_CLAIM",
    "NOTICE_INTERSECTS_CLAIM",
    "NEAR_CLAIM_0_250M",
    "REVIEW_DISTANCE_250_1000M",
]


def to_projected(gdf: gpd.GeoDataFrame, crs: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS; cannot reproject safely")
    if str(gdf.crs).upper() == crs.upper():
        return gdf
    return gdf.to_crs(crs)


# --------------------------------------------------------------------------
# Mine-site entity resolution (state source <-> MSHA)
# --------------------------------------------------------------------------


def build_mine_sites(
    state_mines: gpd.GeoDataFrame | None,
    msha_mines: pd.DataFrame,
    cfg: PipelineConfig,
) -> gpd.GeoDataFrame:
    """Merge state-source mines with MSHA mines into canonical mine sites.

    Merging requires distance <= mine_state_merge_distance_m AND
    (name similarity >= 75 OR operator similarity >= 85). When uncertain the
    records stay separate rather than over-merging.
    """
    msha = msha_mines.copy().reset_index(drop=True)
    msha_gdf = gpd.GeoDataFrame(
        msha,
        geometry=[Point(xy) for xy in zip(msha["longitude"], msha["latitude"])],
        crs="EPSG:4326",
    )
    msha_proj = to_projected(msha_gdf, cfg.projected_crs)

    sites: list[dict] = []
    used_msha: set[int] = set()

    if state_mines is not None and not state_mines.empty:
        state_proj = to_projected(state_mines, cfg.projected_crs)
        sindex = msha_proj.sindex if not msha_proj.empty else None
        msha_id_to_pos = {
            str(mid).strip(): pos for pos, mid in enumerate(msha["msha_mine_id"])
        }
        for pos, (_, srow) in enumerate(state_proj.iterrows()):
            best = None
            # Shared MSHA number is authoritative; skip fuzzy matching entirely.
            state_msha_number = str(srow.get("msha_number") or "").strip()
            if state_msha_number and state_msha_number in msha_id_to_pos:
                mpos = msha_id_to_pos[state_msha_number]
                mrow = msha_proj.iloc[mpos]
                dist = float(srow.geometry.distance(mrow.geometry))
                best = ((100.0, -dist), mpos, dist, 100.0, 100.0)
            elif sindex is not None:
                nearby = sindex.query(
                    srow.geometry, predicate="dwithin", distance=cfg.mine_state_merge_distance_m
                )
                for mpos in nearby:
                    mrow = msha_proj.iloc[mpos]
                    dist = float(srow.geometry.distance(mrow.geometry))
                    name_sim = similarity_score(srow.get("mine_name"), mrow.get("mine_name"))
                    op_sim = similarity_score(
                        srow.get("operator_name"), mrow.get("operator_name")
                    )
                    if (
                        name_sim >= cfg.merge_name_similarity_min
                        or op_sim >= cfg.merge_operator_similarity_min
                    ):
                        rank = (max(name_sim, op_sim), -dist)
                        if best is None or rank > best[0]:
                            best = (rank, mpos, dist, name_sim, op_sim)
            site = _site_from_state_row(state_mines.iloc[pos], cfg)
            if best is not None:
                _, mpos, dist, name_sim, op_sim = best
                used_msha.add(int(mpos))
                _attach_msha(site, msha.iloc[int(mpos)], dist, name_sim, op_sim)
            sites.append(site)

    for mpos in range(len(msha)):
        if mpos in used_msha:
            continue
        sites.append(_site_from_msha_row(msha.iloc[mpos], cfg))

    frame = pd.DataFrame(sites)
    if frame.empty:
        raise ValueError("No mine sites could be constructed")
    frame["mine_site_id"] = [f"{cfg.state_code}-SITE-{i + 1:05d}" for i in range(len(frame))]
    gdf = gpd.GeoDataFrame(
        frame,
        geometry=[Point(xy) for xy in zip(frame["longitude"], frame["latitude"])],
        crs="EPSG:4326",
    )
    log.info(
        "Canonical mine sites: %d (state-source rows merged with MSHA: %d)",
        len(gdf),
        len(used_msha),
    )
    return gdf


def _site_from_state_row(row: pd.Series, cfg: PipelineConfig) -> dict:
    is_nv = cfg.state_code == "NV"
    audit = {
        "state_source_coordinates": [row.get("longitude"), row.get("latitude")],
        "merge": None,
        "raw_state_source": row.get("raw_source_json"),
    }
    return {
        "state": cfg.state_code,
        "canonical_mine_name": row.get("mine_name"),
        "mine_name_normalized": normalize_mine_name(row.get("mine_name")),
        "operator_name": row.get("operator_name"),
        "operator_name_normalized": normalize_entity_name(row.get("operator_name")),
        "controller_name": None,
        "commodity": row.get("commodity"),
        "commodity_normalized": normalize_mine_name(str(row.get("commodity") or ""), strip_generic=False),
        "county": row.get("county"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "source_point_quality": "state_source",
        "primary_source": "nevada_production" if is_nv else "utah_dogm",
        "state_source_id": row.get("state_mine_id"),
        "state_permit_number": row.get("permit_number"),
        "msha_mine_id": None,
        "msha_status": None,
        "msha_status_date": None,
        "latest_state_production_year": row.get("latest_production_year"),
        "state_production_years": row.get("production_years") if is_nv else None,
        "latest_inspection_date": None,
        "hours_last_4_quarters": 0.0,
        "hours_last_8_quarters": 0.0,
        "employees_last_4_quarters": 0.0,
        "inspections_last_18_months": 0,
        "state_production_confirmed": bool(row.get("production_years")) if is_nv else bool(
            row.get("state_production_indicator")
        ),
        "state_production_indicator": bool(row.get("state_production_indicator") or False),
        "state_permit_active": bool(row.get("state_permit_active") or False),
        "blm_plan_present": False,
        "blm_notice_present": False,
        "raw_evidence_json": compact_json(audit),
    }


def _attach_msha(site: dict, mrow: pd.Series, dist: float, name_sim: float, op_sim: float) -> None:
    site["msha_mine_id"] = mrow.get("msha_mine_id")
    site["msha_status"] = mrow.get("mine_status")
    site["msha_status_date"] = mrow.get("mine_status_date")
    site["controller_name"] = first_non_null(site.get("controller_name"), mrow.get("controller_name"))
    site["commodity"] = first_non_null(site.get("commodity"), mrow.get("primary_commodity"))
    site["county"] = first_non_null(site.get("county"), mrow.get("county"))
    site["latest_inspection_date"] = mrow.get("latest_inspection_date")
    site["hours_last_4_quarters"] = float(mrow.get("hours_last_4_quarters") or 0.0)
    site["hours_last_8_quarters"] = float(mrow.get("hours_last_8_quarters") or 0.0)
    site["employees_last_4_quarters"] = float(mrow.get("employees_last_4_quarters") or 0.0)
    site["inspections_last_18_months"] = int(mrow.get("inspections_last_18_months") or 0)
    import json

    audit = json.loads(site["raw_evidence_json"])
    audit["merge"] = {
        "msha_mine_id": mrow.get("msha_mine_id"),
        "distance_m": round(dist, 1),
        "name_similarity": name_sim,
        "operator_similarity": op_sim,
        "confidence": "high" if max(name_sim, op_sim) >= 90 else "medium",
        "msha_coordinates": [mrow.get("longitude"), mrow.get("latitude")],
    }
    site["raw_evidence_json"] = compact_json(audit)


def _site_from_msha_row(mrow: pd.Series, cfg: PipelineConfig) -> dict:
    audit = {
        "state_source_coordinates": None,
        "merge": None,
        "msha_coordinates": [mrow.get("longitude"), mrow.get("latitude")],
    }
    return {
        "state": cfg.state_code,
        "canonical_mine_name": mrow.get("mine_name"),
        "mine_name_normalized": normalize_mine_name(mrow.get("mine_name")),
        "operator_name": mrow.get("operator_name"),
        "operator_name_normalized": normalize_entity_name(mrow.get("operator_name")),
        "controller_name": mrow.get("controller_name"),
        "commodity": mrow.get("primary_commodity"),
        "commodity_normalized": normalize_mine_name(
            str(mrow.get("primary_commodity") or ""), strip_generic=False
        ),
        "county": mrow.get("county"),
        "latitude": mrow.get("latitude"),
        "longitude": mrow.get("longitude"),
        "source_point_quality": "msha_only",
        "primary_source": "msha_mines",
        "state_source_id": None,
        "state_permit_number": None,
        "msha_mine_id": mrow.get("msha_mine_id"),
        "msha_status": mrow.get("mine_status"),
        "msha_status_date": mrow.get("mine_status_date"),
        "latest_state_production_year": None,
        "state_production_years": None,
        "latest_inspection_date": mrow.get("latest_inspection_date"),
        "hours_last_4_quarters": float(mrow.get("hours_last_4_quarters") or 0.0),
        "hours_last_8_quarters": float(mrow.get("hours_last_8_quarters") or 0.0),
        "employees_last_4_quarters": float(mrow.get("employees_last_4_quarters") or 0.0),
        "inspections_last_18_months": int(mrow.get("inspections_last_18_months") or 0),
        "state_production_confirmed": False,
        "state_production_indicator": False,
        "state_permit_active": False,
        "blm_plan_present": False,
        "blm_notice_present": False,
        "raw_evidence_json": compact_json(audit),
    }


# --------------------------------------------------------------------------
# BLM operation association
# --------------------------------------------------------------------------


def associate_operations(
    mine_sites: gpd.GeoDataFrame,
    operations: gpd.GeoDataFrame,
    cfg: PipelineConfig,
) -> pd.DataFrame:
    """Associate BLM plans/notices with mine sites; returns association evidence."""
    columns = [
        "mine_site_id",
        "operation_id",
        "operation_kind",
        "basis",
        "distance_m",
        "name_similarity",
        "operator_similarity",
        "case_serial_number",
        "case_disposition",
    ]
    if operations is None or operations.empty or mine_sites.empty:
        return pd.DataFrame(columns=columns)

    mines_proj = to_projected(mine_sites, cfg.projected_crs).reset_index(drop=True)
    ops_proj = to_projected(operations, cfg.projected_crs).reset_index(drop=True)
    sindex = ops_proj.sindex
    rows: list[dict] = []
    max_dist = max(
        cfg.operation_name_assoc_distance_m, cfg.operation_operator_assoc_distance_m
    )
    for _, mine in mines_proj.iterrows():
        nearby = sindex.query(mine.geometry, predicate="dwithin", distance=max_dist)
        for pos in nearby:
            op = ops_proj.iloc[pos]
            dist = float(mine.geometry.distance(op.geometry))
            name_sim = similarity_score(mine.get("canonical_mine_name"), op.get("operation_name"))
            op_sim = similarity_score(mine.get("operator_name"), op.get("operator_name"))
            basis = None
            if dist == 0.0:
                basis = "mine_point_intersects_operation"
            elif (
                dist <= cfg.operation_name_assoc_distance_m
                and name_sim >= cfg.operation_name_similarity_min
            ):
                basis = "near_with_name_similarity"
            elif (
                dist <= cfg.operation_operator_assoc_distance_m
                and op_sim >= cfg.operation_operator_similarity_min
            ):
                basis = "operator_similarity"
            if basis is None:
                continue
            rows.append(
                {
                    "mine_site_id": mine["mine_site_id"],
                    "operation_id": op["operation_id"],
                    "operation_kind": op["operation_kind"],
                    "basis": basis,
                    "distance_m": round(dist, 1),
                    "name_similarity": name_sim,
                    "operator_similarity": op_sim,
                    "case_serial_number": op.get("case_serial_number"),
                    "case_disposition": op.get("case_disposition"),
                }
            )
    result = pd.DataFrame(rows, columns=columns)
    log.info("Operation associations: %d", len(result))
    return result


# --------------------------------------------------------------------------
# Claim blocks
# --------------------------------------------------------------------------


def assign_claim_blocks(claims: gpd.GeoDataFrame, cfg: PipelineConfig) -> gpd.GeoDataFrame:
    """Label connected components of claims within claim_cluster_distance_m."""
    claims = claims.reset_index(drop=True).copy()
    if claims.empty:
        claims["claim_block_id"] = []
        claims["claim_block_claim_count"] = []
        claims["claim_block_total_recorded_acres"] = []
        return claims
    proj = to_projected(claims, cfg.projected_crs)
    left, right = proj.sindex.query(
        proj.geometry, predicate="dwithin", distance=cfg.claim_cluster_distance_m
    )
    parent = list(range(len(proj)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in zip(left, right):
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[rb] = ra

    roots = [find(i) for i in range(len(proj))]
    block_ids = {root: f"{cfg.state_code}-BLK-{n + 1:05d}" for n, root in enumerate(dict.fromkeys(roots))}
    claims["claim_block_id"] = [block_ids[r] for r in roots]
    counts = claims.groupby("claim_block_id")["claim_id"].transform("count")
    acres = claims.groupby("claim_block_id")["recorded_acres"].transform(
        lambda s: s.fillna(0).sum()
    )
    claims["claim_block_claim_count"] = counts
    claims["claim_block_total_recorded_acres"] = acres
    return claims


# --------------------------------------------------------------------------
# Candidate generation
# --------------------------------------------------------------------------


def generate_candidates(
    mine_sites: gpd.GeoDataFrame,
    claims: gpd.GeoDataFrame,
    operations: gpd.GeoDataFrame,
    associations: pd.DataFrame,
    cfg: PipelineConfig,
) -> pd.DataFrame:
    """Generate mine x claim candidate pairs with match types and similarities.

    A candidate requires a spatial relationship within the review distance or a
    shared associated BLM operation geometry; name similarity alone never
    creates a candidate.
    """
    mines_proj = to_projected(mine_sites, cfg.projected_crs).reset_index(drop=True)
    claims_proj = to_projected(claims, cfg.projected_crs).reset_index(drop=True)
    ops_proj = (
        to_projected(operations, cfg.projected_crs).reset_index(drop=True)
        if operations is not None and not operations.empty
        else None
    )
    claim_sindex = claims_proj.sindex

    # Pre-compute which claims each associated operation intersects.
    op_claim_hits: dict[str, set[int]] = {}
    op_geom_by_id: dict[str, object] = {}
    if ops_proj is not None:
        for _, op in ops_proj.iterrows():
            hits = claim_sindex.query(op.geometry, predicate="intersects")
            op_claim_hits[op["operation_id"]] = {int(h) for h in hits}
            op_geom_by_id[op["operation_id"]] = op.geometry

    assoc_by_mine: dict[str, list[dict]] = {}
    if associations is not None and not associations.empty:
        for _, arow in associations.iterrows():
            assoc_by_mine.setdefault(arow["mine_site_id"], []).append(arow.to_dict())

    pairs: list[dict] = []
    for mpos, mine in mines_proj.iterrows():
        mine_assocs = assoc_by_mine.get(mine["mine_site_id"], [])
        plan_assocs = [a for a in mine_assocs if a["operation_kind"] == "plan"]
        notice_assocs = [a for a in mine_assocs if a["operation_kind"] == "notice"]

        candidate_positions: dict[int, dict] = {}

        nearby = claim_sindex.query(
            mine.geometry, predicate="dwithin", distance=cfg.review_match_distance_m
        )
        for cpos in nearby:
            candidate_positions.setdefault(int(cpos), {"via": "spatial"})

        plan_hits_for_mine: set[int] = set()
        notice_hits_for_mine: set[int] = set()
        for assoc in plan_assocs:
            for cpos in op_claim_hits.get(assoc["operation_id"], set()):
                plan_hits_for_mine.add(cpos)
                candidate_positions.setdefault(cpos, {"via": "plan"})
        for assoc in notice_assocs:
            for cpos in op_claim_hits.get(assoc["operation_id"], set()):
                notice_hits_for_mine.add(cpos)
                candidate_positions.setdefault(cpos, {"via": "notice"})

        mine_near_plan = False
        for assoc in plan_assocs:
            geom = op_geom_by_id.get(assoc["operation_id"])
            if geom is not None and mine.geometry.distance(geom) <= cfg.near_match_distance_m:
                mine_near_plan = True
                break

        for cpos in sorted(candidate_positions):
            claim = claims_proj.iloc[cpos]
            distance = float(mine.geometry.distance(claim.geometry))
            point_intersects = bool(mine.geometry.intersects(claim.geometry))
            plan_intersects = cpos in plan_hits_for_mine
            notice_intersects = cpos in notice_hits_for_mine

            if point_intersects and plan_intersects:
                match_type = "PLAN_AND_MINE_INTERSECT"
            elif plan_intersects and (mine_near_plan or point_intersects):
                match_type = "PLAN_INTERSECTS_CLAIM"
            elif point_intersects:
                match_type = "MINE_POINT_INTERSECTS_CLAIM"
            elif notice_intersects:
                match_type = "NOTICE_INTERSECTS_CLAIM"
            elif distance <= cfg.near_match_distance_m:
                match_type = "NEAR_CLAIM_0_250M"
            elif distance <= cfg.review_match_distance_m:
                match_type = "REVIEW_DISTANCE_250_1000M"
            elif plan_intersects:
                match_type = "PLAN_INTERSECTS_CLAIM"
            elif notice_intersects:
                match_type = "NOTICE_INTERSECTS_CLAIM"
            else:
                continue

            name_sim = similarity_score(mine.get("canonical_mine_name"), claim.get("claim_name"))
            op_name_sim = max(
                (float(a.get("name_similarity") or 0) for a in mine_assocs), default=0.0
            )
            op_operator_sim = max(
                (float(a.get("operator_similarity") or 0) for a in mine_assocs), default=0.0
            )
            claim_serial = str(claim.get("claim_serial_number") or "")
            serial_in_operation = any(
                claim_serial
                and claim_serial == str(a.get("case_serial_number") or "")
                for a in mine_assocs
            )
            pairs.append(
                {
                    "match_id": f"{mine['mine_site_id']}__{claim['claim_id']}",
                    "mine_site_id": mine["mine_site_id"],
                    "claim_id": claim["claim_id"],
                    "state": cfg.state_code,
                    "mine_name": mine.get("canonical_mine_name"),
                    "operator_name": mine.get("operator_name"),
                    "commodity": mine.get("commodity"),
                    "county": mine.get("county"),
                    "claim_serial_number": claim.get("claim_serial_number"),
                    "claim_name": claim.get("claim_name"),
                    "claim_type": claim.get("claim_type"),
                    "claim_block_id": claim.get("claim_block_id"),
                    "salesforce_id": claim.get("salesforce_id"),
                    "case_page": claim.get("case_page"),
                    "match_type": match_type,
                    "point_intersects_claim": point_intersects,
                    "plan_intersects_claim": plan_intersects,
                    "notice_intersects_claim": notice_intersects,
                    "distance_meters": round(distance, 1),
                    "mine_claim_name_similarity": name_sim,
                    "operator_claimant_similarity": None,  # no reliable claimant field
                    "operation_mine_name_similarity": op_name_sim,
                    "operation_operator_similarity": op_operator_sim,
                    "serial_in_operation": serial_in_operation,
                    "geometry_quality_group": claim.get("geometry_quality_group"),
                    "claim_geometry_quality": claim.get("geometry_quality"),
                }
            )
    result = pd.DataFrame(pairs)
    log.info("Candidate mine-claim pairs: %d", len(result))
    return result
