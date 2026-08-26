"""Focused tests for the production MLRS payment-status truth layer."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import requests

from mining_os.services.mlrs_payment_truth import (
    APPROVED_MLRS_HOST,
    EVIDENCE_CLOSED,
    EVIDENCE_CURRENT,
    EVIDENCE_DUE_TODAY,
    EVIDENCE_INVALID_URL,
    EVIDENCE_MISSING_DATE,
    EVIDENCE_NO_RECORD,
    EVIDENCE_PAID,
    EVIDENCE_PAST_DUE,
    EVIDENCE_SCHEMA_DRIFT,
    EVIDENCE_SERIAL_MISMATCH,
    EVIDENCE_TIMEOUT,
    EVIDENCE_UNPAID,
    EVIDENCE_UPSTREAM,
    EVIDENCE_WAIVER_CURRENT,
    MlrsAuraClient,
    UnsafeMlrsUrlError,
    cache_crosses_due_boundary,
    diagnose_aura_schema,
    extract_salesforce_id,
    flatten_aura_record,
    interpret_case_record,
    payment_from_mlrs_aura,
    request_approved_url,
    rollup_payment_status,
    summarize_claim_payments,
    validate_mlrs_case_url,
)


CASE_URL = "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT101527746"
OBSERVED = date(2026, 8, 26)
FIXTURE = Path(__file__).parent / "fixtures" / "mlrs_aura" / "get_record_redacted.json"


def _record(**fields):
    return {
        "apiName": "BLM_Case__c",
        "id": "a02t000000593dSAAQ",
        "Serial_Number__c": fields.pop("Serial_Number__c", "UT101527746"),
        "Lead_File_Number__c": fields.pop("Lead_File_Number__c", "UT101527746"),
        "Case_Status__c": fields.pop("Case_Status__c", "Active"),
        **fields,
    }


def test_extracts_salesforce_id_from_case_url():
    assert extract_salesforce_id(CASE_URL) == "a02t000000593dSAAQ"
    assert extract_salesforce_id("https://mlrs.blm.gov/s/research-map") is None
    assert extract_salesforce_id("") is None


def test_rejects_ssrf_and_non_mlrs_case_urls():
    bad = [
        "http://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://localhost/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://127.0.0.1/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://10.0.0.5/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://evil.example/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://mlrs.blm.gov.evil.example/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://user:pass@mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://mlrs.blm.gov:8443/s/blm-case/a02t000000593dSAAQ/UT1",
        "https://mlrs.blm.gov/s/research-map",
        CASE_URL.replace("https://mlrs.blm.gov", "https://not-mlrs.blm.gov"),
    ]
    for url in bad:
        parsed = validate_mlrs_case_url(url)
        assert parsed["ok"] is False, url
        assert payment_from_mlrs_aura(url)["payment_evidence_code"] == EVIDENCE_INVALID_URL


def test_redirect_escape_is_blocked(monkeypatch):
    session = requests.Session()

    class _Redirect:
        status_code = 302
        is_redirect = True
        headers = {"Location": "http://127.0.0.1:9/secret"}
        text = ""

        def raise_for_status(self):
            return None

    monkeypatch.setattr(session, "request", lambda *a, **k: _Redirect())
    try:
        request_approved_url(session, CASE_URL, timeout=2)
        raise AssertionError("redirect to localhost must be blocked")
    except UnsafeMlrsUrlError as exc:
        assert APPROVED_MLRS_HOST in str(exc) or "escaped" in str(exc).lower() or "127.0.0.1" in str(exc)


def test_interpret_current_not_paid_when_due_date_is_in_the_future():
    out = interpret_case_record(
        _record(Next_Payment_Due_Date__c="2027-09-01"),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        checked_at="2026-08-26T12:00:00Z",
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "current"
    assert out["payment_evidence_code"] == EVIDENCE_CURRENT
    assert out["payment_message"] is None
    assert "not a payment receipt" in (out["payment_evidence_text"] or "").lower()


def test_interpret_due_today_is_not_unpaid():
    out = interpret_case_record(
        _record(Next_Payment_Due_Date__c="2026-08-26"),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "due_today"
    assert out["payment_evidence_code"] == EVIDENCE_DUE_TODAY
    assert out["payment_message"] is None


def test_interpret_past_due_is_not_unpaid_without_warning():
    out = interpret_case_record(
        _record(Next_Payment_Due_Date__c="2024-09-03", Case_Status__c="Active"),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "past_due"
    assert out["payment_evidence_code"] == EVIDENCE_PAST_DUE
    assert out["payment_message"] is None


def test_interpret_paid_requires_payment_receipt_field():
    out = interpret_case_record(
        _record(
            Next_Payment_Due_Date__c="2027-09-01",
            Last_Payment_Date__c="2026-08-01",
            Case_Status__c="Active",
        ),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "paid"
    assert out["payment_evidence_code"] == EVIDENCE_PAID


def test_interpret_waiver_current_is_not_paid():
    out = interpret_case_record(
        _record(
            Next_Payment_Due_Date__c="2027-09-01",
            Small_Miner_Waiver__c=True,
            Case_Status__c="Active",
        ),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "current"
    assert out["payment_evidence_code"] == EVIDENCE_WAIVER_CURRENT
    assert "waiver" in (out["payment_evidence_text"] or "").lower()


def test_interpret_explicit_nonpayment_field_is_unpaid():
    out = interpret_case_record(
        _record(
            Next_Payment_Due_Date__c="2027-09-01",
            Nonpayment_Notice__c="Maintenance fee payment was not received and may result in the closing of the claim.",
        ),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "unpaid"
    assert out["payment_evidence_code"] == EVIDENCE_UNPAID
    assert "Maintenance fee payment was not received" in (out["payment_message"] or "")


def test_interpret_closed_with_past_due_date_is_closed_not_unpaid():
    out = interpret_case_record(
        _record(Next_Payment_Due_Date__c="2024-09-03", Case_Status__c="Closed"),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "closed"
    assert out["payment_evidence_code"] == EVIDENCE_CLOSED
    assert out["payment_message"] is None


def test_interpret_forfeited_is_closed():
    out = interpret_case_record(
        _record(Next_Payment_Due_Date__c="2020-09-01", Case_Status__c="Forfeited"),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "closed"


def test_interpret_serial_mismatch_is_unknown():
    out = interpret_case_record(
        _record(
            Next_Payment_Due_Date__c="2027-09-01",
            Serial_Number__c="NV101000001",
            Lead_File_Number__c="NV101000001",
        ),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_SERIAL_MISMATCH


def test_interpret_unknown_when_due_date_missing():
    out = interpret_case_record(
        _record(Case_Status__c="Active", Next_Payment_Due_Date__c=""),
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_MISSING_DATE


def test_interpret_unknown_when_record_missing():
    out = interpret_case_record(None, source_url=CASE_URL, observed_on=OBSERVED)
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_NO_RECORD


def test_schema_drift_when_contract_fields_absent():
    flat = flatten_aura_record({"apiName": "BLM_Case__c", "id": "a02t000000593dSAAQ", "Color__c": "red"})
    health = diagnose_aura_schema(flat)
    assert health["schema"] == "drift"
    out = interpret_case_record(
        {"apiName": "BLM_Case__c", "id": "a02t000000593dSAAQ", "Color__c": "red"},
        source_url=CASE_URL,
        observed_on=OBSERVED,
    )
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_SCHEMA_DRIFT
    assert out["payment_source_health"] == "drift"


def test_redacted_live_fixture_classifies_closed_not_unpaid():
    envelope = json.loads(FIXTURE.read_text())
    record = envelope["actions"][0]["returnValue"]["record"]
    out = interpret_case_record(
        record,
        source_url=CASE_URL,
        observed_on=OBSERVED,
        expected_serial="UT101527746",
    )
    assert out["payment_status"] == "closed"
    assert diagnose_aura_schema(flatten_aura_record(record))["ok"] is True


def test_never_infers_unpaid_from_empty_claims_summary():
    summary = summarize_claim_payments([])
    assert summary["paid_count"] == 0
    assert summary["unpaid_count"] == 0
    assert summary["unknown_count"] == 0
    assert summary["rollup"] == "unknown"


def test_payment_summary_counts_each_status():
    summary = summarize_claim_payments(
        [
            {"payment_status": "paid", "payment_checked_at": "2026-08-26T10:00:00Z"},
            {"payment_status": "unpaid", "payment_checked_at": "2026-08-26T11:00:00Z"},
            {"payment_status": "unknown"},
            {"payment_status": "current"},
            {"payment_status": "past_due"},
        ]
    )
    assert summary["paid_count"] == 1
    assert summary["unpaid_count"] == 1
    assert summary["unknown_count"] == 1
    assert summary["current_count"] == 1
    assert summary["past_due_count"] == 1
    assert summary["rollup"] == "unpaid"
    assert summary["payment_checked_at"] == "2026-08-26T11:00:00Z"


def test_rollup_does_not_collapse_paid_plus_unknown_to_paid():
    assert rollup_payment_status(["paid", "unknown"]) == "partial"
    assert rollup_payment_status(["paid", "paid"]) == "paid"
    assert rollup_payment_status(["current", "current"]) == "current"
    assert rollup_payment_status(["unpaid", "unknown"]) == "unpaid"
    assert rollup_payment_status(["unknown", "unknown"]) == "unknown"


def test_cache_cannot_carry_current_label_across_due_date():
    payload = {
        "payment_status": "current",
        "payment_due_date": "2026-08-26",
        "payment_evidence_code": EVIDENCE_CURRENT,
    }
    assert cache_crosses_due_boundary(payload, observed_on=date(2026, 8, 25)) is False
    assert cache_crosses_due_boundary(payload, observed_on=date(2026, 8, 26)) is True
    assert cache_crosses_due_boundary(payload, observed_on=date(2026, 8, 27)) is True


class _FakeResponse:
    def __init__(self, text="", status_code=200, content_type="application/json"):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.is_redirect = False

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"status {self.status_code}")
            err.response = self
            raise err


def _aura_json(record: dict) -> str:
    return json.dumps(
        {
            "actions": [
                {
                    "state": "SUCCESS",
                    "returnValue": {"record": record},
                    "error": [],
                }
            ]
        }
    )


def test_aura_client_loads_current_record(monkeypatch):
    client = MlrsAuraClient(timeout_sec=5)
    session = MagicMock()

    def _request(method, url, **kwargs):
        if method == "GET":
            return _FakeResponse(
                text='{"fwuid":"abcFWUID","APPLICATION@markup://siteforce:communityApp":"appHash"}',
                content_type="text/html",
            )
        return _FakeResponse(
            _aura_json(_record(Next_Payment_Due_Date__c="2027-09-01")),
        )

    session.request.side_effect = _request
    monkeypatch.setattr(client, "_session", session)

    out = payment_from_mlrs_aura(
        CASE_URL, client=client, observed_on=OBSERVED, expected_serial="UT101527746"
    )
    assert out["payment_status"] == "current"
    assert out["payment_evidence_code"] == EVIDENCE_CURRENT
    assert session.request.called


def test_aura_timeout_stays_unknown_not_unpaid(monkeypatch):
    client = MlrsAuraClient(timeout_sec=5)
    session = MagicMock()
    session.request.side_effect = requests.Timeout("timed out")
    monkeypatch.setattr(client, "_session", session)

    out = payment_from_mlrs_aura(CASE_URL, client=client, observed_on=OBSERVED)
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_TIMEOUT
    assert "timed out" in (out.get("payment_check_error") or "").lower()
    assert out["payment_message"] is None


def test_aura_upstream_failure_stays_unknown_not_unpaid(monkeypatch):
    client = MlrsAuraClient(timeout_sec=5)
    session = MagicMock()

    def _request(method, url, **kwargs):
        if method == "GET":
            return _FakeResponse(text='{"fwuid":"abcFWUID"}', content_type="text/html")
        raise requests.ConnectionError("upstream 502")

    session.request.side_effect = _request
    monkeypatch.setattr(client, "_session", session)

    out = payment_from_mlrs_aura(CASE_URL, client=client, observed_on=OBSERVED)
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_UPSTREAM
    assert out["payment_message"] is None


def test_invalid_case_url_is_unknown():
    out = payment_from_mlrs_aura("not-a-url")
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_INVALID_URL


def test_payment_from_mlrs_aura_blocks_foreign_host_without_http():
    out = payment_from_mlrs_aura("https://evil.example/s/blm-case/a02t000000593dSAAQ/x")
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_INVALID_URL
