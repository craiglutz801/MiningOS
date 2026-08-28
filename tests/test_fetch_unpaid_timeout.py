"""Bulk Fetch unpaid: scaled timeout + keep partial Paid/Unpaid on kill."""

from __future__ import annotations

from mining_os.active_mine_intel import jobs
from mining_os.active_mine_intel.claim_rollup import rollup_from_claims


def test_timeout_for_claim_count_scales_and_caps():
    assert jobs.timeout_for_claim_count(None, base=360) == 360
    assert jobs.timeout_for_claim_count(0, base=360) == 360
    assert jobs.timeout_for_claim_count(10, base=360) == 360  # 20s * 10 < 6 min
    assert jobs.timeout_for_claim_count(30, base=360) == 600
    assert jobs.timeout_for_claim_count(56, base=360) == 1120
    assert jobs.timeout_for_claim_count(200, base=360) == 45 * 60


def test_claim_rollup_paid_unpaid_unknown_rules_unchanged():
    total, unpaid, paid, unknown, rollup = rollup_from_claims(
        [
            {"payment_status": "paid"},
            {"payment_status": "unpaid"},
            {"payment_status": "unknown"},
            {"payment_status": "unknown", "payment_check_error": "timed_out"},
        ]
    )
    assert (total, unpaid, paid, unknown) == (4, 1, 1, 2)
    assert rollup == "unpaid"


def test_finalize_timed_out_keeps_partial_payment(monkeypatch):
    stored = {
        "characteristics": {
            "claim_records": {
                "fetched_at": "2026-08-28T00:00:00Z",
                "log": "[checkpoint] Saved ArcGIS claims before payment enrichment.",
                "query_method": "arcgis",
                "payment_enrichment": "partial",
                "claims": [
                    {
                        "serial_number": "A",
                        "payment_status": "paid",
                        "payment_checked_at": "t",
                    },
                    {
                        "serial_number": "B",
                        "payment_status": "unpaid",
                        "payment_checked_at": "t",
                    },
                    {"serial_number": "C", "payment_status": "unknown"},
                    {
                        "serial_number": "D",
                        "payment_status": "unknown",
                        "payment_checked_at": "t",
                        "payment_check_error": "mlrs case page did not finish loading",
                    },
                ],
            }
        }
    }
    merged: dict = {}

    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.get_area",
        lambda *a, **k: stored,
    )

    def fake_merge(area_id, updates, **kwargs):
        merged.update(updates)
        return True

    monkeypatch.setattr(
        "mining_os.services.areas_of_focus.merge_area_characteristics",
        fake_merge,
    )
    monkeypatch.setattr(jobs, "apply_claim_rollup_for_area", lambda *a, **k: None)

    out = jobs.finalize_timed_out_area_fetch(42, 7)
    claims = out["claims"]
    by_serial = {c["serial_number"]: c for c in claims}
    assert by_serial["A"]["payment_status"] == "paid"
    assert "timed_out" not in str(by_serial["A"].get("payment_check_error") or "")
    assert by_serial["B"]["payment_status"] == "unpaid"
    assert by_serial["C"]["payment_status"] == "unknown"
    assert by_serial["C"]["payment_check_error"] == "timed_out"
    # Already scraped unknown (page did not load) is not overwritten.
    assert by_serial["D"]["payment_check_error"] != "timed_out"
    cr = merged["claim_records"]
    assert cr["payment_enrichment"] == "timed_out"
    assert out["stamped"] == 1


def test_progress_payload_checking_payment_vs_timeout():
    checking = jobs._progress_payload(
        mine_name="Gold Mine",
        index=2,
        total=5,
        area_id=99,
        succeeded=1,
        failed=0,
        phase="start",
        elapsed_sec=40,
        timeout_sec=1120,
        payment_current=12,
        payment_total=56,
        payment_enrichment="partial",
    )
    assert "Checking payment 12/56 on \"Gold Mine\"" in checking["progress_message"]
    assert "1120s cap" in checking["progress_message"]

    timed = jobs._progress_payload(
        mine_name="Gold Mine",
        index=2,
        total=5,
        area_id=99,
        succeeded=1,
        failed=1,
        phase="timed_out",
        timed_out=True,
    )
    assert "Saved ArcGIS claims, scrape timed out on \"Gold Mine\"" in timed["progress_message"]
    assert timed["timed_out"] is True


def test_run_fetch_timeout_returns_checkpoint_claims(monkeypatch):
    class Stuck:
        pid = 1234

        def start(self):
            return None

        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

        def terminate(self):
            return None

        def kill(self):
            return None

    class Ctx:
        def Process(self, **kwargs):
            return Stuck()

    calls = {"n": 0}

    def monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 10_000.0

    monkeypatch.setattr(jobs.mp, "get_context", lambda _name: Ctx())
    monkeypatch.setattr(jobs.time, "monotonic", monotonic)
    monkeypatch.setattr(jobs, "_terminate_fetch_process", lambda proc: None)
    monkeypatch.setattr(
        jobs,
        "peek_area_claim_progress",
        lambda *a, **k: {
            "claims": [{"payment_status": "paid"}],
            "claim_count": 30,
            "payment_enrichment": "partial",
            "payment_progress": {"current": 8, "total": 30},
        },
    )
    monkeypatch.setattr(
        jobs,
        "finalize_timed_out_area_fetch",
        lambda area_id, account_id: {
            "claims": [
                {"payment_status": "paid"},
                {"payment_status": "unknown", "payment_check_error": "timed_out"},
            ],
            "stamped": 1,
            "claim_count": 2,
            "payment_progress": {"current": 1, "total": 2},
        },
    )

    out = jobs._run_fetch_area_with_timeout(9, 1, timeout_sec=60)
    assert out["timed_out"] is True
    assert out["claims"][0]["payment_status"] == "paid"
    assert out["claims"][1]["payment_check_error"] == "timed_out"
    assert "partial Paid/Unpaid" in (out.get("error") or "")
