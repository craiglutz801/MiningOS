"""
Public share links for a tailored, no-login view of selected targets.

A share link stores the set of target ids belonging to an account. The public
viewer (``GET /api/share/{token}``) loads *live* data for those targets scoped
to the link's account, then returns only the trimmed fields safe to expose:

  - Target name
  - PLSS coordinate
  - Latitude / Longitude
  - Minerals present
  - Known reports (report links)
  - List of UNPAID claims (with the BLM case page link)

Account ids, internal scores, notes, and other private fields are never
included in the public payload.
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.services.auth import current_account_id

_TOKEN_BYTES = 16
_MAX_AREAS_PER_LINK = 2000


def _generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def create_share_link(
    area_ids: list[int],
    *,
    title: str | None = None,
    account_id: int | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    """Create a share link for ``area_ids`` within the current account.

    Only ids that actually belong to the account are persisted, so a caller
    cannot leak targets from another workspace.
    """
    acct = account_id if account_id is not None else current_account_id()
    clean_ids = sorted({int(i) for i in area_ids if i is not None})
    if not clean_ids:
        raise ValueError("Select at least one target to share.")
    if len(clean_ids) > _MAX_AREAS_PER_LINK:
        raise ValueError(f"Cannot share more than {_MAX_AREAS_PER_LINK} targets at once.")

    eng = get_engine()
    with eng.begin() as conn:
        owned = conn.execute(
            text(
                """
                SELECT id FROM areas_of_focus
                WHERE account_id = :account_id AND id = ANY(:ids)
                """
            ),
            {"account_id": acct, "ids": clean_ids},
        ).fetchall()
        owned_ids = sorted({int(r[0]) for r in owned})
        if not owned_ids:
            raise ValueError("None of the selected targets were found in this workspace.")

        token = _generate_token()
        row = conn.execute(
            text(
                """
                INSERT INTO share_links (token, account_id, created_by, title, area_ids)
                VALUES (:token, :account_id, :created_by, :title, :area_ids)
                RETURNING token, created_at
                """
            ),
            {
                "token": token,
                "account_id": acct,
                "created_by": created_by,
                "title": (title or None),
                "area_ids": owned_ids,
            },
        ).mappings().first()

    return {
        "token": row["token"],
        "created_at": row["created_at"].isoformat() if row and row["created_at"] else None,
        "count": len(owned_ids),
        "path": f"/share/{row['token']}",
    }


def _coalesce_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _claim_field(claim: dict[str, Any], *names: str) -> str | None:
    for name in names:
        if name in claim:
            v = _coalesce_str(claim.get(name))
            if v:
                return v
    return None


def _collect_unpaid_claims(characteristics: Any) -> list[dict[str, Any]]:
    """Pull every UNPAID claim out of the stored MLRS / LR2000 snapshots."""
    if not isinstance(characteristics, dict):
        return []

    buckets: list[Any] = []
    for key in ("claim_records", "lr2000_geographic_index"):
        block = characteristics.get(key)
        if isinstance(block, dict):
            buckets.append(block.get("claims"))

    unpaid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claims in buckets:
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            status = str(claim.get("payment_status") or "").strip().lower()
            if status != "unpaid":
                continue
            serial = _claim_field(claim, "serial_number", "CSE_NR") or ""
            name = _claim_field(claim, "claim_name", "CSE_NAME")
            dedupe_key = serial or name or repr(claim)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unpaid.append(
                {
                    "claim_name": name,
                    "serial_number": serial or None,
                    "case_page": _claim_field(claim, "case_page"),
                    "payment_report": _claim_field(claim, "payment_report"),
                    "payment_message": _claim_field(claim, "payment_message"),
                }
            )
    return unpaid


def _public_area_payload(row: dict[str, Any]) -> dict[str, Any]:
    minerals = row.get("minerals") or []
    if not isinstance(minerals, list):
        minerals = []
    report_links = row.get("report_links") or []
    if not isinstance(report_links, list):
        report_links = []
    reports = [r for r in (_coalesce_str(x) for x in report_links) if r]

    return {
        "id": int(row["id"]),
        "name": _coalesce_str(row.get("name")) or "Untitled target",
        "location_plss": _coalesce_str(row.get("location_plss")),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "minerals": [m for m in (_coalesce_str(x) for x in minerals) if m],
        "reports": reports,
        "report_summary": _coalesce_str(row.get("report_summary")),
        "unpaid_claims": _collect_unpaid_claims(row.get("characteristics")),
    }


def get_shared_view(token: str) -> dict[str, Any] | None:
    """Public, account-agnostic lookup of a share link's tailored view.

    Returns ``None`` when the token is unknown, revoked, or expired.
    """
    token = (token or "").strip()
    if not token:
        return None

    eng = get_engine()
    with eng.begin() as conn:
        link = conn.execute(
            text(
                """
                SELECT id, account_id, title, area_ids, created_at, expires_at, revoked
                FROM share_links
                WHERE token = :token
                """
            ),
            {"token": token},
        ).mappings().first()
        if not link or link["revoked"]:
            return None
        if link["expires_at"] is not None:
            expired = conn.execute(
                text("SELECT (:expires_at < now()) AS expired"),
                {"expires_at": link["expires_at"]},
            ).scalar()
            if expired:
                return None

        area_ids = list(link["area_ids"] or [])
        rows: list[dict[str, Any]] = []
        if area_ids:
            fetched = conn.execute(
                text(
                    """
                    SELECT id, name, location_plss, latitude, longitude,
                           minerals, report_links, report_summary, characteristics
                    FROM areas_of_focus
                    WHERE account_id = :account_id AND id = ANY(:ids)
                    """
                ),
                {"account_id": link["account_id"], "ids": area_ids},
            ).mappings().all()
            by_id = {int(r["id"]): dict(r) for r in fetched}
            for aid in area_ids:
                if int(aid) in by_id:
                    rows.append(by_id[int(aid)])

        conn.execute(
            text("UPDATE share_links SET view_count = view_count + 1 WHERE id = :id"),
            {"id": link["id"]},
        )

    targets = [_public_area_payload(r) for r in rows]
    total_unpaid = sum(len(t["unpaid_claims"]) for t in targets)
    return {
        "title": _coalesce_str(link["title"]),
        "created_at": link["created_at"].isoformat() if link["created_at"] else None,
        "target_count": len(targets),
        "unpaid_claim_count": total_unpaid,
        "targets": targets,
    }
