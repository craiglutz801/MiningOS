"""Unit tests for MLRS case-page maintenance fee detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mining_os.services import mlrs_case_payment as mcp


def test_http_detects_unpaid_banner():
    html = "<html><body><div>Maintenance fee payment was not received and may result in the closing of the claim.</div></body></html>"
    with patch("mining_os.services.mlrs_case_payment.requests.get") as mock_get:
        mock_get.return_value = MagicMock()
        mock_get.return_value.text = html
        mock_get.return_value.raise_for_status = MagicMock()
        out = mcp._payment_from_http("https://mlrs.blm.gov/s/blm-case/x/y")
    assert out["payment_status"] == "unpaid"
    assert "Maintenance fee payment was not received" in (out.get("payment_message") or "")


def test_http_unknown_when_no_banner():
    html = "<html><body><script>/* spa shell */</script></body></html>"
    with patch("mining_os.services.mlrs_case_payment.requests.get") as mock_get:
        mock_get.return_value = MagicMock()
        mock_get.return_value.text = html
        mock_get.return_value.raise_for_status = MagicMock()
        out = mcp._payment_from_http("https://mlrs.blm.gov/s/blm-case/x/y")
    assert out["payment_status"] == "unknown"


def test_ras_iframe_detects_unpaid():
    """report.cfm is a shell; unpaid text is inside the iframe (real BLM layout)."""
    wrapper = """<html><iframe id="dispReport" src="/iReport/RAS/1/?serial_number=UT101527746"></iframe></html>"""
    inner = "<html><body>Maintenance fee payment was not received</body></html>"

    class FakeResp:
        def __init__(self, text: str, url: str):
            self.text = text
            self.url = url

        def raise_for_status(self) -> None:
            return None

    class FakeSession:
        headers: dict = {}

        def get(self, url: str, timeout: float = 0, allow_redirects: bool = True):
            if "report.cfm" in url:
                return FakeResp(wrapper, "https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number=UT101527746")
            return FakeResp(inner, url)

    with patch("mining_os.services.mlrs_case_payment.requests.Session", FakeSession):
        out = mcp._payment_from_ras_http(
            "https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number=UT101527746",
            serial_number=None,
        )
    assert out["payment_status"] == "unpaid"
    assert out["payment_check_source"] == "ras_http_iframe"


def test_enrich_sets_unpaid_from_http(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    # Patches apply only in-process; default subprocess enrich would not see mocks.
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")

    html = "<html>Maintenance fee payment was not received</html>"
    with patch("mining_os.services.mlrs_case_payment.requests.get") as mock_get:
        mock_get.return_value = MagicMock()
        mock_get.return_value.text = html
        mock_get.return_value.raise_for_status = MagicMock()

        claims = [
            {
                "claim_name": "PEBBLE # 5",
                "serial_number": "UT101527746",
                "payment_status": "unknown",
                "case_page": "https://mlrs.blm.gov/s/blm-case/sf/UT101527746",
            }
        ]
        out = mcp.enrich_claims_from_mlrs_case_pages(claims)

    assert out[0]["payment_status"] == "unpaid"
    assert mock_get.called


def test_enrich_skips_when_already_unpaid(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    with patch("mining_os.services.mlrs_case_payment.requests.get") as mock_get:
        claims = [
            {
                "serial_number": "X",
                "payment_status": "unpaid",
                "payment_message": "existing",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a/b",
            }
        ]
        out = mcp.enrich_claims_from_mlrs_case_pages(claims)
    assert out[0]["payment_message"] == "existing"
    assert not mock_get.called
