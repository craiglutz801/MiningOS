"""Roll up Paid / Unpaid / Unknown from Target claim_records onto Active Mine rows."""

from __future__ import annotations

import json
from typing import Any


def _as_dict(val: Any) -> dict[str, Any] | None:
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


from mining_os.services.mlrs_payment_truth import summarize_claim_payments


def rollup_from_claims(claims: list[Any]) -> tuple[int, int, int, int, str]:
    """Return (mlrs_total, unpaid_count, paid_count, unknown_count, rollup_status).

    Paid/Unpaid counts require an approved evidence code. Legacy status strings
    without evidence are counted as unknown.
    """
    rows = [c for c in claims if isinstance(c, dict)]
    summary = summarize_claim_payments(rows)
    total = len(rows)
    paid = summary["paid_count"]
    unpaid = summary["unpaid_count"]
    unknown = total - paid - unpaid
    return total, unpaid, paid, unknown, summary["rollup"]


def rollup_from_characteristics(chars: Any) -> tuple[int, int, int, int, str] | None:
    """Rollup from Target characteristics, or None when claim_records were never fetched."""
    data = _as_dict(chars) or {}
    cr = _as_dict(data.get("claim_records"))
    if cr is None:
        return None
    claims = cr.get("claims")
    if not isinstance(claims, list):
        return None
    # fetched_at or a non-empty list means drilldown has the source of truth
    if cr.get("fetched_at") is None and len(claims) == 0 and not cr.get("ok"):
        return None
    return rollup_from_claims(claims)
