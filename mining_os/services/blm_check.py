"""
Use BLM_ClaimAgent (sibling repo) to check paid/unpaid status by coords or PLSS.

Expects Agents/BLM_ClaimAgent on the path or MINING_OS_BLM_AGENT_PATH set.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("mining_os.blm_check")

# Add sibling BLM_ClaimAgent so we can import blm_claim_agent
def _blm_agent_path() -> Path | None:
    env = Path(__file__).resolve().parents[2]
    sibling = env.parent / "BLM_ClaimAgent"
    if sibling.exists() and (sibling / "blm_claim_agent.py").exists():
        return sibling
    import os
    custom = os.getenv("MINING_OS_BLM_AGENT_PATH")
    if custom and Path(custom).exists():
        return Path(custom)
    return None


def _get_agent():
    path = _blm_agent_path()
    if not path:
        return None
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    try:
        from blm_claim_agent import BLMClaimAgent
        return BLMClaimAgent()
    except Exception as e:
        log.warning("BLM_ClaimAgent not available: %s", e)
        return None


def check_by_coords(lat: float, lon: float) -> list[dict]:
    """Return list of claims at (lat, lon) with case_page and payment_report URLs."""
    agent = _get_agent()
    if not agent:
        return []
    try:
        claims = agent.query_claims_by_coords(lat, lon)
        return [
            {
                "claim_name": c.get("claim_name"),
                "serial_number": c.get("serial_number"),
                "case_page": c.get("case_page"),
                "payment_report": c.get("payment_report"),
                "mtrs": c.get("mtrs"),
                "status": c.get("status"),
            }
            for c in claims
        ]
    except Exception as e:
        log.exception("BLM query by coords failed: %s", e)
        return []


def check_payment_status(serial_number: str) -> dict:
    """Check paid/unpaid for one claim by serial number (CSE_NR)."""
    agent = _get_agent()
    if not agent:
        return {"payment_status": "unknown", "error": "BLM agent not available"}
    try:
        info = agent.check_claim_payment_status(serial_number)
        if info.get("error"):
            return {"payment_status": "unknown", "error": info["error"]}
        if info.get("last_payment_date"):
            return {"payment_status": "paid", "last_payment_date": info["last_payment_date"]}
        return {"payment_status": "unpaid", "message": info.get("payment_actions") or "No payment found"}
    except Exception as e:
        log.exception("Payment check failed: %s", e)
        return {"payment_status": "unknown", "error": str(e)}


def check_area_by_coords(area_id: int, lat: float, lon: float) -> dict | None:
    """
    For an area_of_focus with coordinates, query BLM and update status from first claim.
    Returns update summary or None if no claims/agent.
    """
    from mining_os.services.areas_of_focus import get_area, update_area_status

    area = get_area(area_id)
    if not area:
        return None
    claims = check_by_coords(lat, lon)
    if not claims:
        return {"area_id": area_id, "claims_found": 0, "status": "unknown"}
    first = claims[0]
    payment = check_payment_status(first["serial_number"])
    status = payment.get("payment_status", "unknown")
    update_area_status(
        area_id,
        status=status,
        blm_serial_number=first.get("serial_number"),
        blm_case_url=first.get("case_page"),
    )
    return {
        "area_id": area_id,
        "claims_found": len(claims),
        "status": status,
        "serial_number": first.get("serial_number"),
        "case_page": first.get("case_page"),
    }
