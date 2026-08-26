"""Tests for sequential batch BLM actions on targets."""

import pytest

from mining_os.services.area_batch_actions import (
    MAX_BATCH_AREA_ACTIONS,
    batch_fetch_claim_records,
    batch_lr2000_geographic_report,
)


class TestBatchFetchClaimRecords:
    def test_empty_ids(self):
        out = batch_fetch_claim_records([])
        assert out["ok"] is False
        assert "error" in out

    def test_too_many_ids(self):
        out = batch_fetch_claim_records(list(range(MAX_BATCH_AREA_ACTIONS + 1)))
        assert out["ok"] is False
        assert str(MAX_BATCH_AREA_ACTIONS) in (out.get("error") or "")

    def test_dedupes_and_calls_fetch(self, monkeypatch):
        calls: list[int] = []
        account_ids: list[int | None] = []

        def fake_get_area(aid, account_id=None):
            account_ids.append(account_id)
            if aid in (1, 2):
                return {
                    "id": aid,
                    "name": f"Area {aid}",
                    "location_plss": "UT T1S R1E Sec 1",
                    "state_abbr": "UT",
                    "township": "1S",
                    "range": "1E",
                    "section": "1",
                }
            return None

        def fake_run(aid, account_id=None, progress_cb=None):
            calls.append(aid)
            return {
                "ok": True,
                "claims": [
                    {
                        "serial_number": "A1",
                        "payment_status": "paid",
                        "payment_evidence_code": "PAYMENT_RECORDED",
                        "payment_checked_at": "2026-08-26T12:00:00Z",
                    }
                ],
                "log": "ok",
                "error": None,
                "fetched_at": "t",
            }

        monkeypatch.setattr("mining_os.services.areas_of_focus.get_area", fake_get_area)
        monkeypatch.setattr(
            "mining_os.services.fetch_claim_records.run_fetch_claim_records_for_area_id",
            fake_run,
        )

        out = batch_fetch_claim_records([1, 1, 2, 99], account_id=42)
        assert out["ok"] is True
        assert out["processed"] == 3  # duplicate id 1 removed
        assert out["succeeded"] == 2
        assert out["failed"] == 1
        assert calls == [1, 2]
        assert all(a == 42 for a in account_ids)
        by_id = {r["id"]: r for r in out["results"]}
        assert by_id[1]["ok"] is True
        assert by_id[1]["claims_count"] == 1
        assert by_id[1]["paid_count"] == 1
        assert by_id[1]["unpaid_count"] == 0
        assert by_id[1]["unknown_count"] == 0
        assert by_id[1]["payment_checked_at"] == "2026-08-26T12:00:00Z"
        assert by_id[99]["ok"] is False
        assert "not found" in (by_id[99].get("error") or "").lower()


class TestBatchLr2000:
    def test_sequential_lr2000(self, monkeypatch):
        calls: list[int] = []

        def fake_get_area(aid):
            return {
                "id": aid,
                "name": "N",
                "location_plss": "x",
                "state_abbr": "UT",
                "township": "1S",
                "range": "1E",
                "section": "1",
            }

        def fake_lr2000(aid, area):
            calls.append(aid)
            return {"ok": True, "claims": [1, 2], "error": None}

        monkeypatch.setattr("mining_os.services.areas_of_focus.get_area", fake_get_area)
        monkeypatch.setattr(
            "mining_os.services.mlrs_geographic_index.run_lr2000_geographic_index_for_area",
            fake_lr2000,
        )

        out = batch_lr2000_geographic_report([5, 7])
        assert out["ok"] is True
        assert calls == [5, 7]
        assert out["succeeded"] == 2
        assert out["results"][0]["claims_count"] == 2
