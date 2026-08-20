#!/usr/bin/env python3
"""
Re-check MLRS payment status for stored claim_records that are still ``unknown``.

Does **not** change claims already marked paid/unpaid. Unpaid detection still requires
the maintenance-fee phrase. Paid requires a loaded case page with no unpaid banner.

Usage (project root):
  .venv/bin/python -m scripts.reenrich_unknown_claim_payments
  .venv/bin/python -m scripts.reenrich_unknown_claim_payments --area-id 6880
  .venv/bin/python -m scripts.reenrich_unknown_claim_payments --active-mines-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow `python scripts/reenrich_...py` as well as `-m`
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("reenrich_unknown_payments")


def _chars(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _needs_payment_recheck(claim: dict[str, Any]) -> bool:
    st = (claim.get("payment_status") or "unknown").strip().lower()
    return st not in {"paid", "unpaid"}


def reenrich_area(area_id: int, account_id: int | None = None) -> dict[str, Any]:
    from mining_os.active_mine_intel.jobs import _rollup_from_characteristics
    from mining_os.active_mine_intel import store as ami_store
    from mining_os.services.areas_of_focus import get_area, merge_area_characteristics
    from mining_os.services.mlrs_case_payment import enrich_claims_from_mlrs_case_pages

    area = get_area(area_id, account_id=account_id) if account_id is not None else get_area(area_id)
    if not area:
        return {"ok": False, "area_id": area_id, "error": "not found"}

    aid = int(account_id if account_id is not None else area.get("account_id") or 0)
    chars = _chars(area.get("characteristics"))
    cr = chars.get("claim_records")
    if not isinstance(cr, dict):
        return {"ok": True, "area_id": area_id, "skipped": True, "reason": "no claim_records"}

    claims = list(cr.get("claims") or [])
    if not claims:
        return {"ok": True, "area_id": area_id, "skipped": True, "reason": "empty claims"}

    pending = [c for c in claims if isinstance(c, dict) and _needs_payment_recheck(c)]
    if not pending:
        return {"ok": True, "area_id": area_id, "skipped": True, "reason": "no unknowns"}

    log.info("area %s: re-checking %d/%d unknown claim(s)", area_id, len(pending), len(claims))
    enriched_pending = enrich_claims_from_mlrs_case_pages(pending)

    # Map by serial/case back onto original list; never overwrite paid/unpaid.
    by_key: dict[str, dict[str, Any]] = {}
    for c in enriched_pending:
        sn = str(c.get("serial_number") or c.get("CSE_NR") or "").strip().upper()
        case = str(c.get("case_page") or "").strip()
        if sn:
            by_key[f"s:{sn}"] = c
        if case:
            by_key[f"c:{case}"] = c

    changed = 0
    paid = unpaid = unknown = 0
    new_claims: list[dict[str, Any]] = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        st0 = (c.get("payment_status") or "unknown").strip().lower()
        if st0 in {"paid", "unpaid"}:
            new_claims.append(c)
            if st0 == "paid":
                paid += 1
            else:
                unpaid += 1
            continue
        sn = str(c.get("serial_number") or c.get("CSE_NR") or "").strip().upper()
        case = str(c.get("case_page") or "").strip()
        upd = by_key.get(f"s:{sn}") or by_key.get(f"c:{case}")
        if not upd:
            new_claims.append(c)
            unknown += 1
            continue
        st1 = (upd.get("payment_status") or "unknown").strip().lower()
        merged = dict(c)
        for k in (
            "payment_status",
            "payment_message",
            "payment_check_source",
            "payment_check_error",
            "payment_checked_at",
        ):
            if k in upd and upd[k] is not None:
                merged[k] = upd[k]
        if st1 in {"paid", "unpaid"} and st1 != st0:
            changed += 1
        new_claims.append(merged)
        if st1 == "paid":
            paid += 1
        elif st1 == "unpaid":
            unpaid += 1
        else:
            unknown += 1

    payload = {
        **cr,
        "claims": new_claims,
        "payment_reenriched_at": datetime.now(timezone.utc).isoformat(),
        "ok": cr.get("ok", True),
    }
    merge_area_characteristics(area_id, {"claim_records": payload}, account_id=aid or None)

    # Refresh Active Mine Search list columns for linked mines.
    if aid:
        total, unpaid_n, paid_n, unknown_n, rollup = _rollup_from_characteristics({"claim_records": payload})
        ami_store.update_site_claim_rollup(
            aid,
            area_id,
            unpaid_count=unpaid_n,
            paid_count=paid_n,
            unknown_count=unknown_n,
            rollup=rollup,
            mlrs_claim_count=total,
        )

    return {
        "ok": True,
        "area_id": area_id,
        "changed": changed,
        "paid": paid,
        "unpaid": unpaid,
        "unknown": unknown,
        "checked": len(pending),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area-id", type=int, action="append", default=[])
    parser.add_argument(
        "--active-mines-only",
        action="store_true",
        help="Only Targets linked from active_mine_intel.candidate_sites",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max targets to process (0=all)")
    args = parser.parse_args()

    from mining_os.db import get_engine
    from sqlalchemy import text

    eng = get_engine()
    area_ids: list[tuple[int, int]] = []  # (area_id, account_id)

    with eng.connect() as conn:
        if args.area_id:
            for aid in args.area_id:
                row = conn.execute(
                    text("SELECT id, account_id FROM areas_of_focus WHERE id = :id"),
                    {"id": aid},
                ).first()
                if row:
                    area_ids.append((int(row[0]), int(row[1])))
        elif args.active_mines_only:
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT s.area_of_focus_id, s.account_id
                    FROM active_mine_intel.candidate_sites s
                    JOIN areas_of_focus a ON a.id = s.area_of_focus_id
                    WHERE s.area_of_focus_id IS NOT NULL
                      AND a.characteristics ? 'claim_records'
                    ORDER BY 1
                    """
                )
            ).fetchall()
            area_ids = [(int(r[0]), int(r[1])) for r in rows]
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT id, account_id FROM areas_of_focus
                    WHERE characteristics ? 'claim_records'
                    ORDER BY id
                    """
                )
            ).fetchall()
            area_ids = [(int(r[0]), int(r[1])) for r in rows]

    if args.limit and args.limit > 0:
        area_ids = area_ids[: args.limit]

    log.info("Processing %d target(s)", len(area_ids))
    totals = {"changed": 0, "paid": 0, "unpaid": 0, "unknown": 0, "checked": 0}
    for area_id, account_id in area_ids:
        try:
            res = reenrich_area(area_id, account_id=account_id)
        except Exception as e:
            log.exception("area %s failed: %s", area_id, e)
            continue
        if res.get("skipped"):
            log.info("area %s skipped (%s)", area_id, res.get("reason"))
            continue
        log.info(
            "area %s: checked=%s changed=%s paid=%s unpaid=%s unknown=%s",
            area_id,
            res.get("checked"),
            res.get("changed"),
            res.get("paid"),
            res.get("unpaid"),
            res.get("unknown"),
        )
        for k in totals:
            totals[k] += int(res.get(k) or 0)

    log.info("DONE %s", totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
