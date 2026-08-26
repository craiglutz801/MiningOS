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


from mining_os.services.mlrs_payment_truth import rollup_payment_status


def rollup_from_claims(claims: list[Any]) -> tuple[int, int, int, int, str]:
    """Return (mlrs_total, unpaid_count, paid_count, unknown_count, rollup_status)."""
    unpaid = paid = unknown = 0
    statuses: list[str] = []
    total = 0
    for c in claims:
        if not isinstance(c, dict):
            continue
        total += 1
        st = (c.get("payment_status") or "unknown")
        if not isinstance(st, str):
            st = str(st or "unknown")
        st = st.strip().lower() or "unknown"
        statuses.append(st)
        if st == "unpaid":
            unpaid += 1
        elif st == "paid":
            paid += 1
        else:
            unknown += 1
    rollup = rollup_payment_status(statuses) if statuses else "unknown"
    return total, unpaid, paid, unknown, rollup


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
