"""Verification state machine.

Candidate → Cross-source confirmed (automatic when identity + tenure agree
across independent sources with no blocking contradiction).

Human Verified is never automatic. It requires a dated checklist with every
required item attested.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CHECKLIST_VERSION = "ami-verify-v1"

CHECKLIST_ITEMS: tuple[dict[str, str], ...] = (
    {
        "id": "identity",
        "label": "Mine identity confirmed (name, operator, and coordinates against sources)",
    },
    {
        "id": "operational_status",
        "label": "Operational status reviewed; Producing was not inferred from permit/claim/MSHA/BMRR/hours alone",
    },
    {
        "id": "tenure",
        "label": "Tenure reviewed, including mixed-tenure and PLSS geometry limitations",
    },
    {
        "id": "contradictions",
        "label": "No unresolved source contradictions, or contradictions documented in notes",
    },
    {
        "id": "sources_reviewed",
        "label": "Source URLs, retrieved dates, and freshness reviewed on the evidence panel",
    },
)

REQUIRED_ITEM_IDS = tuple(item["id"] for item in CHECKLIST_ITEMS)


def empty_checklist() -> dict[str, Any]:
    return {
        "checklist_version": CHECKLIST_VERSION,
        "items": [
            {"id": item["id"], "label": item["label"], "checked": False} for item in CHECKLIST_ITEMS
        ],
        "reviewer_name": None,
        "reviewed_at": None,
        "notes": None,
    }


def _parse_reviewed_at(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Accept date or datetime; reject obviously undated placeholders.
    if text.lower() in {"now", "today", "tbd", "n/a"}:
        return None
    try:
        if len(text) == 10:
            datetime.strptime(text, "%Y-%m-%d")
            return text
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return None


def validate_human_checklist(payload: dict[str, Any] | None) -> tuple[bool, str | None, dict[str, Any]]:
    """Return (ok, error, normalized)."""
    data = payload or {}
    items_in = {str(i.get("id")): bool(i.get("checked")) for i in (data.get("items") or []) if isinstance(i, dict)}
    missing = [item_id for item_id in REQUIRED_ITEM_IDS if not items_in.get(item_id)]
    reviewer = str(data.get("reviewer_name") or "").strip()
    reviewed_at = _parse_reviewed_at(data.get("reviewed_at"))
    normalized = {
        "checklist_version": CHECKLIST_VERSION,
        "items": [
            {
                "id": item["id"],
                "label": item["label"],
                "checked": bool(items_in.get(item["id"])),
            }
            for item in CHECKLIST_ITEMS
        ],
        "reviewer_name": reviewer or None,
        "reviewed_at": reviewed_at,
        "notes": (str(data.get("notes")).strip() if data.get("notes") else None),
    }
    if missing:
        return False, f"Checklist incomplete; unchecked: {', '.join(missing)}", normalized
    if not reviewer:
        return False, "Human Verified requires a reviewer name.", normalized
    if not reviewed_at:
        return False, "Human Verified requires a dated checklist (reviewed_at as YYYY-MM-DD).", normalized
    return True, None, normalized


def auto_verification_state(
    *,
    independent_source_count: int,
    blocking_contradictions: bool,
    identity_confirmed: bool,
    tenure_known: bool,
    sources_usable: bool,
    human_checklist: dict[str, Any] | None = None,
) -> str:
    if human_checklist:
        ok, _, _ = validate_human_checklist(human_checklist)
        if ok:
            return "Human Verified"
    if (
        sources_usable
        and identity_confirmed
        and tenure_known
        and independent_source_count >= 2
        and not blocking_contradictions
    ):
        return "Cross-source confirmed"
    return "Candidate"


def transition_verification(
    current: str | None,
    *,
    proposed: str,
    checklist: dict[str, Any] | None = None,
    independent_source_count: int = 0,
    blocking_contradictions: bool = False,
    identity_confirmed: bool = False,
    tenure_known: bool = False,
    sources_usable: bool = True,
) -> tuple[bool, str, str | None, dict[str, Any] | None]:
    """Apply a verification transition. Auto-promote to Human Verified is rejected."""
    current_state = current or "Candidate"
    if proposed == "Human Verified":
        ok, error, normalized = validate_human_checklist(checklist)
        if not ok:
            return False, current_state, error, normalized
        return True, "Human Verified", None, normalized
    if proposed == "Cross-source confirmed":
        auto = auto_verification_state(
            independent_source_count=independent_source_count,
            blocking_contradictions=blocking_contradictions,
            identity_confirmed=identity_confirmed,
            tenure_known=tenure_known,
            sources_usable=sources_usable,
            human_checklist=None,
        )
        if auto != "Cross-source confirmed":
            return (
                False,
                current_state,
                "Cross-source confirmed requires two usable independent sources, known tenure, identity match, and no blocking contradictions.",
                None,
            )
        return True, "Cross-source confirmed", None, None
    if proposed == "Candidate":
        return True, "Candidate", None, None
    return False, current_state, f"Unknown verification state {proposed!r}", None
