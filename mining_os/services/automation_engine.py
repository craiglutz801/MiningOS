"""
Automation Engine — filter targets, run actions, log outcomes, optionally email.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from mining_os.config import settings
from mining_os.db import get_engine
from mining_os.services.auth import current_account_id, get_auth_context

log = logging.getLogger("mining_os.automation_engine")

ACTION_TYPES = [
    "fetch_claim_records",
    "lr2000_report",
    "check_blm_status",
    "generate_report",
]

OUTCOME_TYPES = [
    "log_only",
    "email_always",
    "email_on_change",
    "email_on_error",
]

FILTER_KEYS = [
    "priority",
    "mineral",
    "status",
    "state_abbr",
    "claim_type",
    "name",
    "township",
    "range_val",
    "sector",
]

MAX_TARGETS_CAP = 200
PAUSE_BETWEEN_TARGETS_SEC = 0.3


def _effective_account_id(account_id: int | None = None) -> int:
    return int(account_id or current_account_id())


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def _ensure_tables() -> None:
    """Create the automation tables if they don't exist yet (idempotent)."""
    from pathlib import Path
    sql_path = Path(__file__).resolve().parents[1] / "sql" / "017_automation_engine.sql"
    if not sql_path.exists():
        return
    eng = get_engine()
    with eng.begin() as conn:
        exists = conn.execute(
            text("SELECT to_regclass('public.automation_rules')")
        ).scalar()
        if exists:
            return
    from mining_os.db import exec_sql
    exec_sql(sql_path.read_text())
    log.info("Created automation_rules + automation_run_log tables")


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for k in ("created_at", "updated_at", "started_at", "finished_at"):
        v = d.get(k)
        if v and hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    fc = d.get("filter_config")
    if isinstance(fc, str):
        try:
            d["filter_config"] = json.loads(fc)
        except (json.JSONDecodeError, TypeError):
            pass
    res = d.get("results")
    if isinstance(res, str):
        try:
            d["results"] = json.loads(res)
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def list_rules(account_id: int | None = None, *, all_accounts: bool = False) -> list[dict[str, Any]]:
    _ensure_tables()
    ctx = get_auth_context()
    scoped_account_id = int(account_id) if account_id is not None else (ctx.active_account_id if ctx else None)
    eng = get_engine()
    with eng.begin() as conn:
        if all_accounts or scoped_account_id is None:
            rows = conn.execute(
                text("SELECT * FROM automation_rules ORDER BY created_at DESC")
            ).mappings().all()
        else:
            rows = conn.execute(
                text("SELECT * FROM automation_rules WHERE account_id = :account_id ORDER BY created_at DESC"),
                {"account_id": scoped_account_id},
            ).mappings().all()
        return [_row_to_dict(r) for r in rows]


def get_rule(rule_id: int, account_id: int | None = None) -> dict[str, Any] | None:
    _ensure_tables()
    ctx = get_auth_context()
    scoped_account_id = int(account_id) if account_id is not None else (ctx.active_account_id if ctx else None)
    eng = get_engine()
    with eng.begin() as conn:
        if scoped_account_id is None:
            row = conn.execute(
                text("SELECT * FROM automation_rules WHERE id = :id"),
                {"id": rule_id},
            ).mappings().first()
        else:
            row = conn.execute(
                text("SELECT * FROM automation_rules WHERE id = :id AND account_id = :account_id"),
                {"id": rule_id, "account_id": scoped_account_id},
            ).mappings().first()
        return _row_to_dict(row) if row else None


def create_rule(
    name: str,
    action_type: str,
    filter_config: dict | None = None,
    outcome_type: str = "log_only",
    schedule_cron: str | None = None,
    max_targets: int = 50,
    enabled: bool = True,
    account_id: int | None = None,
) -> dict[str, Any]:
    _ensure_tables()
    account_id = _effective_account_id(account_id)
    if action_type not in ACTION_TYPES:
        raise ValueError(f"action_type must be one of {ACTION_TYPES}")
    if outcome_type not in OUTCOME_TYPES:
        raise ValueError(f"outcome_type must be one of {OUTCOME_TYPES}")
    max_targets = min(max(1, max_targets), MAX_TARGETS_CAP)
    fc = json.dumps(filter_config or {})
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("""
            INSERT INTO automation_rules
                (account_id, name, enabled, filter_config, action_type, outcome_type, schedule_cron, max_targets)
            VALUES
                (:account_id, :name, :enabled, CAST(:fc AS jsonb), :action_type, :outcome_type, :schedule_cron, :max_targets)
            RETURNING *
            """),
            {
                "account_id": account_id,
                "name": name.strip(),
                "enabled": enabled,
                "fc": fc,
                "action_type": action_type,
                "outcome_type": outcome_type,
                "schedule_cron": (schedule_cron or "").strip() or None,
                "max_targets": max_targets,
            },
        ).mappings().first()
        return _row_to_dict(row) if row else {}


def update_rule(rule_id: int, account_id: int | None = None, **kwargs: Any) -> dict[str, Any] | None:
    _ensure_tables()
    account_id = _effective_account_id(account_id)
    allowed = {
        "name", "enabled", "filter_config", "action_type",
        "outcome_type", "schedule_cron", "max_targets",
    }
    sets = []
    params: dict[str, Any] = {"id": rule_id, "account_id": account_id}
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "filter_config":
            sets.append("filter_config = CAST(:fc AS jsonb)")
            params["fc"] = json.dumps(v if isinstance(v, dict) else {})
        elif k == "max_targets":
            sets.append(f"{k} = :{k}")
            params[k] = min(max(1, int(v)), MAX_TARGETS_CAP)
        elif k == "action_type":
            if v not in ACTION_TYPES:
                raise ValueError(f"action_type must be one of {ACTION_TYPES}")
            sets.append(f"{k} = :{k}")
            params[k] = v
        elif k == "outcome_type":
            if v not in OUTCOME_TYPES:
                raise ValueError(f"outcome_type must be one of {OUTCOME_TYPES}")
            sets.append(f"{k} = :{k}")
            params[k] = v
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    if not sets:
        return get_rule(rule_id)
    sets.append("updated_at = now()")
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(f"UPDATE automation_rules SET {', '.join(sets)} WHERE id = :id AND account_id = :account_id RETURNING *"),
            params,
        ).mappings().first()
        return _row_to_dict(row) if row else None


def delete_rule(rule_id: int, account_id: int | None = None) -> bool:
    _ensure_tables()
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("DELETE FROM automation_rules WHERE id = :id AND account_id = :account_id"),
            {"id": rule_id, "account_id": account_id},
        )
        return (r.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# Run log helpers
# ---------------------------------------------------------------------------

def list_runs(
    rule_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    account_id: int | None = None,
) -> list[dict[str, Any]]:
    _ensure_tables()
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    where = "WHERE ar.account_id = :account_id"
    params: dict[str, Any] = {"account_id": account_id, "limit": min(limit, 500), "offset": max(offset, 0)}
    if rule_id:
        where += " AND r.rule_id = :rule_id"
        params["rule_id"] = rule_id
    with eng.begin() as conn:
        rows = conn.execute(
            text(f"""
            SELECT r.*, ar.name AS rule_name, ar.action_type
            FROM automation_run_log r
            LEFT JOIN automation_rules ar ON ar.id = r.rule_id
            {where}
            ORDER BY r.started_at DESC
            LIMIT :limit OFFSET :offset
            """),
            params,
        ).mappings().all()
        return [_row_to_dict(r) for r in rows]


def get_run(run_id: int, account_id: int | None = None) -> dict[str, Any] | None:
    _ensure_tables()
    account_id = _effective_account_id(account_id)
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("""
            SELECT r.*, ar.name AS rule_name, ar.action_type
            FROM automation_run_log r
            LEFT JOIN automation_rules ar ON ar.id = r.rule_id
            WHERE r.id = :id AND ar.account_id = :account_id
            """),
            {"id": run_id, "account_id": account_id},
        ).mappings().first()
        return _row_to_dict(row) if row else None


def _create_run_log(rule_id: int, trigger_type: str) -> int:
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("""
            INSERT INTO automation_run_log (rule_id, trigger_type, status)
            VALUES (:rule_id, :trigger_type, 'running')
            RETURNING id
            """),
            {"rule_id": rule_id, "trigger_type": trigger_type},
        ).scalar()
        return int(row)


def _finish_run_log(
    run_id: int,
    *,
    status: str,
    targets_total: int,
    targets_ok: int,
    targets_err: int,
    changes_found: int,
    email_sent: bool,
    error_message: str | None,
    results: list[dict],
    summary: str | None,
) -> None:
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text("""
            UPDATE automation_run_log SET
                finished_at = now(),
                status = :status,
                targets_total = :targets_total,
                targets_ok = :targets_ok,
                targets_err = :targets_err,
                changes_found = :changes_found,
                email_sent = :email_sent,
                error_message = :error_message,
                results = CAST(:results AS jsonb),
                summary = :summary
            WHERE id = :id
            """),
            {
                "id": run_id,
                "status": status,
                "targets_total": targets_total,
                "targets_ok": targets_ok,
                "targets_err": targets_err,
                "changes_found": changes_found,
                "email_sent": email_sent,
                "error_message": error_message,
                "results": json.dumps(results),
                "summary": summary,
            },
        )


# ---------------------------------------------------------------------------
# Action executors
# ---------------------------------------------------------------------------

def _run_action_on_target(
    action_type: str,
    area: dict[str, Any],
    *,
    account_id: int | None = None,
) -> dict[str, Any]:
    """Run one action on one target. Returns {ok, changed, claims_count?, status?, error?}."""
    aid = area["id"]
    name = area.get("name") or ""

    if action_type == "fetch_claim_records":
        from mining_os.services.fetch_claim_records import fetch_claim_records_for_area
        old_chars = area.get("characteristics") or {}
        old_count = len((old_chars.get("claim_records") or {}).get("claims") or [])
        try:
            res = fetch_claim_records_for_area(
                aid,
                name,
                area.get("location_plss"),
                account_id=account_id,
                state_abbr=area.get("state_abbr"),
                meridian=area.get("meridian"),
                township=area.get("township"),
                range_val=area.get("range"),
                section=area.get("section"),
                latitude=area.get("latitude"),
                longitude=area.get("longitude"),
            )
            new_count = res.get("claims_count", 0)
            changed = new_count != old_count
            return {"ok": True, "changed": changed, "claims_count": new_count}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if action_type == "lr2000_report":
        from mining_os.services.mlrs_geographic_index import run_lr2000_geographic_index_for_area
        old_chars = area.get("characteristics") or {}
        old_count = len((old_chars.get("lr2000_geographic_index") or {}).get("claims") or [])
        try:
            res = run_lr2000_geographic_index_for_area(aid, area, account_id=account_id)
            new_count = res.get("claims_count", 0)
            changed = new_count != old_count
            return {"ok": True, "changed": changed, "claims_count": new_count}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if action_type == "check_blm_status":
        from mining_os.services.blm_check import check_area_by_coords
        lat = area.get("latitude")
        lon = area.get("longitude")
        if not (lat and lon and math.isfinite(float(lat)) and math.isfinite(float(lon))):
            return {"ok": False, "error": "No coordinates for BLM check"}
        old_status = (area.get("status") or "unknown").lower()
        try:
            res = check_area_by_coords(aid, float(lat), float(lon))
            if not res:
                return {"ok": True, "changed": False, "status": old_status}
            new_status = (res.get("status") or "unknown").lower()
            changed = new_status != old_status
            return {"ok": True, "changed": changed, "status": new_status, "old_status": old_status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if action_type == "generate_report":
        from mining_os.services.areas_of_focus import get_area
        try:
            full = get_area(aid, account_id=account_id)
            if not full:
                return {"ok": False, "error": "Target not found"}
            return {"ok": True, "changed": False, "note": "Report generation logged"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Unknown action_type: {action_type}"}


# ---------------------------------------------------------------------------
# Filter targets
# ---------------------------------------------------------------------------

def _filter_targets(
    filter_config: dict[str, Any],
    max_targets: int,
    *,
    account_id: int | None = None,
) -> list[dict[str, Any]]:
    from mining_os.services.areas_of_focus import list_areas

    params: dict[str, Any] = {}
    for k in FILTER_KEYS:
        v = filter_config.get(k)
        if v and str(v).strip():
            params[k] = str(v).strip()

    # priority filter: map to list_areas style
    priority = filter_config.get("priority")
    if priority:
        params.pop("priority", None)

    limit = min(max_targets, MAX_TARGETS_CAP)
    areas = list_areas(
        mineral=params.get("mineral"),
        status=params.get("status"),
        state_abbr=params.get("state_abbr"),
        claim_type=params.get("claim_type"),
        township=params.get("township"),
        range_val=params.get("range_val"),
        sector=params.get("sector"),
        name=params.get("name"),
        limit=limit * 5,
        account_id=account_id,
    )

    if priority:
        p = priority.strip().lower()
        areas = [a for a in areas if (a.get("priority") or "").lower() == p]

    return areas[:limit]


# ---------------------------------------------------------------------------
# Email outcome
# ---------------------------------------------------------------------------

def _send_outcome_email(
    rule: dict[str, Any],
    run_results: list[dict[str, Any]],
    changes_found: int,
    targets_ok: int,
    targets_err: int,
) -> bool:
    from mining_os.services.email_alerts import send_alert

    to = settings.ALERT_EMAIL
    if not to:
        log.warning("No ALERT_EMAIL configured; skipping outcome email")
        return False

    rule_name = rule.get("name", "Automation")
    action = rule.get("action_type", "unknown")
    total = len(run_results)

    subject = f"[Mining OS] Automation: {rule_name}"
    if changes_found:
        subject += f" — {changes_found} change(s) detected"

    lines = [
        f"Automation rule: {rule_name}",
        f"Action: {action}",
        f"Targets processed: {total}",
        f"  Succeeded: {targets_ok}",
        f"  Failed: {targets_err}",
        f"  Changes detected: {changes_found}",
        "",
    ]

    if changes_found:
        lines.append("Targets with changes:")
        for r in run_results:
            if r.get("changed"):
                nm = r.get("name") or f"#{r.get('id')}"
                detail = ""
                if r.get("old_status") and r.get("status"):
                    detail = f" (status: {r['old_status']} → {r['status']})"
                elif r.get("claims_count") is not None:
                    detail = f" ({r['claims_count']} claims)"
                lines.append(f"  - {nm}{detail}")

    if targets_err:
        lines.append("")
        lines.append("Errors:")
        for r in run_results:
            if r.get("error"):
                nm = r.get("name") or f"#{r.get('id')}"
                lines.append(f"  - {nm}: {r['error']}")

    body = "\n".join(lines)
    ok, err = send_alert(to, subject, body)
    if not ok:
        log.warning("Outcome email failed: %s", err)
    return ok


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

def execute_rule(
    rule_id: int,
    trigger_type: str = "manual",
    account_id: int | None = None,
) -> dict[str, Any]:
    """Run one automation rule now. Returns the run log summary."""
    rule = get_rule(rule_id, account_id=account_id)
    if not rule:
        return {"ok": False, "error": "Rule not found"}
    if not rule.get("enabled") and trigger_type == "scheduled":
        return {"ok": False, "error": "Rule is disabled"}

    action_type = rule["action_type"]
    outcome_type = rule.get("outcome_type", "log_only")
    filter_config = rule.get("filter_config") or {}
    max_targets = rule.get("max_targets", 50)
    rule_account_id = int(rule["account_id"])

    run_id = _create_run_log(rule_id, trigger_type)
    log.info(
        "Automation run #%d started: rule=%s action=%s trigger=%s",
        run_id, rule.get("name"), action_type, trigger_type,
    )

    try:
        areas = _filter_targets(filter_config, max_targets, account_id=rule_account_id)
    except Exception as e:
        log.exception("Automation run #%d filter failed", run_id)
        _finish_run_log(
            run_id,
            status="failed",
            targets_total=0, targets_ok=0, targets_err=0, changes_found=0,
            email_sent=False, error_message=str(e), results=[], summary=f"Filter failed: {e}",
        )
        return {"ok": False, "run_id": run_id, "error": str(e)}

    results: list[dict[str, Any]] = []
    ok_count = 0
    err_count = 0
    changes = 0

    for i, area in enumerate(areas):
        if i > 0 and PAUSE_BETWEEN_TARGETS_SEC > 0:
            time.sleep(PAUSE_BETWEEN_TARGETS_SEC)

        aid = area["id"]
        name = area.get("name") or f"#{aid}"
        try:
            res = _run_action_on_target(action_type, area, account_id=rule_account_id)
        except Exception as e:
            res = {"ok": False, "error": str(e)}

        row = {"id": aid, "name": name, **res}
        results.append(row)

        if res.get("ok"):
            ok_count += 1
        else:
            err_count += 1
        if res.get("changed"):
            changes += 1

    email_sent = False
    if outcome_type == "email_always":
        email_sent = _send_outcome_email(rule, results, changes, ok_count, err_count)
    elif outcome_type == "email_on_change" and changes > 0:
        email_sent = _send_outcome_email(rule, results, changes, ok_count, err_count)
    elif outcome_type == "email_on_error" and err_count > 0:
        email_sent = _send_outcome_email(rule, results, changes, ok_count, err_count)

    summary = (
        f"{ok_count} ok, {err_count} errors, {changes} changes"
        f" ({len(areas)} targets, action={action_type})"
    )

    _finish_run_log(
        run_id,
        status="completed",
        targets_total=len(areas),
        targets_ok=ok_count,
        targets_err=err_count,
        changes_found=changes,
        email_sent=email_sent,
        error_message=None,
        results=results,
        summary=summary,
    )

    log.info("Automation run #%d completed: %s", run_id, summary)

    return {
        "ok": True,
        "run_id": run_id,
        "targets_total": len(areas),
        "targets_ok": ok_count,
        "targets_err": err_count,
        "changes_found": changes,
        "email_sent": email_sent,
        "summary": summary,
    }
