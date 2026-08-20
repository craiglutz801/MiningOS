"""Transparent, deterministic 0-100 scoring.

Components:
    activity (0..50) + claim match (0..30) + data quality (0..20) + penalties (0..-35)
Every awarded or deducted rule is recorded in the score breakdown. All weights
live in the module-level tables below so they can be reviewed and edited in one
place; the UI never embeds weights.
"""

from __future__ import annotations

from typing import Any

from mining_os.active_mine_intel.matcher.config import PipelineConfig
from mining_os.active_mine_intel.matcher.utilities import compact_json

ACTIVITY_MAX = 50
MATCH_MAX = 30
QUALITY_MAX = 20
PENALTY_MIN = -35

# ---------------------------------------------------------------- weights

NEVADA_PRODUCTION_RECENCY_POINTS = [
    # (max years behind latest available reporting year, points, label)
    (0, 40, "Reported production in latest available state-production year"),
    (1, 34, "Reported production one year before latest available year"),
    (2, 27, "Reported production two years before latest available year"),
    (5, 18, "Reported production 3-5 years before latest available year"),
    (None, 8, "Production record exists but latest report is more than 5 years old"),
]

NEVADA_SUPPORT_POINTS = {
    "msha_active": (6, "MSHA status Active"),
    "msha_intermittent": (4, "MSHA status Intermittent"),
    "hours_last_4q": (6, "Positive MSHA hours in last 4 quarters"),
    "hours_5_to_8q_only": (3, "Positive MSHA hours in quarters 5-8 only"),
    "recent_inspection": (4, "MSHA inspection in last 18 months"),
    "blm_plan": (4, "BLM Plan of Operations associated"),
    "blm_notice": (2, "BLM Notice associated"),
}

UTAH_ACTIVITY_POINTS = {
    "dogm_active": (15, "DOGM active/current mine or permit"),
    "state_production_indicator": (
        20,
        "Structured state source explicitly indicates producing/production/current extraction",
    ),
    "msha_active": (10, "MSHA status Active"),
    "msha_intermittent": (7, "MSHA status Intermittent"),
    "hours_last_4q": (12, "Positive MSHA hours in last 4 quarters"),
    "hours_5_to_8q_only": (6, "Positive MSHA hours in quarters 5-8 only"),
    "recent_inspection": (6, "MSHA inspection in last 18 months"),
    "blm_plan": (8, "BLM Plan of Operations associated"),
    "blm_notice": (3, "BLM Notice associated"),
}

MATCH_BASE_POINTS = {
    "PLAN_AND_MINE_INTERSECT": (24, "Plan and mine point both intersect claim (published geometry)"),
    "PLAN_INTERSECTS_CLAIM": (21, "Associated plan intersects claim (published geometry)"),
    "MINE_POINT_INTERSECTS_CLAIM": (19, "Mine point intersects claim (published geometry)"),
    "NOTICE_INTERSECTS_CLAIM": (15, "Associated notice intersects claim (published geometry)"),
    "NEAR_CLAIM_0_250M": (11, "Mine is within 0-250 m of claim"),
    "REVIEW_DISTANCE_250_1000M": (4, "Mine is within 250-1,000 m of claim"),
}

MATCH_SUPPORT_POINTS = {
    "name_sim_high": (4, "Mine name <-> claim name similarity >= 90"),
    "name_sim_mid": (2, "Mine name <-> claim name similarity 75-89.99"),
    "operation_sim_high": (4, "Mine/operator <-> BLM operation similarity >= 90"),
    "operation_sim_mid": (2, "Mine/operator <-> BLM operation similarity 75-89.99"),
    "serial_in_operation": (6, "Same claim serial appears in operation record"),
}

QUALITY_POINTS = {
    "plss_direct": (10, "BLM geometry-quality indicates direct PLSS match (code 0-3)"),
    "valid_polygon": (2, "Valid claim polygon geometry with nonzero area"),
    "state_geometry_source": (
        3,
        "Mine source is state production/DOGM geometry rather than MSHA-only point",
    ),
    "plan_geometry": (3, "Supporting BLM Plan geometry exists"),
    "multi_source_agreement": (2, "Multiple independent activity sources agree"),
}

PENALTY_POINTS = {
    "msha_nonproducing": (-15, "MSHA status NonProducing / NonProdActive"),
    "msha_temp_idle": (-12, "MSHA status Temporarily Idled"),
    "msha_abandoned": (-35, "MSHA status Abandoned or AbandonedSealed"),
    "no_recent_activity": (-10, "No activity evidence in last 5 years"),
    "stale_state_production": (
        -15,
        "State production record older than 10 years with no recent MSHA activity",
    ),
    "low_quality_coordinate": (
        -6,
        "Mine coordinate flagged low quality or outside expected source geometry",
    ),
    "weak_distant_match": (
        -8,
        "Only match is 250-1,000 m away and name similarity < 60",
    ),
    "coarse_claim_geometry": (
        -8,
        "Claim geometry quality indicates coarse section/county representation",
    ),
    "degraded_run": (-5, "Required source unavailable in degraded run"),
}

# ---------------------------------------------------------------- helpers


def normalize_msha_status(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    if not text or text == "none" or text == "nan":
        return "UNKNOWN"
    if "abandoned" in text:
        return "ABANDONED"
    if "temporarily" in text or "temp" in text and "idle" in text:
        return "TEMP_IDLE"
    if "intermittent" in text:
        return "INTERMITTENT"
    if "nonproducing" in text or "non producing" in text or "nonprod" in text:
        return "NONPRODUCING"
    if "active" in text:
        return "ACTIVE"
    return "OTHER"


def _msha_flags(site: dict) -> dict:
    status = normalize_msha_status(site.get("msha_status"))
    hours4 = float(site.get("hours_last_4_quarters") or 0.0)
    hours8 = float(site.get("hours_last_8_quarters") or 0.0)
    return {
        "status": status,
        "hours_4q_positive": hours4 > 0,
        "hours_5to8_only": hours4 <= 0 and (hours8 - hours4) > 0,
        "recent_inspection": int(site.get("inspections_last_18_months") or 0) > 0,
    }


def _add(rules: list, breakdown_key: str, points: float, label: str) -> float:
    rules.append({"rule": breakdown_key, "points": points, "label": label})
    return points


# ---------------------------------------------------------------- activity


def score_activity(
    site: dict, state_code: str, latest_available_year: int | None
) -> tuple[float, list[dict]]:
    rules: list[dict] = []
    total = 0.0
    flags = _msha_flags(site)

    if state_code == "NV":
        latest = site.get("latest_state_production_year")
        if latest is not None and latest == latest:  # not NaN
            latest = int(latest)
            anchor = latest_available_year if latest_available_year is not None else latest
            behind = anchor - latest
            for max_behind, points, label in NEVADA_PRODUCTION_RECENCY_POINTS:
                if max_behind is None or behind <= max_behind:
                    total += _add(rules, f"nv_production_recency_{max_behind}", points, label)
                    break
        support = NEVADA_SUPPORT_POINTS
        if flags["status"] == "ACTIVE":
            total += _add(rules, "msha_active", *support["msha_active"])
        elif flags["status"] == "INTERMITTENT":
            total += _add(rules, "msha_intermittent", *support["msha_intermittent"])
        if flags["hours_4q_positive"]:
            total += _add(rules, "hours_last_4q", *support["hours_last_4q"])
        elif flags["hours_5to8_only"]:
            total += _add(rules, "hours_5_to_8q_only", *support["hours_5_to_8q_only"])
        if flags["recent_inspection"]:
            total += _add(rules, "recent_inspection", *support["recent_inspection"])
        if site.get("blm_plan_present"):
            total += _add(rules, "blm_plan", *support["blm_plan"])
        if site.get("blm_notice_present"):
            total += _add(rules, "blm_notice", *support["blm_notice"])
    else:  # Utah
        points_table = UTAH_ACTIVITY_POINTS
        if site.get("state_permit_active"):
            total += _add(rules, "dogm_active", *points_table["dogm_active"])
        if site.get("state_production_indicator"):
            total += _add(
                rules, "state_production_indicator", *points_table["state_production_indicator"]
            )
        if flags["status"] == "ACTIVE":
            total += _add(rules, "msha_active", *points_table["msha_active"])
        elif flags["status"] == "INTERMITTENT":
            total += _add(rules, "msha_intermittent", *points_table["msha_intermittent"])
        if flags["hours_4q_positive"]:
            total += _add(rules, "hours_last_4q", *points_table["hours_last_4q"])
        elif flags["hours_5to8_only"]:
            total += _add(rules, "hours_5_to_8q_only", *points_table["hours_5_to_8q_only"])
        if flags["recent_inspection"]:
            total += _add(rules, "recent_inspection", *points_table["recent_inspection"])
        if site.get("blm_plan_present"):
            total += _add(rules, "blm_plan", *points_table["blm_plan"])
        if site.get("blm_notice_present"):
            total += _add(rules, "blm_notice", *points_table["blm_notice"])

    return min(total, ACTIVITY_MAX), rules


# ---------------------------------------------------------------- match


def score_match(pair: dict) -> tuple[float, list[dict]]:
    rules: list[dict] = []
    total = 0.0
    match_type = pair.get("match_type")
    if match_type in MATCH_BASE_POINTS:
        total += _add(rules, f"match_{match_type}", *MATCH_BASE_POINTS[match_type])

    name_sim = float(pair.get("mine_claim_name_similarity") or 0.0)
    if name_sim >= 90:
        total += _add(rules, "name_sim_high", *MATCH_SUPPORT_POINTS["name_sim_high"])
    elif name_sim >= 75:
        total += _add(rules, "name_sim_mid", *MATCH_SUPPORT_POINTS["name_sim_mid"])

    op_sim = max(
        float(pair.get("operation_mine_name_similarity") or 0.0),
        float(pair.get("operation_operator_similarity") or 0.0),
    )
    if op_sim >= 90:
        total += _add(rules, "operation_sim_high", *MATCH_SUPPORT_POINTS["operation_sim_high"])
    elif op_sim >= 75:
        total += _add(rules, "operation_sim_mid", *MATCH_SUPPORT_POINTS["operation_sim_mid"])

    if pair.get("serial_in_operation"):
        total += _add(rules, "serial_in_operation", *MATCH_SUPPORT_POINTS["serial_in_operation"])

    return min(total, MATCH_MAX), rules


# ---------------------------------------------------------------- quality


def score_quality(pair: dict, site: dict) -> tuple[float, list[dict]]:
    rules: list[dict] = []
    total = 0.0
    group = pair.get("geometry_quality_group") or "UNKNOWN"
    if group == "PLSS_DIRECT":
        total += _add(rules, "plss_direct", *QUALITY_POINTS["plss_direct"])
    # UNKNOWN quality: award 0, expose UNKNOWN — never guess.
    if pair.get("claim_has_valid_polygon", True):
        total += _add(rules, "valid_polygon", *QUALITY_POINTS["valid_polygon"])
    if site.get("source_point_quality") == "state_source":
        total += _add(rules, "state_geometry_source", *QUALITY_POINTS["state_geometry_source"])
    if pair.get("plan_intersects_claim") or site.get("blm_plan_present"):
        total += _add(rules, "plan_geometry", *QUALITY_POINTS["plan_geometry"])
    independent_sources = sum(
        [
            bool(site.get("state_production_confirmed") or site.get("state_permit_active")),
            bool(
                normalize_msha_status(site.get("msha_status")) in ("ACTIVE", "INTERMITTENT")
                or float(site.get("hours_last_4_quarters") or 0) > 0
            ),
            bool(site.get("blm_plan_present") or site.get("blm_notice_present")),
        ]
    )
    if independent_sources >= 2:
        total += _add(
            rules, "multi_source_agreement", *QUALITY_POINTS["multi_source_agreement"]
        )
    return min(total, QUALITY_MAX), rules


# ---------------------------------------------------------------- penalties


def score_penalties(
    pair: dict,
    site: dict,
    state_code: str,
    current_year: int,
    degraded_mode: bool = False,
) -> tuple[float, list[dict]]:
    rules: list[dict] = []
    total = 0.0
    flags = _msha_flags(site)

    if flags["status"] == "NONPRODUCING":
        total += _add(rules, "msha_nonproducing", *PENALTY_POINTS["msha_nonproducing"])
    elif flags["status"] == "TEMP_IDLE":
        total += _add(rules, "msha_temp_idle", *PENALTY_POINTS["msha_temp_idle"])
    elif flags["status"] == "ABANDONED":
        total += _add(rules, "msha_abandoned", *PENALTY_POINTS["msha_abandoned"])

    latest_year = site.get("latest_state_production_year")
    latest_year = int(latest_year) if latest_year is not None and latest_year == latest_year else None
    latest_activity_year = _latest_activity_year(site, latest_year, current_year)
    has_msha_recent = flags["hours_4q_positive"] or flags["recent_inspection"]

    if latest_activity_year is None or current_year - latest_activity_year > 5:
        total += _add(rules, "no_recent_activity", *PENALTY_POINTS["no_recent_activity"])
    if (
        latest_year is not None
        and current_year - latest_year > 10
        and not has_msha_recent
    ):
        total += _add(
            rules, "stale_state_production", *PENALTY_POINTS["stale_state_production"]
        )
    if site.get("coordinate_low_quality"):
        total += _add(
            rules, "low_quality_coordinate", *PENALTY_POINTS["low_quality_coordinate"]
        )
    if (
        pair.get("match_type") == "REVIEW_DISTANCE_250_1000M"
        and float(pair.get("mine_claim_name_similarity") or 0) < 60
    ):
        total += _add(rules, "weak_distant_match", *PENALTY_POINTS["weak_distant_match"])
    if (pair.get("geometry_quality_group") or "UNKNOWN") == "COARSE":
        total += _add(
            rules, "coarse_claim_geometry", *PENALTY_POINTS["coarse_claim_geometry"]
        )
    if degraded_mode:
        total += _add(rules, "degraded_run", *PENALTY_POINTS["degraded_run"])

    return max(total, PENALTY_MIN), rules


def _latest_activity_year(
    site: dict, latest_production_year: int | None, current_year: int
) -> int | None:
    years = []
    if latest_production_year is not None:
        years.append(latest_production_year)
    flags = _msha_flags(site)
    if flags["hours_4q_positive"] or flags["recent_inspection"]:
        years.append(current_year)
    insp = site.get("latest_inspection_date")
    if insp is not None and str(insp) not in ("NaT", "None", "nan", ""):
        try:
            years.append(int(str(insp)[:4]))
        except ValueError:
            pass
    if float(site.get("hours_last_8_quarters") or 0) > 0:
        quarter = site.get("latest_reported_quarter")
        if quarter:
            try:
                years.append(int(str(quarter).split()[0]))
            except ValueError:
                pass
    # A currently active state permit is current activity evidence (though never
    # production evidence by itself).
    if site.get("state_permit_active"):
        years.append(current_year)
    return max(years) if years else None


# ---------------------------------------------------------------- labels


def mine_activity_label(
    site: dict, state_code: str, latest_available_year: int | None, current_year: int
) -> str:
    flags = _msha_flags(site)
    abandoned = flags["status"] == "ABANDONED"
    has_activity = (
        flags["status"] in ("ACTIVE", "INTERMITTENT")
        or flags["hours_4q_positive"]
        or flags["recent_inspection"]
        or site.get("blm_plan_present")
        or site.get("blm_notice_present")
    )
    if state_code == "NV":
        latest = site.get("latest_state_production_year")
        latest = int(latest) if latest is not None and latest == latest else None
        anchor = latest_available_year or current_year
        if abandoned:
            return "INACTIVE_OR_CONTRADICTORY"
        if latest is not None and anchor - latest <= 1:
            return "CONFIRMED_RECENT_PRODUCTION_EVIDENCE"
        if latest is not None and anchor - latest <= 5:
            return "RECENT_PRODUCTION_EVIDENCE"
        if latest is not None and has_activity:
            return "OPERATING_EVIDENCE_PRODUCTION_STALE"
        if has_activity:
            return "ACTIVITY_EVIDENCE_ONLY"
        return "INACTIVE_OR_CONTRADICTORY"
    # Utah: an active permit is never called confirmed production.
    if abandoned:
        return "INACTIVE_OR_CONTRADICTORY"
    strong_msha = flags["status"] == "ACTIVE" and flags["hours_4q_positive"]
    if site.get("state_permit_active") and (
        site.get("state_production_indicator") or strong_msha
    ):
        return "STRONG_OPERATING_EVIDENCE"
    if site.get("state_permit_active") and (
        flags["hours_4q_positive"]
        or flags["recent_inspection"]
        or flags["status"] in ("ACTIVE", "INTERMITTENT")
    ):
        return "LIKELY_OPERATING_PRODUCTION_UNCONFIRMED"
    if site.get("state_permit_active"):
        return "PERMITTED_ACTIVITY_NEEDS_VERIFICATION"
    if has_activity:
        return "WEAK_ACTIVITY_EVIDENCE"
    return "INACTIVE_OR_CONTRADICTORY"


def confidence_category(total_score: float, cfg: PipelineConfig) -> str:
    if total_score >= cfg.high_score_threshold:
        return "HIGH"
    if total_score >= cfg.strong_score_threshold:
        return "STRONG"
    if total_score >= cfg.review_score_threshold:
        return "REVIEW"
    if total_score >= cfg.weak_score_threshold:
        return "WEAK"
    return "LOW"


def recommended_next_action(
    pair: dict, site: dict, category: str, state_code: str, label: str
) -> str:
    match_type = pair.get("match_type")
    if match_type in ("PLAN_AND_MINE_INTERSECT", "PLAN_INTERSECTS_CLAIM"):
        return (
            "Review the BLM Plan of Operations case file and map. Confirm the plan "
            "operator, project name, authorized disturbance footprint, and linked "
            "claim serial numbers."
        )
    if category in ("HIGH", "STRONG") and match_type == "MINE_POINT_INTERSECTS_CLAIM":
        return (
            "Retrieve the MLRS Serial Register Page for the matched claim, obtain the "
            "county-recorded location notice and claim map, and compare the recorded "
            "boundary with the mine/permit footprint."
        )
    if state_code == "UT" and label in (
        "PERMITTED_ACTIVITY_NEEDS_VERIFICATION",
        "LIKELY_OPERATING_PRODUCTION_UNCONFIRMED",
    ):
        return (
            "Review the Utah DOGM permit file and recent MSHA employment/inspection "
            "history to determine whether extraction is currently occurring."
        )
    if state_code == "NV" and label == "OPERATING_EVIDENCE_PRODUCTION_STALE":
        return (
            "Confirm whether the operation remains active using recent MSHA hours, "
            "inspections, company disclosures, and the current Nevada major-mine report."
        )
    if match_type in ("NEAR_CLAIM_0_250M", "REVIEW_DISTANCE_250_1000M"):
        return (
            "Do not treat this as an overlap. Check coordinate accuracy, state permit "
            "maps, county claim maps, and the BLM geometry-quality value before "
            "further use."
        )
    return (
        "Retrieve the MLRS Serial Register Page for the matched claim and verify the "
        "recorded location documents before relying on this candidate."
    )


# ---------------------------------------------------------------- entry point


def score_candidate(
    pair: dict,
    site: dict,
    cfg: PipelineConfig,
    latest_available_year: int | None,
    current_year: int,
    degraded_mode: bool = False,
) -> dict:
    activity, activity_rules = score_activity(site, cfg.state_code, latest_available_year)
    match, match_rules = score_match(pair)
    quality, quality_rules = score_quality(pair, site)
    penalties, penalty_rules = score_penalties(
        pair, site, cfg.state_code, current_year, degraded_mode
    )
    total = max(0.0, min(100.0, activity + match + quality + penalties))
    category = confidence_category(total, cfg)
    label = mine_activity_label(site, cfg.state_code, latest_available_year, current_year)
    breakdown = {
        "activity": {"points": activity, "max": ACTIVITY_MAX, "rules": activity_rules},
        "claim_match": {"points": match, "max": MATCH_MAX, "rules": match_rules},
        "data_quality": {"points": quality, "max": QUALITY_MAX, "rules": quality_rules},
        "penalties": {"points": penalties, "min": PENALTY_MIN, "rules": penalty_rules},
        "total": total,
    }
    return {
        "activity_score": round(activity, 1),
        "claim_match_score": round(match, 1),
        "data_quality_score": round(quality, 1),
        "penalty_score": round(penalties, 1),
        "total_score": round(total, 1),
        "confidence_category": category,
        "mine_activity_label": label,
        "score_breakdown_json": compact_json(breakdown),
        "recommended_next_action": recommended_next_action(
            pair, site, category, cfg.state_code, label
        ),
        "verification_status": "Not Reviewed",
    }
