"""
Automation Engine — filter targets, run actions, log outcomes, optionally email.
"""
from __future__ import annotations

import json
import logging
import math
import threading
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
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
FETCH_CLAIM_RECORDS_INCLUDE_EXISTING_KEY = "include_targets_with_claim_status"


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

def _json_dumps(value: Any) -> str:
    return json.dumps(value)

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
            VALUES (:rule_id, :trigger_type, :status)
            RETURNING id
            """),
            {"rule_id": rule_id, "trigger_type": trigger_type, "status": RUN_STATUS_RUNNING},
        ).scalar()
        return int(row)


def _update_run_log(
    run_id: int,
    *,
    status: str | None = None,
    finished: bool = False,
    targets_total: int | None = None,
    targets_ok: int | None = None,
    targets_err: int | None = None,
    changes_found: int | None = None,
    email_sent: bool | None = None,
    error_message: str | None = None,
    results: list[dict[str, Any]] | None = None,
    summary: str | None = None,
) -> None:
    sets = []
    params: dict[str, Any] = {"id": run_id}

    if finished:
        sets.append("finished_at = now()")
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if targets_total is not None:
        sets.append("targets_total = :targets_total")
        params["targets_total"] = targets_total
    if targets_ok is not None:
        sets.append("targets_ok = :targets_ok")
        params["targets_ok"] = targets_ok
    if targets_err is not None:
        sets.append("targets_err = :targets_err")
        params["targets_err"] = targets_err
    if changes_found is not None:
        sets.append("changes_found = :changes_found")
        params["changes_found"] = changes_found
    if email_sent is not None:
        sets.append("email_sent = :email_sent")
        params["email_sent"] = email_sent
    if error_message is not None:
        sets.append("error_message = :error_message")
        params["error_message"] = error_message
    if results is not None:
        sets.append("results = CAST(:results AS jsonb)")
        params["results"] = _json_dumps(results)
    if summary is not None:
        sets.append("summary = :summary")
        params["summary"] = summary

    if not sets:
        return

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text(f"UPDATE automation_run_log SET {', '.join(sets)} WHERE id = :id"),
            params,
        )


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
    _update_run_log(
        run_id,
        finished=True,
        status=status,
        targets_total=targets_total,
        targets_ok=targets_ok,
        targets_err=targets_err,
        changes_found=changes_found,
        email_sent=email_sent,
        error_message=error_message,
        results=results,
        summary=summary,
    )


def _find_running_run(rule_id: int) -> dict[str, Any] | None:
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("""
            SELECT r.*, ar.name AS rule_name, ar.action_type
            FROM automation_run_log r
            LEFT JOIN automation_rules ar ON ar.id = r.rule_id
            WHERE r.rule_id = :rule_id AND r.status = :status
            ORDER BY r.started_at DESC
            LIMIT 1
            """),
            {"rule_id": rule_id, "status": RUN_STATUS_RUNNING},
        ).mappings().first()
        return _row_to_dict(row) if row else None


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
        from mining_os.services.fetch_claim_records import run_fetch_claim_records_for_area_id
        old_chars = area.get("characteristics") or {}
        old_count = len((old_chars.get("claim_records") or {}).get("claims") or [])
        res = run_fetch_claim_records_for_area_id(aid, account_id=account_id)
        claims = res.get("claims") or []
        new_count = len(claims) if isinstance(claims, list) else int(res.get("claims_count") or 0)
        changed = new_count != old_count
        return {
            "ok": bool(res.get("ok")),
            "changed": changed if res.get("ok") else False,
            "claims_count": new_count,
            "error": res.get("error"),
        }

    if action_type == "lr2000_report":
        from mining_os.services.mlrs_geographic_index import run_lr2000_geographic_index_for_area
        old_chars = area.get("characteristics") or {}
        old_count = len((old_chars.get("lr2000_geographic_index") or {}).get("claims") or [])
        res = run_lr2000_geographic_index_for_area(aid, area, account_id=account_id)
        claims = res.get("claims") or []
        new_count = len(claims) if isinstance(claims, list) else int(res.get("claims_count") or 0)
        changed = new_count != old_count
        return {
            "ok": bool(res.get("ok")),
            "changed": changed if res.get("ok") else False,
            "claims_count": new_count,
            "error": res.get("error"),
        }

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


def _claim_status_already_set(area: dict[str, Any]) -> bool:
    status = str(area.get("status") or "").strip().lower()
    return status in {"paid", "unpaid"}


def _should_skip_fetch_claim_records(area: dict[str, Any], filter_config: dict[str, Any]) -> bool:
    include_existing = bool(filter_config.get(FETCH_CLAIM_RECORDS_INCLUDE_EXISTING_KEY))
    return (not include_existing) and _claim_status_already_set(area)


def _build_skip_result(area: dict[str, Any]) -> dict[str, Any]:
    status = str(area.get("status") or "").strip().lower() or "unknown"
    return {
        "ok": True,
        "changed": False,
        "skipped": True,
        "skip_reason": (
            f"Skipped Fetch Claim Records because this target already has claim status '{status}'. "
            "Enable the include-existing-claim-status option on the rule to process it anyway."
        ),
    }


def _summary_line(
    *,
    action_type: str,
    total: int,
    processed: int,
    ok_count: int,
    err_count: int,
    skipped_count: int,
    changes: int,
) -> str:
    return (
        f"{ok_count} ok, {err_count} errors, {skipped_count} skipped, {changes} changes "
        f"({processed}/{total} handled, action={action_type})"
    )


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
    return _execute_rule_run(rule, run_id, trigger_type=trigger_type)


def _execute_rule_run(
    rule: dict[str, Any],
    run_id: int,
    *,
    trigger_type: str,
) -> dict[str, Any]:
    action_type = rule["action_type"]
    outcome_type = rule.get("outcome_type", "log_only")
    filter_config = rule.get("filter_config") or {}
    max_targets = rule.get("max_targets", 50)
    rule_account_id = int(rule["account_id"])

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
    skipped_count = 0
    total = len(areas)

    _update_run_log(
        run_id,
        targets_total=total,
        targets_ok=0,
        targets_err=0,
        changes_found=0,
        results=[],
        summary=_summary_line(
            action_type=action_type,
            total=total,
            processed=0,
            ok_count=0,
            err_count=0,
            skipped_count=0,
            changes=0,
        ),
    )

    for i, area in enumerate(areas):
        if i > 0 and PAUSE_BETWEEN_TARGETS_SEC > 0:
            time.sleep(PAUSE_BETWEEN_TARGETS_SEC)

        aid = area["id"]
        name = area.get("name") or f"#{aid}"
        if action_type == "fetch_claim_records" and _should_skip_fetch_claim_records(area, filter_config):
            res = _build_skip_result(area)
        else:
            try:
                res = _run_action_on_target(action_type, area, account_id=rule_account_id)
            except Exception as e:
                res = {"ok": False, "error": str(e)}

        row = {"id": aid, "name": name, **res}
        results.append(row)

        if res.get("skipped"):
            skipped_count += 1
        elif res.get("ok"):
            ok_count += 1
        else:
            err_count += 1
        if res.get("changed"):
            changes += 1

        _update_run_log(
            run_id,
            targets_total=total,
            targets_ok=ok_count,
            targets_err=err_count,
            changes_found=changes,
            results=results,
            summary=_summary_line(
                action_type=action_type,
                total=total,
                processed=len(results),
                ok_count=ok_count,
                err_count=err_count,
                skipped_count=skipped_count,
                changes=changes,
            ),
        )

    email_sent = False
    if outcome_type == "email_always":
        email_sent = _send_outcome_email(rule, results, changes, ok_count, err_count)
    elif outcome_type == "email_on_change" and changes > 0:
        email_sent = _send_outcome_email(rule, results, changes, ok_count, err_count)
    elif outcome_type == "email_on_error" and err_count > 0:
        email_sent = _send_outcome_email(rule, results, changes, ok_count, err_count)

    summary = _summary_line(
        action_type=action_type,
        total=total,
        processed=len(results),
        ok_count=ok_count,
        err_count=err_count,
        skipped_count=skipped_count,
        changes=changes,
    )

    _finish_run_log(
        run_id,
        status=RUN_STATUS_COMPLETED,
        targets_total=total,
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
        "targets_total": total,
        "targets_ok": ok_count,
        "targets_err": err_count,
        "changes_found": changes,
        "email_sent": email_sent,
        "summary": summary,
    }


def queue_rule_run(
    rule_id: int,
    trigger_type: str = "manual",
    account_id: int | None = None,
) -> dict[str, Any]:
    """Start one automation rule on a background thread and return immediately."""
    rule = get_rule(rule_id, account_id=account_id)
    if not rule:
        return {"ok": False, "error": "Rule not found"}
    if not rule.get("enabled") and trigger_type == "scheduled":
        return {"ok": False, "error": "Rule is disabled"}

    existing = _find_running_run(rule_id)
    if existing:
        return {
            "ok": True,
            "run_id": existing["id"],
            "status": RUN_STATUS_RUNNING,
            "message": "This rule is already running.",
            "already_running": True,
        }

    run_id = _create_run_log(rule_id, trigger_type)

    def _worker() -> None:
        try:
            _execute_rule_run(rule, run_id, trigger_type=trigger_type)
        except Exception as e:  # pragma: no cover - defensive background path
            log.exception("Automation run #%d crashed", run_id)
            _finish_run_log(
                run_id,
                status=RUN_STATUS_FAILED,
                targets_total=0,
                targets_ok=0,
                targets_err=1,
                changes_found=0,
                email_sent=False,
                error_message=str(e),
                results=[],
                summary=f"Automation run crashed: {e}",
            )

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"automation-run-{run_id}",
    ).start()

    return {
        "ok": True,
        "run_id": run_id,
        "status": RUN_STATUS_RUNNING,
        "message": "Automation run started in the background.",
        "already_running": False,
    }
