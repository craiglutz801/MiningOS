"""Watchlist change detection for SITLA opportunities."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.sitla_intel.alerts")


def detect_watchlist_changes(account_id: int) -> dict[str, Any]:
    eng = get_engine()
    created = 0
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT o.id, o.best_title, o.reference_number
                FROM sitla_intel.watchlists w
                JOIN sitla_intel.opportunities o ON o.id = w.opportunity_id
                WHERE w.account_id = :aid AND o.is_active = true
                """
            ),
            {"aid": account_id},
        ).mappings().all()
        for row in rows:
            oid = str(row["id"])
            obs = conn.execute(
                text(
                    """
                    SELECT minimum_bid, normalized_status, observed_at
                    FROM sitla_intel.opportunity_observations
                    WHERE opportunity_id = CAST(:oid AS uuid)
                    ORDER BY observed_at DESC
                    LIMIT 2
                    """
                ),
                {"oid": oid},
            ).mappings().all()
            if len(obs) < 2:
                continue
            newest, prev = obs[0], obs[1]
            changes: dict[str, Any] = {}
            if (newest.get("minimum_bid") or None) != (prev.get("minimum_bid") or None):
                changes["minimum_bid"] = {
                    "from": float(prev["minimum_bid"]) if prev.get("minimum_bid") is not None else None,
                    "to": float(newest["minimum_bid"]) if newest.get("minimum_bid") is not None else None,
                }
            if (newest.get("normalized_status") or "") != (prev.get("normalized_status") or ""):
                changes["lifecycle"] = {
                    "from": prev.get("normalized_status"),
                    "to": newest.get("normalized_status"),
                }
            if not changes:
                continue
            dedupe = f"sitla:{account_id}:{oid}:{newest.get('observed_at')}:{sorted(changes.keys())}"
            if conn.execute(
                text("SELECT 1 FROM sitla_intel.alert_events WHERE dedupe_key = :d LIMIT 1"),
                {"d": dedupe},
            ).first():
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.alert_events (
                      opportunity_id, account_id, alert_type, severity, detected_at,
                      previous_value_json, new_value_json, delivery_status, dedupe_key
                    ) VALUES (
                      CAST(:oid AS uuid), :aid, 'WATCHLIST_CHANGE', 'info', :ts,
                      CAST(:prev AS jsonb), CAST(:new AS jsonb), 'pending', :dedupe
                    )
                    """
                ),
                {
                    "oid": oid,
                    "aid": account_id,
                    "ts": datetime.now(timezone.utc),
                    "prev": json.dumps(
                        {
                            "minimum_bid": float(prev["minimum_bid"]) if prev.get("minimum_bid") is not None else None,
                            "lifecycle": prev.get("normalized_status"),
                        }
                    ),
                    "new": json.dumps(changes),
                    "dedupe": dedupe,
                },
            )
            created += 1
    return {"ok": True, "alerts_created": created}


def deliver_pending_alerts(limit: int = 50) -> dict[str, Any]:
    from mining_os.config import settings
    from mining_os.services.email_alerts import send_alert

    eng = get_engine()
    sent = failed = skipped = 0
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT a.id, a.new_value_json, o.best_title, o.reference_number, o.county_name
                FROM sitla_intel.alert_events a
                LEFT JOIN sitla_intel.opportunities o ON o.id = a.opportunity_id
                WHERE a.delivery_status = 'pending'
                ORDER BY a.detected_at ASC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).mappings().all()
    to_email = getattr(settings, "ALERT_EMAIL", None) or ""
    for row in rows:
        alert_id = str(row["id"])
        if not to_email:
            with eng.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE sitla_intel.alert_events
                        SET delivery_status = 'skipped',
                            error_message = 'ALERT_EMAIL / SMTP not configured'
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": alert_id},
                )
            skipped += 1
            continue
        subject = f"[SITLA] Watchlist update: {row.get('best_title') or row.get('reference_number')}"
        body = f"County: {row.get('county_name')}\nChanges: {json.dumps(row.get('new_value_json'), default=str)}\n"
        ok, err = send_alert(str(to_email), subject, body)
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE sitla_intel.alert_events
                    SET delivery_status = :st, error_message = :err
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": alert_id,
                    "st": "sent" if ok else "failed",
                    "err": None if ok else (err or "send failed")[:2000],
                },
            )
        if ok:
            sent += 1
        else:
            failed += 1
    return {"ok": True, "sent": sent, "failed": failed, "skipped": skipped}
