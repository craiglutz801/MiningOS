"""Unit tests for MLRS case-page maintenance fee detection."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from mining_os.services import mlrs_case_payment as mcp


@pytest.fixture(autouse=True)
def _stub_aura_truth_layer(monkeypatch):
    """Existing HTTP/Playwright tests should not hit the live Aura RPC."""
    monkeypatch.setattr(
        mcp,
        "payment_from_mlrs_aura",
        lambda case_url, client=None, observed_on=None, expected_serial=None: {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_source": "mlrs_case_aura",
            "payment_source_url": case_url,
            "payment_evidence_text": "Aura truth layer stubbed in unit test.",
            "payment_evidence_code": "CASE_RECORD_UNAVAILABLE",
        },
    )


def test_http_detects_unpaid_banner():
    html = "<html><body><div>Maintenance fee payment was not received and may result in the closing of the claim.</div></body></html>"
    fake = MagicMock()
    fake.text = html
    fake.raise_for_status = MagicMock()
    with patch("mining_os.services.mlrs_case_payment.request_approved_url", return_value=fake):
        out = mcp._payment_from_http("https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/y")
    assert out["payment_status"] == "unpaid"
    assert "Maintenance fee payment was not received" in (out.get("payment_message") or "")


def test_http_unknown_when_no_banner():
    html = "<html><body><script>/* spa shell */</script></body></html>"
    fake = MagicMock()
    fake.text = html
    fake.raise_for_status = MagicMock()
    with patch("mining_os.services.mlrs_case_payment.request_approved_url", return_value=fake):
        out = mcp._payment_from_http("https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/y")
    assert out["payment_status"] == "unknown"


def test_http_rejects_localhost_without_request():
    with patch("mining_os.services.mlrs_case_payment.request_approved_url") as mock_req:
        out = mcp._payment_from_http("https://127.0.0.1/s/blm-case/a02t000000593dSAAQ/y")
    assert out["payment_status"] == "unknown"
    assert out["payment_evidence_code"] == "INVALID_CASE_URL"
    assert not mock_req.called


def test_body_is_shellish_for_sf_bootstrap():
    shell = "mlrs virtual public room loading css error sorry to interrupt"
    assert mcp._body_is_shellish(shell) is True
    loaded = (
        "blm case serial number case disposition case customers related records "
        "active claim without overdue banner"
    )
    assert mcp._body_is_shellish(loaded) is False
    assert mcp._body_looks_like_loaded_case(loaded) is True


def test_selenium_does_not_mark_paid_on_shell():
    """Unpaid phrase rule unchanged; shell pages must stay unknown (not false paid)."""
    driver = MagicMock()
    driver.page_source = "<html><title>MLRS Virtual Public Room</title><body>Loading CSS Error</body></html>"
    driver.current_url = "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/x"

    with patch("mining_os.services.mlrs_case_payment.time.sleep", return_value=None):
        with patch("mining_os.services.mlrs_case_payment.time.monotonic", side_effect=[0.0, 0.0, 100.0, 100.0]):
            out = mcp._payment_from_selenium_driver(
                driver, "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/x", timeout=5
            )

    assert out["payment_status"] == "unknown"
    assert out.get("payment_check_source") == "mlrs_case_selenium"


def test_selenium_does_not_infer_paid_when_case_loaded_without_banner():
    loaded = (
        "<html><body>BLM Case Serial Number Case Disposition "
        "Case Customers Related Records Active</body></html>"
    )
    driver = MagicMock()
    driver.page_source = loaded
    driver.current_url = "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT1"
    with patch("mining_os.services.mlrs_case_payment.time.sleep", return_value=None):
        out = mcp._payment_from_selenium_driver(
            driver, "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT1", timeout=5
        )
    assert out["payment_status"] == "unknown"
    assert out.get("payment_evidence_code") == "PAGE_LOADED_NO_WARNING"


def test_selenium_marks_unpaid_when_banner_present():
    html = (
        "<html><body>BLM Case Serial Number Case Disposition "
        "Maintenance fee payment was not received and may result in the closing of the claim."
        "</body></html>"
    )
    driver = MagicMock()
    driver.page_source = html
    driver.current_url = "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/x"
    with patch("mining_os.services.mlrs_case_payment.time.sleep", return_value=None):
        out = mcp._payment_from_selenium_driver(
            driver, "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/x", timeout=5
        )
    assert out["payment_status"] == "unpaid"
    assert "Maintenance fee payment was not received" in (out.get("payment_message") or "")


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

    with patch("mining_os.services.mlrs_case_payment.request_approved_ras_url") as mock_get:
        mock_get.side_effect = [
            FakeResp(wrapper, "https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number=UT101527746"),
            FakeResp(inner, "https://reports.blm.gov/iReport/RAS/1/?serial_number=UT101527746"),
        ]
        out = mcp._payment_from_ras_http(
            "https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number=UT101527746",
            serial_number=None,
        )
    assert out["payment_status"] == "unpaid"
    assert out["payment_check_source"] == "ras_http_iframe"


def test_loaded_case_heuristic_detects_real_detail_page():
    body = "BLM Case UT101426602 Serial Number Case Disposition Related Records Case Customers"
    assert mcp._body_looks_like_loaded_case(body.lower()) is True


def test_enrich_sets_unpaid_from_http(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    # Patches apply only in-process; default subprocess enrich would not see mocks.
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")

    html = "<html>Maintenance fee payment was not received</html>"
    fake = MagicMock()
    fake.text = html
    fake.raise_for_status = MagicMock()
    with patch("mining_os.services.mlrs_case_payment.request_approved_url", return_value=fake) as mock_get:
        claims = [
            {
                "claim_name": "PEBBLE # 5",
                "serial_number": "UT101527746",
                "payment_status": "unknown",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT101527746",
            }
        ]
        out = mcp.enrich_claims_from_mlrs_case_pages(claims)

    assert out[0]["payment_status"] == "unpaid"
    assert mock_get.called


def test_enrich_rechecks_legacy_unpaid_without_evidence_code(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_HEADLESS", "0")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()
    monkeypatch.setattr(
        mcp,
        "payment_from_mlrs_aura",
        lambda case_url, client=None, observed_on=None, expected_serial=None: {
            "payment_status": "current",
            "payment_check_source": "mlrs_case_aura",
            "payment_source_url": case_url,
            "payment_evidence_code": "NEXT_PAYMENT_DUE_CURRENT",
            "payment_due_date": "2027-09-01",
        },
    )
    fake_http = MagicMock()
    fake_http.text = "<html>no banner</html>"
    fake_http.raise_for_status = MagicMock()
    with patch("mining_os.services.mlrs_case_payment.request_approved_url", return_value=fake_http) as mock_get:
        claims = [
            {
                "serial_number": "X",
                "payment_status": "unpaid",
                "payment_message": "existing",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
            }
        ]
        out = mcp.enrich_claims_from_mlrs_case_pages(claims)
    assert mock_get.called
    assert out[0]["payment_status"] == "current"
    assert out[0]["payment_evidence_code"] == "NEXT_PAYMENT_DUE_CURRENT"


def test_enrich_skips_when_authoritative_unpaid(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    with patch("mining_os.services.mlrs_case_payment.requests.get") as mock_get:
        claims = [
            {
                "serial_number": "X",
                "payment_status": "unpaid",
                "payment_message": "existing",
                "payment_evidence_code": "NONPAYMENT_WARNING",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
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
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
                "payment_status": "unpaid",
                "payment_message": "cached unpaid",
                "payment_check_source": "seed",
                "payment_evidence_code": "NONPAYMENT_WARNING",
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
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
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

    def fake_aura(case_url, client=None, observed_on=None, expected_serial=None):
        return {
            "payment_status": "current",
            "payment_message": None,
            "payment_check_source": "mlrs_case_aura",
            "payment_source_url": case_url,
            "payment_checked_at": "2026-08-26T12:00:00Z",
            "payment_evidence_text": "Next payment due 2027-09-01 is a compliance deadline, not a receipt.",
            "payment_evidence_code": "NEXT_PAYMENT_DUE_CURRENT",
            "payment_due_date": "2027-09-01",
        }

    monkeypatch.setattr(mcp, "payment_from_mlrs_aura", fake_aura)

    claims = [
        {"serial_number": "A", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/a"},
        {"serial_number": "B", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b"},
    ]
    out = mcp.enrich_claims_from_mlrs_case_pages(claims, progress_cb=progress_events.append)

    assert [c["payment_status"] for c in out] == ["current", "current"]
    assert any((evt.get("phase") == "payment_cache") for evt in progress_events)
    assert any((evt.get("phase") == "payment_enrich" and evt.get("current") == 2) for evt in progress_events)


def test_enrich_processes_large_batches_in_sequential_chunks(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_MAX_CLAIMS", "1")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_LARGE_BATCH_CHUNK_SIZE", "1")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()

    progress_events: list[dict[str, object]] = []
    claims = [
        {"serial_number": "A", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/a"},
        {"serial_number": "B", "payment_status": "unknown", "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b"},
    ]

    seen_batches: list[list[str]] = []

    def fake_chunk(batch, chunk_id=0, progress_cb=None):
        seen_batches.append([str(c.get("serial_number")) for c in batch])
        enriched = []
        for i, claim in enumerate(batch, start=1):
            row = dict(claim)
            row["payment_status"] = "paid"
            row["payment_check_source"] = "test_chunk"
            enriched.append(row)
            if progress_cb:
                progress_cb({"done": i, "total": len(batch), "message": f"chunk {chunk_id} row {i}"})
        return enriched

    monkeypatch.setattr(mcp, "_run_enrich_subprocess_chunk", fake_chunk)

    out = mcp.enrich_claims_from_mlrs_case_pages(claims, progress_cb=progress_events.append)

    assert [c["payment_status"] for c in out] == ["paid", "paid"]
    assert seen_batches == [["A"], ["B"]]
    assert any("Large batch detected" in str(evt.get("message")) for evt in progress_events)
    enrich_events = [evt for evt in progress_events if evt.get("phase") == "payment_enrich" and evt.get("current")]
    assert any(evt.get("message") == "Checked 1 of 2 claim page(s)…" for evt in enrich_events)
    assert any(evt.get("message") == "Checked 2 of 2 claim page(s)…" for evt in enrich_events)


def test_check_payment_for_url_uses_enriched_row(monkeypatch):
    def fake_enrich(rows, progress_cb=None):
        return [
            {
                **rows[0],
                "payment_status": "paid",
                "payment_check_source": "mlrs_case_playwright",
            }
        ]

    monkeypatch.setattr(mcp, "enrich_claims_from_mlrs_case_pages", fake_enrich)
    out = mcp.check_payment_for_url("https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/y")
    assert out["payment_status"] == "paid"
    assert out["payment_check_source"] == "mlrs_case_playwright"


def test_merge_payment_fields_clears_stale_error_on_paid():
    dst = {"payment_check_error": "old error", "payment_status": "unknown"}
    src = {
        "payment_status": "paid",
        "payment_check_source": "mlrs_case_playwright",
        "payment_evidence_code": "PAYMENT_RECORDED",
    }
    mcp._merge_payment_fields(dst, src)
    assert dst["payment_status"] == "paid"
    assert "payment_check_error" not in dst


def test_enrich_uses_aura_truth_layer_current(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_HEADLESS", "0")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()
    monkeypatch.setattr(
        mcp,
        "payment_from_mlrs_aura",
        lambda case_url, client=None, observed_on=None, expected_serial=None: {
            "payment_status": "current",
            "payment_message": None,
            "payment_check_source": "mlrs_case_aura",
            "payment_source_url": case_url,
            "payment_checked_at": "2026-08-26T12:00:00Z",
            "payment_evidence_text": "Next payment due 2027-09-01 is a compliance deadline, not a receipt.",
            "payment_evidence_code": "NEXT_PAYMENT_DUE_CURRENT",
            "payment_due_date": "2027-09-01",
        },
    )
    fake_http = MagicMock()
    fake_http.text = "<html>no banner</html>"
    fake_http.raise_for_status = MagicMock()
    with patch("mining_os.services.mlrs_case_payment.request_approved_url", return_value=fake_http):
        out = mcp.enrich_claims_from_mlrs_case_pages(
            [
                {
                    "serial_number": "UT1",
                    "payment_status": "unknown",
                    "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT1",
                }
            ]
        )
    assert out[0]["payment_status"] == "current"
    assert out[0]["payment_evidence_code"] == "NEXT_PAYMENT_DUE_CURRENT"
    assert out[0]["payment_checked_at"] == "2026-08-26T12:00:00Z"


def test_enrich_skips_selenium_on_paas(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_HEADLESS", "1")
    selenium_launches: list[str] = []

    def _no_launch(timeout=None):
        selenium_launches.append("launched")
        return None, "selenium must not run on PaaS"

    monkeypatch.setattr(mcp, "_launch_selenium_driver", _no_launch)
    monkeypatch.setattr(
        mcp,
        "_payment_from_http",
        lambda url, timeout=None: {"payment_status": "unknown", "payment_message": None},
    )

    out = mcp._enrich_claims_inproc(
        [
            {
                "serial_number": "UT1",
                "payment_status": "unknown",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT1",
            }
        ]
    )
    assert selenium_launches == []
    assert out[0]["payment_status"] == "unknown"
    assert out[0].get("payment_check_source") != "mlrs_case_selenium"


def test_cache_current_is_not_reused_after_due_date(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_CACHE_TTL_HOURS", "24")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_HEADLESS", "0")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mcp.prime_payment_cache(
        [
            {
                "serial_number": "UT-1",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
                "payment_status": "current",
                "payment_due_date": "1999-01-01",
                "payment_evidence_code": "NEXT_PAYMENT_DUE_CURRENT",
                "payment_check_source": "seed",
                "payment_checked_at": now_iso,
            }
        ],
        fetched_at=now_iso,
    )
    monkeypatch.setattr(
        mcp,
        "payment_from_mlrs_aura",
        lambda case_url, client=None, observed_on=None, expected_serial=None: {
            "payment_status": "past_due",
            "payment_check_source": "mlrs_case_aura",
            "payment_source_url": case_url,
            "payment_due_date": "1999-01-01",
            "payment_evidence_code": "NEXT_PAYMENT_DUE_PAST",
        },
    )
    with patch(
        "mining_os.services.mlrs_case_payment._payment_from_http",
        return_value={"payment_status": "unknown", "payment_message": None},
    ):
        out = mcp.enrich_claims_from_mlrs_case_pages(
            [
                {
                    "serial_number": "UT-1",
                    "payment_status": "unknown",
                    "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
                }
            ]
        )
    assert out[0]["payment_status"] == "past_due"
    assert out[0]["payment_check_source"] == "mlrs_case_aura"


def test_subprocess_timeout_marks_remaining_unknown(monkeypatch):
    claims = [
        {
            "serial_number": "UT1",
            "payment_status": "unknown",
            "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT1",
        }
    ]
    out = mcp._mark_claims_timeout_unknown(claims)
    assert out[0]["payment_status"] == "unknown"
    assert out[0]["payment_evidence_code"] == "TIMEOUT"


def test_legacy_paid_cache_is_not_reused(monkeypatch):
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_SELENIUM", "0")
    monkeypatch.setenv("MINING_OS_MLRS_ENRICH_INPROC", "1")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_CACHE_TTL_HOURS", "24")
    monkeypatch.setenv("MINING_OS_MLRS_PAYMENT_HEADLESS", "0")
    with mcp._PAYMENT_CACHE_LOCK:
        mcp._PAYMENT_CACHE.clear()

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    seeded = mcp.prime_payment_cache(
        [
            {
                "serial_number": "UT-1",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
                "payment_status": "paid",
                "payment_evidence_code": "NEXT_PAYMENT_DUE_CURRENT",
                "payment_check_source": "seed",
                "payment_checked_at": now_iso,
            },
            {
                "serial_number": "UT-2",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/c",
                "payment_status": "unpaid",
                "payment_evidence_code": "NEXT_PAYMENT_DUE_OVERDUE",
                "payment_check_source": "seed",
                "payment_checked_at": now_iso,
            },
            {
                "serial_number": "UT-3",
                "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/d",
                "payment_status": "paid",
                "payment_check_source": "seed",
                "payment_checked_at": now_iso,
            },
        ],
        fetched_at=now_iso,
    )
    assert seeded == 0

    monkeypatch.setattr(
        mcp,
        "payment_from_mlrs_aura",
        lambda case_url, client=None, observed_on=None, expected_serial=None: {
            "payment_status": "current",
            "payment_check_source": "mlrs_case_aura",
            "payment_source_url": case_url,
            "payment_evidence_code": "NEXT_PAYMENT_DUE_CURRENT",
            "payment_due_date": "2027-09-01",
        },
    )
    fake_http = MagicMock()
    fake_http.text = "<html>no banner</html>"
    fake_http.raise_for_status = MagicMock()
    with patch("mining_os.services.mlrs_case_payment.request_approved_url", return_value=fake_http):
        out = mcp.enrich_claims_from_mlrs_case_pages(
            [
                {
                    "serial_number": "UT-1",
                    "payment_status": "paid",
                    "payment_evidence_code": "NEXT_PAYMENT_DUE_CURRENT",
                    "case_page": "https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/b",
                }
            ]
        )
    assert out[0]["payment_status"] == "current"
    assert out[0]["payment_check_source"] == "mlrs_case_aura"
    assert out[0]["payment_evidence_code"] == "NEXT_PAYMENT_DUE_CURRENT"
