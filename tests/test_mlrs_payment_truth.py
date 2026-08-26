"""Focused tests for the production MLRS payment-status truth layer.

Uses the public BLM MLRS case record (Aura DetailController.getRecord) and
classifies only from Next_Payment_Due_Date__c. Missing/failed data stays unknown.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import requests

from mining_os.services.mlrs_payment_truth import (
    EVIDENCE_MISSING_DATE,
    EVIDENCE_NO_RECORD,
    EVIDENCE_PAID,
    EVIDENCE_TIMEOUT,
    EVIDENCE_UNPAID,
    EVIDENCE_UPSTREAM,
    MlrsAuraClient,
    extract_salesforce_id,
    interpret_case_record,
    payment_from_mlrs_aura,
    summarize_claim_payments,
)


CASE_URL = "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT101527746"
OBSERVED = date(2026, 8, 26)


def test_extracts_salesforce_id_from_case_url():
    assert extract_salesforce_id(CASE_URL) == "a02t000000593dSAAQ"
    assert extract_salesforce_id("https://mlrs.blm.gov/s/research-map") is None
    assert extract_salesforce_id("") is None


def test_interpret_paid_when_due_date_is_in_the_future():
    out = interpret_case_record(
        {
            "Next_Payment_Due_Date__c": "2027-09-01",
            "Serial_Number__c": "UT101407602",
            "Case_Status__c": "Active",
        },
        source_url=CASE_URL,
        observed_on=OBSERVED,
        checked_at="2026-08-26T12:00:00Z",
    )
    assert out["payment_status"] == "paid"
    assert out["payment_evidence_code"] == EVIDENCE_PAID
    assert out["payment_source_url"] == CASE_URL
    assert out["payment_checked_at"] == "2026-08-26T12:00:00Z"
    assert "2027-09-01" in (out["payment_evidence_text"] or "")
    assert out["payment_message"] is None


def test_interpret_unpaid_when_due_date_is_on_or_before_observation_date():
    out = interpret_case_record(
        {
            "Next_Payment_Due_Date__c": "2024-09-03",
            "Serial_Number__c": "UT101527746",
            "Case_Status__c": "Closed",
        },
        source_url=CASE_URL,
        observed_on=OBSERVED,
        checked_at="2026-08-26T12:00:00Z",
    )
    assert out["payment_status"] == "unpaid"
    assert out["payment_evidence_code"] == EVIDENCE_UNPAID
    assert "Maintenance fee payment was not received" in (out["payment_message"] or "")
    assert "2024-09-03" in (out["payment_evidence_text"] or "")


def test_interpret_unpaid_on_the_due_date_itself():
    out = interpret_case_record(
        {"Next_Payment_Due_Date__c": "2026-08-26"},
        source_url=CASE_URL,
        observed_on=OBSERVED,
    )
    assert out["payment_status"] == "unpaid"
    assert out["payment_evidence_code"] == EVIDENCE_UNPAID


def test_interpret_unknown_when_due_date_missing():
    out = interpret_case_record(
        {"Serial_Number__c": "UT1", "Case_Status__c": "Active"},
        source_url=CASE_URL,
        observed_on=OBSERVED,
    )
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_MISSING_DATE
    assert out["payment_message"] is None


def test_interpret_unknown_when_record_missing():
    out = interpret_case_record(None, source_url=CASE_URL, observed_on=OBSERVED)
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_NO_RECORD


def test_never_infers_unpaid_from_empty_claims_summary():
    summary = summarize_claim_payments([])
    assert summary == {
        "paid_count": 0,
        "unpaid_count": 0,
        "unknown_count": 0,
        "payment_checked_at": None,
    }


def test_payment_summary_counts_each_status():
    summary = summarize_claim_payments(
        [
            {"payment_status": "paid", "payment_checked_at": "2026-08-26T10:00:00Z"},
            {"payment_status": "unpaid", "payment_checked_at": "2026-08-26T11:00:00Z"},
            {"payment_status": "unknown"},
        ]
    )
    assert summary["paid_count"] == 1
    assert summary["unpaid_count"] == 1
    assert summary["unknown_count"] == 1
    assert summary["payment_checked_at"] == "2026-08-26T11:00:00Z"


class _FakeResponse:
    def __init__(self, text="", status_code=200, content_type="application/json"):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"status {self.status_code}")
            err.response = self
            raise err


def _aura_json(record: dict) -> str:
    import json

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


def test_aura_client_loads_paid_record(monkeypatch):
    client = MlrsAuraClient(timeout_sec=5)
    session = MagicMock()
    session.get.return_value = _FakeResponse(
        text='{"fwuid":"abcFWUID","APPLICATION@markup://siteforce:communityApp":"appHash"}',
        content_type="text/html",
    )
    session.post.return_value = _FakeResponse(
        _aura_json({"Next_Payment_Due_Date__c": "2027-09-01", "Serial_Number__c": "UT1"}),
    )
    monkeypatch.setattr(client, "_session", session)

    out = payment_from_mlrs_aura(CASE_URL, client=client, observed_on=OBSERVED)
    assert out["payment_status"] == "paid"
    assert out["payment_evidence_code"] == EVIDENCE_PAID
    assert session.get.called
    assert session.post.called


def test_aura_timeout_stays_unknown_not_unpaid(monkeypatch):
    client = MlrsAuraClient(timeout_sec=5)
    session = MagicMock()
    session.get.side_effect = requests.Timeout("timed out")
    monkeypatch.setattr(client, "_session", session)

    out = payment_from_mlrs_aura(CASE_URL, client=client, observed_on=OBSERVED)
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_TIMEOUT
    assert "timed out" in (out.get("payment_check_error") or "").lower()
    assert out["payment_message"] is None


def test_aura_upstream_failure_stays_unknown_not_unpaid(monkeypatch):
    client = MlrsAuraClient(timeout_sec=5)
    session = MagicMock()
    session.get.return_value = _FakeResponse(
        text='{"fwuid":"abcFWUID"}',
        content_type="text/html",
    )
    session.post.side_effect = requests.ConnectionError("upstream 502")
    monkeypatch.setattr(client, "_session", session)

    out = payment_from_mlrs_aura(CASE_URL, client=client, observed_on=OBSERVED)
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == EVIDENCE_UPSTREAM
    assert out["payment_message"] is None


def test_invalid_case_url_is_unknown():
    out = payment_from_mlrs_aura("not-a-url")
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == "INVALID_CASE_URL"
