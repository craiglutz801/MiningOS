"""
Background scheduler for automation rules.
Uses a single daemon thread that wakes every 60s and checks which cron rules are due.
Started once via start_scheduler() — safe to call multiple times (idempotent).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("mining_os.automation_scheduler")

_lock = threading.Lock()
_started = False
_thread: threading.Thread | None = None

CHECK_INTERVAL_SEC = 60


def _is_cron_due(cron_expr: str, now: datetime) -> bool:
    """Return True if cron_expr matches the current minute (truncated to minute)."""
    try:
        from croniter import croniter
        truncated = now.replace(second=0, microsecond=0)
        it = croniter(cron_expr, truncated - __import__("datetime").timedelta(seconds=61))
        nxt = it.get_next(datetime)
        return nxt <= truncated
    except Exception as e:
        log.debug("Cron parse failed for %r: %s", cron_expr, e)
        return False


def _tick() -> None:
    """One scheduler tick: find enabled rules with a cron schedule and run due ones."""
    try:
        from mining_os.services.automation_engine import list_rules, execute_rule

        now = datetime.now(timezone.utc)
        rules = list_rules()
        for rule in rules:
            if not rule.get("enabled"):
                continue
            cron = (rule.get("schedule_cron") or "").strip()
            if not cron:
                continue
            if _is_cron_due(cron, now):
                rule_id = rule["id"]
                log.info("Scheduler: cron matched for rule #%d (%s), executing", rule_id, rule.get("name"))
                try:
                    execute_rule(rule_id, trigger_type="scheduled")
                except Exception:
                    log.exception("Scheduler: rule #%d execution failed", rule_id)
    except Exception:
        log.exception("Scheduler tick failed")


def _run_loop() -> None:
    log.info("Automation scheduler thread started (interval=%ds)", CHECK_INTERVAL_SEC)
    while True:
        try:
            _tick()
        except Exception:
            log.exception("Scheduler loop error")
        time.sleep(CHECK_INTERVAL_SEC)


def start_scheduler() -> None:
    """Start the background scheduler thread (idempotent, daemon thread)."""
    global _started, _thread
    with _lock:
        if _started:
            return
        _started = True
        _thread = threading.Thread(target=_run_loop, daemon=True, name="automation-scheduler")
        _thread.start()
        log.info("Automation scheduler started")


def is_running() -> bool:
    return _started and _thread is not None and _thread.is_alive()
