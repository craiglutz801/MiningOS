"""Persistence helpers for Active Mine Search runs and candidate sites."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.active_mine_intel.store")


def _jsonable(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return {} if "{" in (val[:1] or "") else []
        try:
            return json.loads(s)
        except Exception:
            return {"raw": s}
    try:
        # pandas / numpy
        if hasattr(val, "item"):
            return val.item()
    except Exception:
        pass
    return val


def create_run(account_id: int, state_abbr: str, refresh: bool = True) -> str:
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO active_mine_intel.runs (account_id, state_abbr, status, refresh)
                VALUES (:aid, :st, 'running', :refresh)
                RETURNING id
                """
            ),
            {"aid": account_id, "st": state_abbr.upper(), "refresh": refresh},
        ).first()
    return str(row[0])


def update_run(run_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "finished_at",
        "matcher_run_id",
        "site_count",
        "linked_count",
        "unresolved_plss",
        "targets_created",
        "targets_reused",
        "qc_json",
        "manifest_json",
        "error_message",
    }
    sets = []
    params: dict[str, Any] = {"id": run_id}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k.endswith("_json") and not isinstance(v, str):
            v = json.dumps(v or {})
        sets.append(f"{k} = :{k}")
        params[k] = v
    if not sets:
        return
    sets.append("updated_at = now()")
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(f"UPDATE active_mine_intel.runs SET {', '.join(sets)} WHERE id = :id"),
            params,
        )


def get_run(run_id: str, account_id: int | None = None) -> dict[str, Any] | None:
    eng = get_engine()
    clauses = ["id = :id"]
    params: dict[str, Any] = {"id": run_id}
    if account_id is not None:
        clauses.append("account_id = :aid")
        params["aid"] = account_id
    with eng.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT id::text, account_id, state_abbr, status, refresh, started_at, finished_at,
                       matcher_run_id, site_count, linked_count, unresolved_plss,
                       targets_created, targets_reused, qc_json, manifest_json, error_message
                FROM active_mine_intel.runs
                WHERE {' AND '.join(clauses)}
                """
            ),
            params,
        ).mappings().first()
    return _decorate_run(dict(row)) if row else None


def get_latest_run(
    account_id: int,
    *,
    state: str | None = None,
    running_only: bool = False,
) -> dict[str, Any] | None:
    eng = get_engine()
    clauses = ["account_id = :aid"]
    params: dict[str, Any] = {"aid": account_id}
    if state:
        clauses.append("state_abbr = :st")
        params["st"] = state.upper()
    if running_only:
        clauses.append("status IN ('running', 'pending')")
    with eng.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT id::text, account_id, state_abbr, status, refresh, started_at, finished_at,
                       matcher_run_id, site_count, linked_count, unresolved_plss,
                       targets_created, targets_reused, qc_json, manifest_json, error_message
                FROM active_mine_intel.runs
                WHERE {' AND '.join(clauses)}
                ORDER BY started_at DESC
                LIMIT 1
                """
            ),
            params,
        ).mappings().first()
    return _decorate_run(dict(row)) if row else None


def set_run_progress(
    run_id: str,
    *,
    stage: int,
    total_stages: int,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Merge progress into qc_json so the UI can poll stage / percent."""
    pct = int(max(0, min(100, round(100.0 * stage / max(total_stages, 1)))))
    progress = {
        "stage": stage,
        "total_stages": total_stages,
        "percent": pct,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if detail:
        progress["detail"] = detail
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE active_mine_intel.runs
                SET qc_json = COALESCE(qc_json, '{}'::jsonb) || CAST(:prog AS jsonb),
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": run_id, "prog": json.dumps({"progress": progress})},
        )


def _decorate_run(row: dict[str, Any]) -> dict[str, Any]:
    qc = row.get("qc_json") or {}
    if isinstance(qc, str):
        try:
            qc = json.loads(qc)
        except Exception:
            qc = {}
    progress = qc.get("progress") if isinstance(qc, dict) else None
    if isinstance(progress, dict):
        row["progress"] = progress
        row["progress_percent"] = progress.get("percent")
        row["progress_message"] = progress.get("message")
        row["progress_stage"] = progress.get("stage")
        row["progress_total"] = progress.get("total_stages")
    return row


def replace_candidates_for_state(
    account_id: int,
    state_abbr: str,
    run_id: str,
    sites: list[dict[str, Any]],
    matches: list[dict[str, Any]] | None = None,
) -> int:
    """Delete prior candidates for account+state and insert the new run's rows."""
    eng = get_engine()
    state = state_abbr.upper()
    with eng.begin() as conn:
        # Drop prior matches for previous runs of this account+state
        conn.execute(
            text(
                """
                DELETE FROM active_mine_intel.candidate_matches
                WHERE account_id = :aid
                  AND run_id IN (
                    SELECT id FROM active_mine_intel.runs
                    WHERE account_id = :aid AND state_abbr = :st AND id <> :rid
                  )
                """
            ),
            {"aid": account_id, "st": state, "rid": run_id},
        )
        conn.execute(
            text(
                """
                DELETE FROM active_mine_intel.candidate_sites
                WHERE account_id = :aid AND state_abbr = :st
                """
            ),
            {"aid": account_id, "st": state},
        )
        for site in sites:
            serials = site.get("claim_serials") or []
            if isinstance(serials, str):
                serials = [s.strip() for s in serials.replace(";", ",").split(",") if s.strip()]
            conn.execute(
                text(
                    """
                    INSERT INTO active_mine_intel.candidate_sites (
                      account_id, run_id, mine_site_id, state_abbr, rank, name, operator_name,
                      commodity, county, latitude, longitude,
                      total_score, activity_score, claim_match_score, data_quality_score,
                      penalty_score, confidence_category, activity_label,
                      best_claim_serial, best_claim_name, best_match_type, best_distance_meters,
                      claim_count, claim_serials, blm_plan_present, blm_notice_present, msha_status,
                      score_breakdown_json, evidence_summary_json, recommended_next_action,
                      location_plss, township, range, section, meridian, plss_normalized,
                      plss_source, plss_status, area_of_focus_id
                    ) VALUES (
                      :aid, :rid, :msid, :st, :rank, :name, :op,
                      :commodity, :county, :lat, :lon,
                      :total, :act, :cms, :dqs, :pen, :conf, :alabel,
                      :bcs, :bcn, :bmt, :bdm,
                      :cc, :serials, :plan, :notice, :msha,
                      CAST(:sb AS jsonb), CAST(:ev AS jsonb), :rna,
                      :lplss, :twp, :rng, :sec, :mer, :plssn,
                      :psrc, :pstat, :aof
                    )
                    """
                ),
                {
                    "aid": account_id,
                    "rid": run_id,
                    "msid": site["mine_site_id"],
                    "st": state,
                    "rank": site.get("rank"),
                    "name": site.get("name"),
                    "op": site.get("operator_name"),
                    "commodity": site.get("commodity"),
                    "county": site.get("county"),
                    "lat": site.get("latitude"),
                    "lon": site.get("longitude"),
                    "total": site.get("total_score"),
                    "act": site.get("activity_score"),
                    "cms": site.get("claim_match_score"),
                    "dqs": site.get("data_quality_score"),
                    "pen": site.get("penalty_score"),
                    "conf": site.get("confidence_category"),
                    "alabel": site.get("activity_label"),
                    "bcs": site.get("best_claim_serial"),
                    "bcn": site.get("best_claim_name"),
                    "bmt": site.get("best_match_type"),
                    "bdm": site.get("best_distance_meters"),
                    "cc": int(site.get("claim_count") or 0),
                    "serials": serials,
                    "plan": bool(site.get("blm_plan_present")),
                    "notice": bool(site.get("blm_notice_present")),
                    "msha": site.get("msha_status"),
                    "sb": json.dumps(_jsonable(site.get("score_breakdown_json") or {})),
                    "ev": json.dumps(_jsonable(site.get("evidence_summary_json") or [])),
                    "rna": site.get("recommended_next_action"),
                    "lplss": site.get("location_plss"),
                    "twp": site.get("township"),
                    "rng": site.get("range"),
                    "sec": site.get("section"),
                    "mer": site.get("meridian"),
                    "plssn": site.get("plss_normalized"),
                    "psrc": site.get("plss_source"),
                    "pstat": site.get("plss_status") or "unresolved",
                    "aof": site.get("area_of_focus_id"),
                },
            )
        for m in matches or []:
            conn.execute(
                text(
                    """
                    INSERT INTO active_mine_intel.candidate_matches (
                      account_id, run_id, mine_site_id, claim_serial_number, claim_name,
                      match_type, distance_meters, total_score, activity_score,
                      claim_match_score, confidence_category,
                      score_breakdown_json, evidence_summary_json
                    ) VALUES (
                      :aid, :rid, :msid, :serial, :cname,
                      :mt, :dist, :total, :act, :cms, :conf,
                      CAST(:sb AS jsonb), CAST(:ev AS jsonb)
                    )
                    """
                ),
                {
                    "aid": account_id,
                    "rid": run_id,
                    "msid": m.get("mine_site_id"),
                    "serial": m.get("claim_serial_number") or m.get("best_claim_serial"),
                    "cname": m.get("claim_name"),
                    "mt": m.get("match_type"),
                    "dist": m.get("distance_meters"),
                    "total": m.get("total_score"),
                    "act": m.get("activity_score"),
                    "cms": m.get("claim_match_score"),
                    "conf": m.get("confidence_category"),
                    "sb": json.dumps(_jsonable(m.get("score_breakdown_json") or {})),
                    "ev": json.dumps(_jsonable(m.get("evidence_summary_json") or [])),
                },
            )
    return len(sites)


def list_sites(
    account_id: int,
    *,
    state: str | None = None,
    min_score: float | None = 55.0,
    confidence: str | None = None,
    include_low: bool = False,
    unpaid_only: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    eng = get_engine()
    clauses = ["account_id = :aid"]
    params: dict[str, Any] = {"aid": account_id}
    if state:
        clauses.append("state_abbr = :st")
        params["st"] = state.upper()
    if min_score is not None:
        clauses.append("COALESCE(total_score, 0) >= :min_score")
        params["min_score"] = float(min_score)
    if confidence:
        clauses.append("confidence_category = :conf")
        params["conf"] = confidence.upper()
    elif not include_low:
        clauses.append("(confidence_category IS NULL OR confidence_category <> 'LOW')")
    if unpaid_only:
        clauses.append("COALESCE(unpaid_claim_count, 0) > 0")
    if search:
        clauses.append(
            "(name ILIKE :q OR county ILIKE :q OR best_claim_serial ILIKE :q OR location_plss ILIKE :q)"
        )
        params["q"] = f"%{search.strip()}%"

    where = " AND ".join(clauses)
    offset = max(0, (page - 1) * page_size)
    params["lim"] = page_size
    params["off"] = offset

    with eng.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM active_mine_intel.candidate_sites WHERE {where}"),
            params,
        ).scalar() or 0
        rows = conn.execute(
            text(
                f"""
                SELECT id::text, mine_site_id, state_abbr, rank, name, operator_name, commodity,
                       county, latitude, longitude, total_score, activity_score, claim_match_score,
                       confidence_category, activity_label, best_claim_serial, best_claim_name,
                       best_match_type, best_distance_meters, claim_count, claim_serials,
                       location_plss, township, range, section, meridian, plss_normalized,
                       plss_status, area_of_focus_id, unpaid_claim_count, paid_claim_count,
                       unknown_claim_count, mlrs_claim_count, claim_status_rollup,
                       claims_fetched_at, recommended_next_action, run_id::text
                FROM active_mine_intel.candidate_sites
                WHERE {where}
                ORDER BY COALESCE(total_score, 0) DESC, rank ASC NULLS LAST
                LIMIT :lim OFFSET :off
                """
            ),
            params,
        ).mappings().all()
    return {
        "ok": True,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "sites": [dict(r) for r in rows],
    }


def get_site(account_id: int, site_id: str) -> dict[str, Any] | None:
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id::text, mine_site_id, state_abbr, rank, name, operator_name, commodity,
                       county, latitude, longitude, total_score, activity_score, claim_match_score,
                       data_quality_score, penalty_score, confidence_category, activity_label,
                       best_claim_serial, best_claim_name, best_match_type, best_distance_meters,
                       claim_count, claim_serials, blm_plan_present, blm_notice_present, msha_status,
                       score_breakdown_json, evidence_summary_json, recommended_next_action,
                       location_plss, township, range, section, meridian, plss_normalized,
                       plss_source, plss_status, area_of_focus_id, unpaid_claim_count,
                       paid_claim_count, unknown_claim_count, mlrs_claim_count,
                       claim_status_rollup, claims_fetched_at, run_id::text
                FROM active_mine_intel.candidate_sites
                WHERE account_id = :aid AND (id::text = :sid OR mine_site_id = :sid)
                LIMIT 1
                """
            ),
            {"aid": account_id, "sid": site_id},
        ).mappings().first()
        if not row:
            return None
        site = dict(row)
        matches = conn.execute(
            text(
                """
                SELECT claim_serial_number, claim_name, match_type, distance_meters,
                       total_score, activity_score, claim_match_score, confidence_category,
                       score_breakdown_json, evidence_summary_json
                FROM active_mine_intel.candidate_matches
                WHERE account_id = :aid AND run_id = :rid AND mine_site_id = :msid
                ORDER BY COALESCE(total_score, 0) DESC
                LIMIT 50
                """
            ),
            {
                "aid": account_id,
                "rid": site["run_id"],
                "msid": site["mine_site_id"],
            },
        ).mappings().all()
        site["matches"] = [dict(m) for m in matches]
        if site.get("area_of_focus_id"):
            target = conn.execute(
                text(
                    """
                    SELECT id, name, location_plss, plss_normalized, status, characteristics
                    FROM areas_of_focus WHERE id = :id AND account_id = :aid
                    """
                ),
                {"id": site["area_of_focus_id"], "aid": account_id},
            ).mappings().first()
            site["target"] = dict(target) if target else None
    return site


def linked_target_ids(
    account_id: int,
    *,
    state: str | None = None,
    site_ids: list[str] | None = None,
) -> list[int]:
    return [int(t["area_of_focus_id"]) for t in linked_targets_in_site_order(
        account_id, state=state, site_ids=site_ids
    )]


def linked_targets_in_site_order(
    account_id: int,
    *,
    state: str | None = None,
    site_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Unique linked Targets in Active Mines list order (score desc, then rank).

    First mine that references each Target supplies the display name used in
    fetch-job progress. Shared PLSS Targets appear once.
    """
    eng = get_engine()
    clauses = ["account_id = :aid", "area_of_focus_id IS NOT NULL"]
    params: dict[str, Any] = {"aid": account_id}
    if state:
        clauses.append("state_abbr = :st")
        params["st"] = state.upper()
    if site_ids:
        clauses.append("(id::text = ANY(:sids) OR mine_site_id = ANY(:sids))")
        params["sids"] = site_ids
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT area_of_focus_id, name, id::text AS site_id
                FROM active_mine_intel.candidate_sites
                WHERE {' AND '.join(clauses)}
                ORDER BY COALESCE(total_score, 0) DESC, rank ASC NULLS LAST, name ASC
                """
            ),
            params,
        ).mappings().all()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for r in rows:
        aid = r.get("area_of_focus_id")
        if aid is None:
            continue
        aid_i = int(aid)
        if aid_i in seen:
            continue
        seen.add(aid_i)
        out.append(
            {
                "area_of_focus_id": aid_i,
                "mine_name": (r.get("name") or "").strip() or f"Target #{aid_i}",
                "site_id": r.get("site_id"),
            }
        )
    return out


def fail_stale_fetch_jobs(
    account_id: int | None = None,
    *,
    reason: str = "Fetch job abandoned (API restart or superseded).",
) -> int:
    """Mark orphaned running/pending fetch jobs as failed. Returns rows updated."""
    eng = get_engine()
    clauses = ["status IN ('running', 'pending')"]
    params: dict[str, Any] = {"reason": reason}
    if account_id is not None:
        clauses.append("account_id = :aid")
        params["aid"] = account_id
    with eng.begin() as conn:
        result = conn.execute(
            text(
                f"""
                UPDATE active_mine_intel.fetch_jobs
                SET status = 'failed',
                    finished_at = COALESCE(finished_at, now()),
                    error_message = COALESCE(error_message, :reason),
                    updated_at = now()
                WHERE {' AND '.join(clauses)}
                """
            ),
            params,
        )
        return int(result.rowcount or 0)


def update_site_claim_rollup(
    account_id: int,
    area_of_focus_id: int,
    *,
    unpaid_count: int,
    rollup: str,
    mlrs_claim_count: int | None = None,
    paid_count: int = 0,
    unknown_count: int = 0,
) -> None:
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE active_mine_intel.candidate_sites
                SET unpaid_claim_count = :u,
                    paid_claim_count = :p,
                    unknown_claim_count = :unk,
                    claim_status_rollup = :r,
                    mlrs_claim_count = COALESCE(:mlrs, mlrs_claim_count),
                    claims_fetched_at = now(),
                    updated_at = now()
                WHERE account_id = :aid AND area_of_focus_id = :aof
                """
            ),
            {
                "aid": account_id,
                "aof": area_of_focus_id,
                "u": unpaid_count,
                "p": paid_count,
                "unk": unknown_count,
                "r": rollup,
                "mlrs": mlrs_claim_count,
            },
        )


def create_fetch_job(account_id: int, target_ids: list[int], state_abbr: str | None) -> str:
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO active_mine_intel.fetch_jobs
                  (account_id, state_abbr, status, target_ids)
                VALUES (:aid, :st, 'running', :ids)
                RETURNING id
                """
            ),
            {"aid": account_id, "st": state_abbr, "ids": target_ids},
        ).first()
    return str(row[0])


def update_fetch_job(job_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "finished_at",
        "processed",
        "succeeded",
        "failed",
        "results_json",
        "progress_json",
        "error_message",
    }
    sets = []
    params: dict[str, Any] = {"id": job_id}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("results_json", "progress_json") and not isinstance(v, str):
            v = json.dumps(v if v is not None else ({} if k == "progress_json" else []))
        sets.append(f"{k} = :{k}")
        params[k] = v
    if not sets:
        return
    sets.append("updated_at = now()")
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(f"UPDATE active_mine_intel.fetch_jobs SET {', '.join(sets)} WHERE id = :id"),
            params,
        )


def get_fetch_job(job_id: str, account_id: int | None = None) -> dict[str, Any] | None:
    eng = get_engine()
    clauses = ["id = :id"]
    params: dict[str, Any] = {"id": job_id}
    if account_id is not None:
        clauses.append("account_id = :aid")
        params["aid"] = account_id
    with eng.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT id::text, account_id, state_abbr, status, started_at, finished_at,
                       target_ids, processed, succeeded, failed, results_json, progress_json,
                       error_message
                FROM active_mine_intel.fetch_jobs
                WHERE {' AND '.join(clauses)}
                """
            ),
            params,
        ).mappings().first()
    if not row:
        return None
    out = dict(row)
    for key in ("results_json", "progress_json"):
        val = out.get(key)
        if isinstance(val, str):
            try:
                out[key] = json.loads(val)
            except (TypeError, ValueError, json.JSONDecodeError):
                out[key] = {} if key == "progress_json" else []
    return out
