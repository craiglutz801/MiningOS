"""
Production payment-status truth layer for MLRS mining claims.

ArcGIS claim discovery does not include maintenance-fee status. Localhost can
inspect the Lightning case page with Playwright/Selenium; production (Render)
must not use Selenium. This module reads the public BLM MLRS case record over
HTTP via Salesforce Aura ``DetailController.getRecord`` — the same guest-visible
``BLM_Case__c`` record the case page renders.

Normalized claim labels
-----------------------
``payment_status`` is **not** inferred as Paid/Unpaid from the due date alone.

- ``paid``: explicit payment evidence in the case record (a maintenance-fee
  payment date / received flag). A future due date is **not** a receipt.
- ``unpaid``: explicit BLM nonpayment warning (the standard maintenance-fee
  phrase, or an equivalent nonpayment field). A stale past due date is **not**
  that warning.
- ``current``: open case whose next-payment due date is strictly after the
  observation date. Supporting evidence only — includes small-miner waiver
  holdings that keep a claim current without a cash payment.
- ``due_today``: open case whose due date **equals** the observation date.
  BLM treats fees/waivers as timely on or before the due date, so this is
  not overdue and not unpaid.
- ``past_due``: open case whose due date is strictly before the observation
  date, with no explicit nonpayment warning.
- ``closed``: case status is closed / void / forfeited / abandoned / equivalent.
  Historical due dates on closed cases are not unpaid.
- ``unknown``: missing record, identity mismatch, unparseable/missing fields,
  schema drift, timeout, or upstream failure. Missing data is never unpaid.

Source fields (public ``BLM_Case__c`` via Aura getRecord)
---------------------------------------------------------
- Identity: ``Serial_Number__c``, ``Lead_File_Number__c`` (plus Lightning ``Name``)
- Lifecycle: ``Case_Status__c`` (and ``Status`` if present)
- Due date (supporting): ``Next_Payment_Due_Date__c``
- Payment evidence (when present on the layout): ``Last_Payment_Date__c``,
  ``Maintenance_Fee_Paid_Date__c``, ``Last_Maintenance_Fee_Payment_Date__c``,
  ``Payment_Received_Date__c``, plus any other custom field whose API name
  clearly denotes a received maintenance-fee payment
- Waiver evidence (when present): ``Small_Miner_Waiver__c`` and any custom
  field whose API name contains ``waiver``
- Nonpayment evidence (when present): field values containing the BLM
  maintenance-fee warning, or a dedicated nonpayment/delinquent flag

Aura ``/s/sfsites/aura`` is an undocumented Salesforce implementation detail.
See ``docs/MLRS_FETCH_CLAIM_RECORDS_AUTOMATION.md`` for the live smoke procedure
and ``tests/fixtures/mlrs_aura/`` for a redacted production-shaped response.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger("mining_os.mlrs_payment_truth")

SOURCE_NAME = "mlrs_case_aura"
AURA_ENDPOINT = "https://mlrs.blm.gov/s/sfsites/aura"
AURA_GET_RECORD = (
    "serviceComponent://ui.force.components.controllers.detail.DetailController"
    "/ACTION$getRecord"
)
APPROVED_MLRS_HOST = "mlrs.blm.gov"
APPROVED_RAS_HOST = "reports.blm.gov"
STANDARD_UNPAID_MESSAGE = (
    "Maintenance fee payment was not received and may result in the closing of the claim."
)
UNPAID_PHRASE = "maintenance fee payment was not received"

# Evidence codes
EVIDENCE_PAID = "PAYMENT_RECORDED"
EVIDENCE_WAIVER_CURRENT = "SMALL_MINER_WAIVER_CURRENT"
EVIDENCE_CURRENT = "NEXT_PAYMENT_DUE_CURRENT"
EVIDENCE_DUE_TODAY = "NEXT_PAYMENT_DUE_TODAY"
EVIDENCE_PAST_DUE = "NEXT_PAYMENT_DUE_PAST"
EVIDENCE_UNPAID = "NONPAYMENT_WARNING"
EVIDENCE_CLOSED = "CASE_CLOSED"
EVIDENCE_SERIAL_MISMATCH = "SERIAL_MISMATCH"
EVIDENCE_MISSING_DATE = "NEXT_PAYMENT_DUE_MISSING"
EVIDENCE_UNPARSEABLE_DATE = "NEXT_PAYMENT_DUE_UNPARSEABLE"
EVIDENCE_NO_RECORD = "CASE_RECORD_UNAVAILABLE"
EVIDENCE_INVALID_URL = "INVALID_CASE_URL"
EVIDENCE_TIMEOUT = "TIMEOUT"
EVIDENCE_UPSTREAM = "UPSTREAM_ERROR"
EVIDENCE_SCHEMA_DRIFT = "SCHEMA_DRIFT"
EVIDENCE_REDIRECT = "REDIRECT_BLOCKED"

# Back-compat aliases used by older snapshots / tests
EVIDENCE_PAID_LEGACY = "NEXT_PAYMENT_DUE_CURRENT"
EVIDENCE_UNPAID_LEGACY = "NEXT_PAYMENT_DUE_OVERDUE"

AUTHORITATIVE_PAID_CODES = frozenset({EVIDENCE_PAID, "PAYMENT_RECEIVED"})
AUTHORITATIVE_UNPAID_CODES = frozenset({EVIDENCE_UNPAID, "MAINTENANCE_FEE_WARNING"})
CLOSED_STATUSES = frozenset({
    "closed",
    "void",
    "voided",
    "forfeited",
    "forfeit",
    "abandoned",
    "cancelled",
    "canceled",
    "relinquished",
    "withdrawn",
    "expired",
    "inactive",
    "closed-patent",
    "closed patent",
})

_SF_ID_RE = re.compile(r"^[a-zA-Z0-9]{15}(?:[a-zA-Z0-9]{3})?$")
_CASE_PATH_RE = re.compile(
    r"^/s/blm-case/([a-zA-Z0-9]{15,18})(?:/([A-Za-z0-9._-]+))?/?$",
    re.IGNORECASE,
)
_FWUID_RE = re.compile(r'"fwuid"\s*:\s*"([^"]+)"')
_LOADED_APP_RE = re.compile(
    r'"APPLICATION@markup://siteforce:communityApp"\s*:\s*"([^"]+)"'
)

_PAYMENT_DATE_FIELDS = (
    "Last_Payment_Date__c",
    "Last_Maintenance_Fee_Payment_Date__c",
    "Maintenance_Fee_Paid_Date__c",
    "Payment_Received_Date__c",
    "Maintenance_Fee_Payment_Date__c",
    "Date_Paid__c",
)
_PAYMENT_FLAG_FIELDS = (
    "Maintenance_Fee_Paid__c",
    "Payment_Received__c",
    "Fees_Paid__c",
)
_WAIVER_FIELDS = (
    "Small_Miner_Waiver__c",
    "Small_Miner_Waiver_Filed__c",
    "SMW_Waiver__c",
    "Waiver_Filed__c",
    "Maintenance_Fee_Waiver__c",
)
_STATUS_FIELDS = ("Case_Status__c", "Status", "Case_Disposition__c")
_SERIAL_FIELDS = ("Serial_Number__c", "Lead_File_Number__c", "Name")
_DUE_FIELDS = ("Next_Payment_Due_Date__c",)
_CONTRACT_FIELD_GROUPS = {
    "identity": _SERIAL_FIELDS,
    "lifecycle": _STATUS_FIELDS,
    "due_date": _DUE_FIELDS,
}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TRUTHY = frozenset({"true", "t", "yes", "y", "1", "on", "filed", "approved", "active", "waived"})
_PAYMENT_NAME_RE = re.compile(
    r"(last_.{0,20}payment|payment_.{0,20}(date|received|paid)|fee_paid|fees_paid)",
    re.I,
)
_WAIVER_NAME_RE = re.compile(r"waiver", re.I)
_NONPAY_NAME_RE = re.compile(r"non[-_ ]?pay|delinquen|unpaid_flag|overdue_flag", re.I)


class UnsafeMlrsUrlError(ValueError):
    """Case/report URL failed the production host/path allow-list."""


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


def parse_iso_date(value: Any) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_serial(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text)


def serials_match(expected: Any, *candidates: Any) -> bool:
    want = normalize_serial(expected)
    if not want:
        return False
    for candidate in candidates:
        got = normalize_serial(candidate)
        if got and (got == want or got.endswith(want) or want.endswith(got)):
            return True
    return False


def is_closed_status(value: Any) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()
    if not text:
        return False
    if text in CLOSED_STATUSES:
        return True
    tokens = set(text.split())
    return bool(tokens & {"closed", "void", "voided", "forfeited", "forfeit", "abandoned", "cancelled", "canceled", "relinquished"})


def _field_value(raw: Any) -> Any:
    if isinstance(raw, dict):
        if "value" in raw:
            return raw.get("value")
        if "displayValue" in raw and raw.get("displayValue") not in (None, ""):
            return raw.get("displayValue")
    return raw


def flatten_aura_record(record: dict[str, Any] | None) -> dict[str, Any]:
    """Return a flat API-name → value map from Aura getRecord payloads."""
    if not isinstance(record, dict) or not record:
        return {}
    flat: dict[str, Any] = {}
    nested = record.get("fields")
    if isinstance(nested, dict):
        for key, raw in nested.items():
            if isinstance(key, str) and key:
                flat[key] = _field_value(raw)
    for key, raw in record.items():
        if key in {"fields", "childRelationships", "recordTypeInfo"}:
            continue
        if key in flat:
            continue
        if isinstance(key, str) and key:
            flat[key] = _field_value(raw)
    return flat


def extract_salesforce_id(case_url: str | None) -> str | None:
    parsed = validate_mlrs_case_url(case_url)
    if not parsed.get("ok"):
        return None
    return parsed.get("record_id")


def validate_mlrs_case_url(case_url: str | None) -> dict[str, Any]:
    """Allow only https://mlrs.blm.gov/s/blm-case/<sfId>/... with no userinfo."""
    raw = (case_url or "").strip() if isinstance(case_url, str) else ""
    if not raw:
        return {"ok": False, "error": "missing MLRS case URL", "url": raw or None}
    try:
        parts = urlparse(raw)
    except Exception:
        return {"ok": False, "error": "unparseable MLRS case URL", "url": raw}
    if (parts.scheme or "").lower() != "https":
        return {"ok": False, "error": "MLRS case URL must use HTTPS", "url": raw}
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        return {"ok": False, "error": "MLRS case URL must not include userinfo", "url": raw}
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if host != APPROVED_MLRS_HOST:
        return {"ok": False, "error": f"MLRS case URL host must be {APPROVED_MLRS_HOST}", "url": raw}
    if parts.port not in (None, 443):
        return {"ok": False, "error": "MLRS case URL must not use a non-default port", "url": raw}
    path_match = _CASE_PATH_RE.match(parts.path or "")
    if not path_match:
        return {"ok": False, "error": "MLRS case URL path must be /s/blm-case/<id>/…", "url": raw}
    record_id = path_match.group(1)
    slug = path_match.group(2)
    if not _SF_ID_RE.match(record_id):
        return {"ok": False, "error": "MLRS case URL Salesforce id is not a valid SFID", "url": raw}
    return {
        "ok": True,
        "url": raw,
        "record_id": record_id,
        "serial_slug": slug,
        "normalized": f"https://{APPROVED_MLRS_HOST}{parts.path}",
    }


def validate_ras_report_url(report_url: str | None) -> dict[str, Any]:
    raw = (report_url or "").strip() if isinstance(report_url, str) else ""
    if not raw:
        return {"ok": False, "error": "missing RAS report URL", "url": raw or None}
    try:
        parts = urlparse(raw)
    except Exception:
        return {"ok": False, "error": "unparseable RAS report URL", "url": raw}
    if (parts.scheme or "").lower() != "https":
        return {"ok": False, "error": "RAS URL must use HTTPS", "url": raw}
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        return {"ok": False, "error": "RAS URL must not include userinfo", "url": raw}
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if host != APPROVED_RAS_HOST:
        return {"ok": False, "error": f"RAS URL host must be {APPROVED_RAS_HOST}", "url": raw}
    if parts.port not in (None, 443):
        return {"ok": False, "error": "RAS URL must not use a non-default port", "url": raw}
    path = (parts.path or "").lower()
    if "/report.cfm" not in path and "/ireport/" not in path:
        return {"ok": False, "error": "RAS URL path is not a Serial Register report", "url": raw}
    return {"ok": True, "url": raw, "normalized": raw}


def is_approved_mlrs_url(url: str | None) -> bool:
    parts = urlparse((url or "").strip())
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if host != APPROVED_MLRS_HOST:
        return False
    if (parts.scheme or "").lower() != "https":
        return False
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        return False
    if parts.port not in (None, 443):
        return False
    path = parts.path or ""
    if path.startswith("/s/blm-case/"):
        return bool(validate_mlrs_case_url(url).get("ok"))
    if path.startswith("/s/sfsites/aura"):
        return True
    # Public case-page bootstrap assets stay on the same host.
    return path.startswith("/s/") or path.startswith("/sfsites/")


def request_approved_url(
    session: requests.Session,
    url: str,
    *,
    method: str = "GET",
    timeout: float,
    headers: dict[str, str] | None = None,
    data: Any = None,
    allow_case_or_aura: bool = True,
) -> requests.Response:
    """GET/POST an MLRS URL, following redirects only while they stay on mlrs.blm.gov."""
    current = url
    current_method = method.upper()
    for _ in range(6):
        if allow_case_or_aura:
            if current_method == "GET" and "/s/blm-case/" in (urlparse(current).path or ""):
                parsed = validate_mlrs_case_url(current)
                if not parsed.get("ok"):
                    raise UnsafeMlrsUrlError(parsed.get("error") or "invalid MLRS URL")
            elif not is_approved_mlrs_url(current):
                raise UnsafeMlrsUrlError(f"blocked MLRS request to unapproved URL: {current}")
        response = session.request(
            current_method,
            current,
            timeout=timeout,
            headers=headers,
            data=data if current_method in {"POST", "PUT", "PATCH"} else None,
            allow_redirects=False,
        )
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            location = (response.headers.get("Location") or "").strip()
            if not location:
                raise UnsafeMlrsUrlError("MLRS redirect omitted Location")
            nxt = urljoin(current, location)
            if not is_approved_mlrs_url(nxt):
                raise UnsafeMlrsUrlError(f"MLRS redirect escaped approved host: {nxt}")
            current = nxt
            if response.status_code in {301, 302, 303}:
                current_method = "GET"
                data = None
            continue
        return response
    raise UnsafeMlrsUrlError("too many MLRS redirects")


def request_approved_ras_url(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    current = url
    for _ in range(6):
        parsed = validate_ras_report_url(current)
        if not parsed.get("ok"):
            # Relative iframe paths are resolved before this helper; reject others.
            parts = urlparse(current)
            host = (parts.hostname or "").strip().lower().rstrip(".")
            if host != APPROVED_RAS_HOST or (parts.scheme or "").lower() != "https":
                raise UnsafeMlrsUrlError(parsed.get("error") or "invalid RAS URL")
        response = session.get(current, timeout=timeout, headers=headers, allow_redirects=False)
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            location = (response.headers.get("Location") or "").strip()
            if not location:
                raise UnsafeMlrsUrlError("RAS redirect omitted Location")
            nxt = urljoin(current, location)
            nxt_host = (urlparse(nxt).hostname or "").strip().lower().rstrip(".")
            if nxt_host != APPROVED_RAS_HOST or (urlparse(nxt).scheme or "").lower() != "https":
                raise UnsafeMlrsUrlError(f"RAS redirect escaped approved host: {nxt}")
            current = nxt
            continue
        return response
    raise UnsafeMlrsUrlError("too many RAS redirects")


def _truthy_flag(value: Any) -> bool:
    if value is True:
        return True
    if value in (None, False, 0, "0"):
        return False
    return str(value).strip().lower() in _TRUTHY


def _first_present(flat: dict[str, Any], names: tuple[str, ...]) -> tuple[str | None, Any]:
    for name in names:
        if name in flat and flat.get(name) not in (None, ""):
            return name, flat.get(name)
    for name in names:
        if name in flat:
            return name, flat.get(name)
    return None, None


def diagnose_aura_schema(flat: dict[str, Any]) -> dict[str, Any]:
    """Contract check against the fields the classifier actually reads."""
    present = sorted(k for k in flat if isinstance(k, str))
    missing_groups: list[str] = []
    found: dict[str, str] = {}
    for group, names in _CONTRACT_FIELD_GROUPS.items():
        hit = next((n for n in names if n in flat), None)
        if hit:
            found[group] = hit
        else:
            missing_groups.append(group)
    drift = "due_date" in missing_groups or "identity" in missing_groups
    return {
        "ok": not drift,
        "schema": "ok" if not drift else "drift",
        "missing_groups": missing_groups,
        "found_groups": found,
        "fields_present": present,
    }


def _scan_named_evidence(flat: dict[str, Any]) -> dict[str, Any]:
    payment_date_field = None
    payment_date = None
    waiver_field = None
    waiver = False
    nonpayment_field = None
    nonpayment = False

    for name in _PAYMENT_DATE_FIELDS:
        if name in flat and parse_iso_date(flat.get(name)):
            payment_date_field = name
            payment_date = parse_iso_date(flat.get(name))
            break
    for name in _PAYMENT_FLAG_FIELDS:
        if _truthy_flag(flat.get(name)):
            payment_date_field = payment_date_field or name
            payment_date = payment_date or utc_today()
            break
    for name in _WAIVER_FIELDS:
        if _truthy_flag(flat.get(name)):
            waiver_field = name
            waiver = True
            break

    for name, value in flat.items():
        if not isinstance(name, str):
            continue
        text = str(value or "").strip()
        low = text.lower()
        if UNPAID_PHRASE in low or (_NONPAY_NAME_RE.search(name) and _truthy_flag(value)):
            nonpayment_field = name
            nonpayment = True
        if waiver_field is None and _WAIVER_NAME_RE.search(name) and _truthy_flag(value):
            waiver_field = name
            waiver = True
        if payment_date_field is None and _PAYMENT_NAME_RE.search(name) and "next_payment_due" not in name.lower():
            parsed = parse_iso_date(value)
            if parsed or _truthy_flag(value):
                payment_date_field = name
                payment_date = parsed or utc_today()

    return {
        "payment_date_field": payment_date_field,
        "payment_date": payment_date,
        "waiver_field": waiver_field,
        "waiver": waiver,
        "nonpayment_field": nonpayment_field,
        "nonpayment": nonpayment,
    }


def _payload(
    *,
    status: str,
    source_url: str | None,
    code: str,
    evidence: str,
    checked_at: str,
    message: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "payment_status": status,
        "payment_message": message,
        "payment_check_source": SOURCE_NAME,
        "payment_source_url": source_url,
        "payment_checked_at": checked_at,
        "payment_evidence_text": evidence,
        "payment_evidence_code": code,
    }
    if error:
        out["payment_check_error"] = error
    if extra:
        out.update(extra)
    return out


def unknown_timeout_payload(source_url: str | None = None, *, error: str | None = None) -> dict[str, Any]:
    return _payload(
        status="unknown",
        source_url=source_url,
        code=EVIDENCE_TIMEOUT,
        evidence="Per-run payment-check timeout budget exhausted before this claim was classified.",
        checked_at=utc_now_iso(),
        error=error or "payment enrichment timeout budget exhausted",
    )


def interpret_case_record(
    record: dict[str, Any] | None,
    *,
    source_url: str,
    observed_on: date | None = None,
    checked_at: str | None = None,
    expected_serial: str | None = None,
) -> dict[str, Any]:
    """Classify a loaded BLM_Case__c record. Due date is supporting evidence only."""
    observed = observed_on or utc_today()
    checked = checked_at or utc_now_iso()
    extra_base: dict[str, Any] = {"payment_observed_on": observed.isoformat()}

    if not isinstance(record, dict) or not record:
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_NO_RECORD,
            evidence="Aura getRecord succeeded but returned no case record.",
            checked_at=checked,
            extra=extra_base,
        )

    flat = flatten_aura_record(record)
    health = diagnose_aura_schema(flat)
    extra_base["payment_source_health"] = health["schema"]
    extra_base["payment_fields_present"] = health["fields_present"]

    serial_field, serial = _first_present(flat, _SERIAL_FIELDS)
    lead = flat.get("Lead_File_Number__c")
    status_field, case_status = _first_present(flat, _STATUS_FIELDS)
    due_field, due_raw = _first_present(flat, _DUE_FIELDS)
    extra_base.update(
        {
            "payment_record_serial": serial,
            "payment_case_status": case_status,
            "payment_due_date": str(due_raw)[:10] if due_raw not in (None, "") else None,
            "payment_identity_field": serial_field,
            "payment_status_field": status_field,
            "payment_due_field": due_field,
        }
    )

    expected = (expected_serial or "").strip()
    if expected and serial not in (None, "") and not serials_match(expected, serial, lead):
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_SERIAL_MISMATCH,
            evidence=(
                f"Aura record serial {serial!r} / lead file {lead!r} does not match "
                f"claim serial {expected!r}."
            ),
            checked_at=checked,
            extra=extra_base,
        )
    if expected and serial in (None, "") and lead in (None, ""):
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_SERIAL_MISMATCH,
            evidence=(
                f"Aura record has no Serial_Number__c / Lead_File_Number__c to verify "
                f"claim serial {expected!r}."
            ),
            checked_at=checked,
            extra=extra_base,
        )

    if health["schema"] == "drift":
        # Still classify from whatever is present, but never invent paid/unpaid.
        extra_base["payment_schema_missing"] = health["missing_groups"]

    if is_closed_status(case_status):
        return _payload(
            status="closed",
            source_url=source_url,
            code=EVIDENCE_CLOSED,
            evidence=(
                f"BLM MLRS case status {case_status!r} is closed/void/forfeited/abandoned; "
                "historical due dates are not treated as unpaid."
            ),
            checked_at=checked,
            extra=extra_base,
        )

    evidence_bits = _scan_named_evidence(flat)
    extra_base["payment_waiver"] = bool(evidence_bits["waiver"])
    if evidence_bits["payment_date_field"]:
        extra_base["payment_receipt_field"] = evidence_bits["payment_date_field"]
        extra_base["payment_receipt_date"] = (
            evidence_bits["payment_date"].isoformat() if evidence_bits["payment_date"] else None
        )
    if evidence_bits["waiver_field"]:
        extra_base["payment_waiver_field"] = evidence_bits["waiver_field"]

    due = parse_iso_date(due_raw) if due_raw not in (None, "") else None
    if due_raw not in (None, "") and due is None:
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_UNPARSEABLE_DATE,
            evidence=f"BLM MLRS Next_Payment_Due_Date__c={due_raw!r} could not be parsed.",
            checked_at=checked,
            extra=extra_base,
        )
    extra_base["payment_due_date"] = due.isoformat() if due else extra_base.get("payment_due_date")

    due_indicator = "unknown"
    if due is not None:
        if due > observed:
            due_indicator = "current"
        elif due == observed:
            due_indicator = "due_today"
        else:
            due_indicator = "past_due"
    extra_base["payment_due_indicator"] = due_indicator

    status_note = f"; case status {case_status}" if case_status else ""
    serial_note = f" (serial {serial})" if serial else ""

    if evidence_bits["nonpayment"]:
        return _payload(
            status="unpaid",
            source_url=source_url,
            code=EVIDENCE_UNPAID,
            evidence=(
                f"BLM MLRS case record field {evidence_bits['nonpayment_field']} contains "
                f"explicit nonpayment evidence{serial_note}{status_note}."
            ),
            checked_at=checked,
            message=STANDARD_UNPAID_MESSAGE,
            extra=extra_base,
        )

    if evidence_bits["payment_date"] and due_indicator in {"current", "due_today", "unknown"}:
        paid_due = (
            f" Next_Payment_Due_Date__c={due.isoformat()} supports a still-open compliance window."
            if due
            else " No next-payment due date was present; payment date is treated as receipt evidence only."
        )
        return _payload(
            status="paid",
            source_url=source_url,
            code=EVIDENCE_PAID,
            evidence=(
                f"BLM MLRS case record {evidence_bits['payment_date_field']}="
                f"{evidence_bits['payment_date'].isoformat()} records a maintenance-fee payment."
                f"{paid_due}{status_note}"
            ),
            checked_at=checked,
            extra=extra_base,
        )

    if evidence_bits["waiver"] and due_indicator in {"current", "due_today"}:
        return _payload(
            status="current",
            source_url=source_url,
            code=EVIDENCE_WAIVER_CURRENT,
            evidence=(
                f"BLM MLRS case record {evidence_bits['waiver_field']} indicates a small-miner "
                f"waiver; Next_Payment_Due_Date__c={due.isoformat()} is {due_indicator.replace('_', ' ')}. "
                f"A waiver keeps the claim current without proving a cash payment.{status_note}"
            ),
            checked_at=checked,
            extra=extra_base,
        )

    if due is None:
        code = EVIDENCE_SCHEMA_DRIFT if health["schema"] == "drift" else EVIDENCE_MISSING_DATE
        evidence = (
            "Aura BLM_Case__c record is missing Next_Payment_Due_Date__c (schema drift)."
            if code == EVIDENCE_SCHEMA_DRIFT
            else (
                "BLM MLRS case record loaded, but Next_Payment_Due_Date__c is empty"
                f"{serial_note}{status_note}."
            )
        )
        return _payload(
            status="unknown",
            source_url=source_url,
            code=code,
            evidence=evidence,
            checked_at=checked,
            extra=extra_base,
        )

    observed_label = observed.isoformat()
    due_label = due.isoformat()
    if due_indicator == "past_due":
        return _payload(
            status="past_due",
            source_url=source_url,
            code=EVIDENCE_PAST_DUE,
            evidence=(
                f"BLM MLRS Next_Payment_Due_Date__c={due_label} is before observation date "
                f"{observed_label}{status_note}. Past-due date is a compliance indicator, not the "
                "explicit BLM nonpayment warning."
            ),
            checked_at=checked,
            extra=extra_base,
        )
    if due_indicator == "due_today":
        return _payload(
            status="due_today",
            source_url=source_url,
            code=EVIDENCE_DUE_TODAY,
            evidence=(
                f"BLM MLRS Next_Payment_Due_Date__c={due_label} equals observation date "
                f"{observed_label}{status_note}. Fees/waivers are timely on or before the due date, "
                "so this is not overdue."
            ),
            checked_at=checked,
            extra=extra_base,
        )
    return _payload(
        status="current",
        source_url=source_url,
        code=EVIDENCE_CURRENT,
        evidence=(
            f"BLM MLRS Next_Payment_Due_Date__c={due_label} is after observation date "
            f"{observed_label}{status_note}. A future due date is a compliance deadline, not a "
            "payment receipt."
        ),
        checked_at=checked,
        extra=extra_base,
    )


def cache_crosses_due_boundary(payload: dict[str, Any], *, observed_on: date | None = None) -> bool:
    """True when a cached current/due-today/paid label would be wrong after the due date."""
    observed = observed_on or utc_today()
    due = parse_iso_date(payload.get("payment_due_date"))
    if due is None:
        return False
    status = (payload.get("payment_status") or "").strip().lower()
    if status in {"current", "paid"} and due <= observed:
        return True
    if status == "due_today" and due != observed:
        return True
    return False


def is_authoritative_paid(claim: dict[str, Any] | None) -> bool:
    if not isinstance(claim, dict):
        return False
    status = (claim.get("payment_status") or "").strip().lower()
    code = (claim.get("payment_evidence_code") or "").strip()
    return status == "paid" and (not code or code in AUTHORITATIVE_PAID_CODES)


def is_authoritative_unpaid(claim: dict[str, Any] | None) -> bool:
    if not isinstance(claim, dict):
        return False
    status = (claim.get("payment_status") or "").strip().lower()
    code = (claim.get("payment_evidence_code") or "").strip()
    if status != "unpaid":
        return False
    if code in {EVIDENCE_UNPAID_LEGACY, EVIDENCE_PAST_DUE, EVIDENCE_CURRENT, EVIDENCE_PAID_LEGACY}:
        return False
    return (not code) or code in AUTHORITATIVE_UNPAID_CODES or code == EVIDENCE_UNPAID


def rollup_payment_status(statuses: list[str] | None) -> str:
    """Target-level aggregate. Unknown is never collapsed into Paid."""
    normalized = [(s or "unknown").strip().lower() or "unknown" for s in (statuses or [])]
    if not normalized:
        return "unknown"
    if any(s == "unpaid" for s in normalized):
        return "unpaid"
    if all(s == "paid" for s in normalized):
        return "paid"
    if all(s == "unknown" for s in normalized):
        return "unknown"
    if all(s in {"current", "due_today"} for s in normalized):
        return "current"
    if all(s == "past_due" for s in normalized):
        return "past_due"
    if all(s == "closed" for s in normalized):
        return "closed"
    return "partial"


def summarize_claim_payments(claims: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Roll up claim payment statuses for batch/API summaries. Empty is not unpaid."""
    paid = unpaid = unknown = current = past_due = closed = due_today = 0
    latest: str | None = None
    statuses: list[str] = []
    for claim in claims or []:
        if not isinstance(claim, dict):
            unknown += 1
            statuses.append("unknown")
            continue
        status = (claim.get("payment_status") or "unknown").strip().lower() or "unknown"
        statuses.append(status)
        if status == "paid":
            paid += 1
        elif status == "unpaid":
            unpaid += 1
        elif status == "current":
            current += 1
        elif status == "due_today":
            due_today += 1
        elif status == "past_due":
            past_due += 1
        elif status == "closed":
            closed += 1
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
        "current_count": current,
        "past_due_count": past_due,
        "closed_count": closed,
        "due_today_count": due_today,
        "payment_checked_at": latest,
        "rollup": rollup_payment_status(statuses),
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
        response = request_approved_url(
            self._session,
            case_url,
            timeout=self.timeout_sec,
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
        response = request_approved_url(
            self._session,
            f"{AURA_ENDPOINT}?r=1",
            method="POST",
            data=payload,
            headers=headers,
            timeout=self.timeout_sec,
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


def payment_from_mlrs_aura(
    case_url: str,
    *,
    client: MlrsAuraClient | None = None,
    observed_on: date | None = None,
    expected_serial: str | None = None,
) -> dict[str, Any]:
    """Load the public MLRS case record and return a payment truth payload."""
    parsed = validate_mlrs_case_url(case_url)
    source_url = (case_url or "").strip() if isinstance(case_url, str) else ""
    if not parsed.get("ok"):
        return _payload(
            status="unknown",
            source_url=source_url or None,
            code=EVIDENCE_INVALID_URL,
            evidence=parsed.get("error") or "Case page URL is not an approved public MLRS case URL.",
            checked_at=utc_now_iso(),
            error=parsed.get("error") or "invalid or incomplete MLRS case_page URL",
        )
    record_id = parsed["record_id"]

    aura = client or MlrsAuraClient()
    try:
        record = aura.get_record(record_id, parsed.get("normalized") or source_url)
    except UnsafeMlrsUrlError as exc:
        log.info("mlrs aura blocked url %s: %s", source_url[:80], exc)
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_REDIRECT,
            evidence="Blocked an MLRS request whose URL or redirect left mlrs.blm.gov.",
            checked_at=utc_now_iso(),
            error=str(exc),
        )
    except requests.Timeout as exc:
        log.info("mlrs aura timeout %s: %s", source_url[:80], exc)
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_TIMEOUT,
            evidence="Timed out loading the public BLM MLRS case record.",
            checked_at=utc_now_iso(),
            error=str(exc),
        )
    except requests.RequestException as exc:
        log.info("mlrs aura upstream failure %s: %s", source_url[:80], exc)
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_UPSTREAM,
            evidence="BLM MLRS Aura request failed while loading the case record.",
            checked_at=utc_now_iso(),
            error=str(exc),
        )
    except Exception as exc:
        log.info("mlrs aura record unavailable %s: %s", source_url[:80], exc)
        return _payload(
            status="unknown",
            source_url=source_url,
            code=EVIDENCE_NO_RECORD,
            evidence="Public BLM MLRS case record could not be read.",
            checked_at=utc_now_iso(),
            error=str(exc),
        )

    return interpret_case_record(
        record,
        source_url=source_url,
        observed_on=observed_on,
        expected_serial=expected_serial,
    )
