"""
Production payment-status truth layer for MLRS mining claims.

ArcGIS claim discovery does not include maintenance-fee status. Localhost can
inspect the Lightning case page with Playwright/Selenium; production (Render)
must not use Selenium. This module reads the public BLM MLRS case record over
HTTP via Salesforce Aura ``DetailController.getRecord`` — the same guest-visible
``BLM_Case__c`` record the case page renders — and classifies payment from the
authoritative ``Next_Payment_Due_Date__c`` field.

Rules (authoritative evidence only):

- ``paid``: case record loaded and next payment due date is strictly after the
  observation date.
- ``unpaid``: case record loaded and next payment due date is on or before the
  observation date (BLM has not recorded a later due date).
- ``unknown``: the record could not be loaded, the due date is missing/unparseable,
  the request timed out, or the upstream call failed. Missing/failed data is
  never treated as unpaid.

Source: public MLRS case record at ``mlrs.blm.gov`` (Aura RPC), not RAS JS
dashboards and not Selenium.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

log = logging.getLogger("mining_os.mlrs_payment_truth")

SOURCE_NAME = "mlrs_case_aura"
AURA_ENDPOINT = "https://mlrs.blm.gov/s/sfsites/aura"
AURA_GET_RECORD = (
    "serviceComponent://ui.force.components.controllers.detail.DetailController"
    "/ACTION$getRecord"
)
STANDARD_UNPAID_MESSAGE = (
    "Maintenance fee payment was not received and may result in the closing of the claim."
)

EVIDENCE_PAID = "NEXT_PAYMENT_DUE_CURRENT"
EVIDENCE_UNPAID = "NEXT_PAYMENT_DUE_OVERDUE"
EVIDENCE_MISSING_DATE = "NEXT_PAYMENT_DUE_MISSING"
EVIDENCE_UNPARSEABLE_DATE = "NEXT_PAYMENT_DUE_UNPARSEABLE"
EVIDENCE_NO_RECORD = "CASE_RECORD_UNAVAILABLE"
EVIDENCE_INVALID_URL = "INVALID_CASE_URL"
EVIDENCE_TIMEOUT = "TIMEOUT"
EVIDENCE_UPSTREAM = "UPSTREAM_ERROR"

_FWUID_RE = re.compile(r'"fwuid"\s*:\s*"([^"]+)"')
_LOADED_APP_RE = re.compile(
    r'"APPLICATION@markup://siteforce:communityApp"\s*:\s*"([^"]+)"'
)
_SF_ID_RE = re.compile(r"/s/blm-case/([a-zA-Z0-9]{15,18})(?:/|$)", re.IGNORECASE)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def _aura_timeout_sec() -> float:
    return _env_float("MINING_OS_MLRS_PAYMENT_AURA_TIMEOUT_SEC", 12.0, 3.0, 30.0)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def extract_salesforce_id(case_url: str | None) -> str | None:
    """Return the BLM_Case__c Salesforce id from an MLRS case page URL."""
    if not case_url or not isinstance(case_url, str):
        return None
    match = _SF_ID_RE.search(case_url.strip())
    if not match:
        return None
    return match.group(1)


def parse_iso_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _unknown(
    *,
    source_url: str | None,
    code: str,
    evidence: str,
    error: str | None = None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "payment_status": "unknown",
        "payment_message": None,
        "payment_check_source": SOURCE_NAME,
        "payment_source_url": source_url,
        "payment_checked_at": checked_at or utc_now_iso(),
        "payment_evidence_text": evidence,
        "payment_evidence_code": code,
    }
    if error:
        payload["payment_check_error"] = error
    return payload


def interpret_case_record(
    record: dict[str, Any] | None,
    *,
    source_url: str,
    observed_on: date | None = None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Classify paid/unpaid/unknown from a loaded BLM_Case__c record."""
    observed = observed_on or utc_today()
    checked = checked_at or utc_now_iso()
    if not isinstance(record, dict) or not record:
        return _unknown(
            source_url=source_url,
            code=EVIDENCE_NO_RECORD,
            evidence="Aura getRecord succeeded but returned no case record.",
            checked_at=checked,
        )

    due_raw = record.get("Next_Payment_Due_Date__c")
    serial = record.get("Serial_Number__c") or record.get("Lead_File_Number__c")
    status = record.get("Case_Status__c")
    if due_raw in (None, ""):
        return _unknown(
            source_url=source_url,
            code=EVIDENCE_MISSING_DATE,
            evidence=(
                "BLM MLRS case record loaded, but Next_Payment_Due_Date__c is empty"
                + (f" (serial {serial})" if serial else "")
                + (f", case status {status}" if status else "")
                + "."
            ),
            checked_at=checked,
        )

    due = parse_iso_date(due_raw)
    if due is None:
        return _unknown(
            source_url=source_url,
            code=EVIDENCE_UNPARSEABLE_DATE,
            evidence=f"BLM MLRS Next_Payment_Due_Date__c={due_raw!r} could not be parsed.",
            checked_at=checked,
        )

    observed_label = observed.isoformat()
    due_label = due.isoformat()
    status_note = f"; case status {status}" if status else ""

    if due <= observed:
        evidence = (
            f"BLM MLRS case record Next_Payment_Due_Date__c={due_label} "
            f"is on or before observation date {observed_label}{status_note}."
        )
        return {
            "payment_status": "unpaid",
            "payment_message": STANDARD_UNPAID_MESSAGE,
            "payment_check_source": SOURCE_NAME,
            "payment_source_url": source_url,
            "payment_checked_at": checked,
            "payment_evidence_text": evidence,
            "payment_evidence_code": EVIDENCE_UNPAID,
        }

    evidence = (
        f"BLM MLRS case record Next_Payment_Due_Date__c={due_label} "
        f"is after observation date {observed_label}{status_note}."
    )
    return {
        "payment_status": "paid",
        "payment_message": None,
        "payment_check_source": SOURCE_NAME,
        "payment_source_url": source_url,
        "payment_checked_at": checked,
        "payment_evidence_text": evidence,
        "payment_evidence_code": EVIDENCE_PAID,
    }


class MlrsAuraClient:
    """Guest Aura session reused across claims in one Fetch Claim Records run."""

    def __init__(self, timeout_sec: float | None = None) -> None:
        self.timeout_sec = timeout_sec if timeout_sec is not None else _aura_timeout_sec()
        self._session = requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        self._fwuid: str | None = None
        self._loaded_app: str | None = None
        self._bootstrapped = False

    def _aura_context(self) -> dict[str, Any]:
        loaded: dict[str, str] = {}
        if self._loaded_app:
            loaded["APPLICATION@markup://siteforce:communityApp"] = self._loaded_app
        return {
            "mode": "PROD",
            "fwuid": self._fwuid or "",
            "app": "siteforce:communityApp",
            "loaded": loaded,
            "dn": [],
            "globals": {},
            "uad": True,
        }

    def bootstrap(self, case_url: str) -> None:
        response = self._session.get(
            case_url,
            timeout=self.timeout_sec,
            allow_redirects=True,
            headers=_BROWSER_HEADERS,
        )
        response.raise_for_status()
        html = response.text or ""
        fwuid_match = _FWUID_RE.search(html)
        loaded_match = _LOADED_APP_RE.search(html)
        if not fwuid_match:
            raise RuntimeError("MLRS case page did not include an Aura fwuid")
        self._fwuid = fwuid_match.group(1)
        self._loaded_app = loaded_match.group(1) if loaded_match else None
        self._bootstrapped = True

    def get_record(self, record_id: str, case_url: str) -> dict[str, Any]:
        if not self._bootstrapped:
            self.bootstrap(case_url)
        page_path = urlparse(case_url).path or f"/s/blm-case/{record_id}"
        message = {
            "actions": [
                {
                    "id": "1;a",
                    "descriptor": AURA_GET_RECORD,
                    "callingDescriptor": "UNKNOWN",
                    "params": {"recordId": record_id},
                }
            ]
        }
        payload = {
            "message": json.dumps(message, separators=(",", ":")),
            "aura.context": json.dumps(self._aura_context(), separators=(",", ":")),
            "aura.pageURI": page_path,
            "aura.token": "null",
        }
        headers = {
            **_BROWSER_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://mlrs.blm.gov",
            "Referer": case_url,
            "Accept": "application/json, */*",
        }
        response = self._session.post(
            f"{AURA_ENDPOINT}?r=1",
            data=payload,
            headers=headers,
            timeout=self.timeout_sec,
            allow_redirects=True,
        )
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "").lower()
        body = response.text or ""
        if "application/json" not in content_type and not body.lstrip().startswith("{"):
            raise RuntimeError("Aura getRecord did not return JSON (login wall or HTML shell)")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Aura getRecord returned invalid JSON: {exc}") from exc
        actions = data.get("actions") if isinstance(data, dict) else None
        if not actions:
            raise RuntimeError("Aura getRecord returned no actions")
        action = actions[0] if isinstance(actions[0], dict) else {}
        state = (action.get("state") or "").upper()
        if state and state != "SUCCESS":
            raise RuntimeError(f"Aura getRecord state={state or 'unknown'}")
        return_value = action.get("returnValue") or {}
        if not isinstance(return_value, dict):
            raise RuntimeError("Aura getRecord returnValue was not an object")
        record = return_value.get("record")
        if not isinstance(record, dict):
            raise RuntimeError("Aura getRecord omitted the case record")
        return record


def summarize_claim_payments(claims: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Roll up claim payment statuses for batch/API summaries. Empty is not unpaid."""
    paid = unpaid = unknown = 0
    latest: str | None = None
    for claim in claims or []:
        if not isinstance(claim, dict):
            unknown += 1
            continue
        status = (claim.get("payment_status") or "unknown").strip().lower()
        if status == "paid":
            paid += 1
        elif status == "unpaid":
            unpaid += 1
        else:
            unknown += 1
        checked = claim.get("payment_checked_at")
        if isinstance(checked, str) and checked.strip():
            if latest is None or checked > latest:
                latest = checked
    return {
        "paid_count": paid,
        "unpaid_count": unpaid,
        "unknown_count": unknown,
        "payment_checked_at": latest,
    }


def payment_from_mlrs_aura(
    case_url: str,
    *,
    client: MlrsAuraClient | None = None,
    observed_on: date | None = None,
) -> dict[str, Any]:
    """Load the public MLRS case record and return a payment truth payload."""
    source_url = (case_url or "").strip()
    record_id = extract_salesforce_id(source_url)
    if not source_url.startswith("http") or not record_id:
        return _unknown(
            source_url=source_url or None,
            code=EVIDENCE_INVALID_URL,
            evidence="Case page URL is missing a public MLRS Salesforce case id.",
            error="invalid or incomplete MLRS case_page URL",
        )

    aura = client or MlrsAuraClient()
    try:
        record = aura.get_record(record_id, source_url)
    except requests.Timeout as exc:
        log.info("mlrs aura timeout %s: %s", source_url[:80], exc)
        return _unknown(
            source_url=source_url,
            code=EVIDENCE_TIMEOUT,
            evidence="Timed out loading the public BLM MLRS case record.",
            error=str(exc),
        )
    except requests.RequestException as exc:
        log.info("mlrs aura upstream failure %s: %s", source_url[:80], exc)
        return _unknown(
            source_url=source_url,
            code=EVIDENCE_UPSTREAM,
            evidence="BLM MLRS Aura request failed while loading the case record.",
            error=str(exc),
        )
    except Exception as exc:
        log.info("mlrs aura record unavailable %s: %s", source_url[:80], exc)
        return _unknown(
            source_url=source_url,
            code=EVIDENCE_NO_RECORD,
            evidence="Public BLM MLRS case record could not be read.",
            error=str(exc),
        )

    return interpret_case_record(record, source_url=source_url, observed_on=observed_on)
