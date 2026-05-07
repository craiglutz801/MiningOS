"""Unit tests for MLRS case-page maintenance fee detection."""

from __future__ import annotations

import time
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


def test_enrich_reuses_recent_cached_payment_result(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_CACHE_TTL_HOURS", "24")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mcp.prime_payment_cache(
        [
            {
                "serial_number": "UT-1",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a/b",
                "payment_status": "unpaid",
                "payment_message": "cached unpaid",
                "payment_check_source": "seed",
                "payment_checked_at": now_iso,
            }
        ],
        fetched_at=now_iso,
    )

    with patch("mining_os.services.mlrs_case_payment.requests.get") as mock_get:
        claims = [
            {
                "serial_number": "UT-1",
                "payment_status": "unknown",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a/b",
            }
        ]
        out = mcp.enrich_claims_from_mlrs_case_pages(claims)

    assert out[0]["payment_status"] == "unpaid"
    assert out[0]["payment_message"] == "cached unpaid"
    assert out[0]["payment_check_source"] == "seed_cache"
    assert not mock_get.called


def test_enrich_reports_progress(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()

    progress_events: list[dict[str, object]] = []

    def fake_http(_url: str, timeout: float = 28.0):
        return {
            "payment_status": "paid",
            "payment_message": None,
            "payment_check_source": "mlrs_case_http",
        }

    monkeypatch.setattr(mcp, "_payment_from_http", fake_http)

    claims = [
        {"serial_number": "A", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a/a"},
        {"serial_number": "B", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a/b"},
    ]
    out = mcp.enrich_claims_from_mlrs_case_pages(claims, progress_cb=progress_events.append)

    assert [c["payment_status"] for c in out] == ["paid", "paid"]
    assert any((evt.get("phase") == "payment_cache") for evt in progress_events)
    assert any((evt.get("phase") == "payment_enrich" and evt.get("current") == 2) for evt in progress_events)


def test_enrich_skips_large_batches_to_protect_service(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_MAX_CLAIMS", "1")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()

    progress_events: list[dict[str, object]] = []
    claims = [
        {"serial_number": "A", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a/a"},
        {"serial_number": "B", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a/b"},
    ]

    with patch("mining_os.services.mlrs_case_payment.requests.get") as mock_get:
        out = mcp.enrich_claims_from_mlrs_case_pages(claims, progress_cb=progress_events.append)

    assert [c["payment_status"] for c in out] == ["unknown", "unknown"]
    assert not mock_get.called
    assert any("Skipping payment-status browser checks" in str(evt.get("message")) for evt in progress_events)
