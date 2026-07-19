"""Watchlist change detection and alert delivery for Tax Sales."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine

log = logging.getLogger("mining_os.tax_intel.alerts")


def detect_watchlist_changes(account_id: int) -> dict[str, Any]:
    """
    Create alert_events when watchlisted opportunities change amount/lifecycle
    since the previous observation.
    """
    eng = get_engine()
    created = 0
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT o.id, o.best_name, o.primary_apn, o.state, o.county_name,
                       o.sale_lifecycle_status, o.amount_due, o.auction_start_at,
                       w.user_id
                FROM tax_intel.watchlists w
                JOIN tax_intel.tax_opportunities o ON o.id = w.opportunity_id
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
                    SELECT amount_due, normalized_status, observed_at
                    FROM tax_intel.tax_observations
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
            if (newest.get("amount_due") or None) != (prev.get("amount_due") or None):
                changes["amount_due"] = {
                    "from": float(prev["amount_due"]) if prev.get("amount_due") is not None else None,
                    "to": float(newest["amount_due"]) if newest.get("amount_due") is not None else None,
                }
            if (newest.get("normalized_status") or "") != (prev.get("normalized_status") or ""):
                changes["lifecycle"] = {
                    "from": prev.get("normalized_status"),
                    "to": newest.get("normalized_status"),
                }
            if not changes:
                continue
            dedupe = f"{account_id}:{oid}:{newest.get('observed_at')}:{sorted(changes.keys())}"
            exists = conn.execute(
                text("SELECT 1 FROM tax_intel.alert_events WHERE dedupe_key = :d LIMIT 1"),
                {"d": dedupe},
            ).first()
            if exists:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.alert_events (
                      opportunity_id, account_id, alert_type, severity, detected_at,
                      previous_value_json, new_value_json, delivery_status,
                      delivery_channels_json, dedupe_key
                    ) VALUES (
                      CAST(:oid AS uuid), :aid, 'WATCHLIST_CHANGE', 'info', :ts,
                      CAST(:prev AS jsonb), CAST(:new AS jsonb), 'pending',
                      CAST(:ch AS jsonb), :dedupe
                    )
                    """
                ),
                {
                    "oid": oid,
                    "aid": account_id,
                    "ts": datetime.now(timezone.utc),
                    "prev": json.dumps(
                        {
                            "amount_due": float(prev["amount_due"]) if prev.get("amount_due") is not None else None,
                            "lifecycle": prev.get("normalized_status"),
                        }
                    ),
                    "new": json.dumps(changes),
                    "ch": json.dumps(["email", "in_app"]),
                    "dedupe": dedupe,
                },
            )
            created += 1
    return {"ok": True, "alerts_created": created}


def deliver_pending_alerts(limit: int = 50) -> dict[str, Any]:
    """Attempt SMTP delivery for pending alerts; always safe if SMTP missing."""
    from mining_os.config import settings
    from mining_os.services.email_alerts import send_alert

    eng = get_engine()
    sent = failed = skipped = 0
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT a.id, a.opportunity_id, a.account_id, a.alert_type,
                       a.new_value_json, a.previous_value_json,
                       o.best_name, o.state, o.county_name, o.primary_apn
                FROM tax_intel.alert_events a
                LEFT JOIN tax_intel.tax_opportunities o ON o.id = a.opportunity_id
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
                        UPDATE tax_intel.alert_events
                        SET delivery_status = 'skipped',
                            error_message = 'ALERT_EMAIL / SMTP not configured'
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": alert_id},
                )
            skipped += 1
            continue
        subject = (
            f"[Tax Sales] Watchlist update: {row.get('best_name') or row.get('primary_apn') or 'opportunity'}"
        )
        body = (
            f"State/County: {row.get('state')} / {row.get('county_name')}\n"
            f"APN: {row.get('primary_apn')}\n"
            f"Changes: {json.dumps(row.get('new_value_json'), default=str)}\n"
            f"Previous: {json.dumps(row.get('previous_value_json'), default=str)}\n"
        )
        ok, err = send_alert(str(to_email), subject, body)
        with eng.begin() as conn:
            if ok:
                conn.execute(
                    text(
                        """
                        UPDATE tax_intel.alert_events
                        SET delivery_status = 'sent', error_message = NULL
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": alert_id},
                )
                sent += 1
            else:
                conn.execute(
                    text(
                        """
                        UPDATE tax_intel.alert_events
                        SET delivery_status = 'failed', error_message = :err
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": alert_id, "err": (err or "send failed")[:2000]},
                )
                failed += 1
    return {"ok": True, "sent": sent, "failed": failed, "skipped": skipped, "considered": len(rows)}


def list_alerts(account_id: int, limit: int = 50) -> dict[str, Any]:
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT a.id, a.alert_type, a.severity, a.detected_at, a.delivery_status,
                       a.new_value_json, a.previous_value_json,
                       o.best_name, o.primary_apn, o.state, o.county_name, o.id AS opportunity_id
                FROM tax_intel.alert_events a
                LEFT JOIN tax_intel.tax_opportunities o ON o.id = a.opportunity_id
                WHERE a.account_id = :aid
                ORDER BY a.detected_at DESC
                LIMIT :lim
                """
            ),
            {"aid": account_id, "lim": limit},
        ).mappings().all()
    items = []
    for r in rows:
        d = dict(r)
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        items.append(d)
    return {"ok": True, "items": items, "total": len(items)}
