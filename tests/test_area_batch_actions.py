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

        def fake_get_area(aid):
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

        def fake_fetch(aid, name, location_plss, **kwargs):
            calls.append(aid)
            return {"ok": True, "claims": [{"x": 1}], "log": "ok", "error": None, "fetched_at": "t"}

        monkeypatch.setattr("mining_os.services.areas_of_focus.get_area", fake_get_area)
        monkeypatch.setattr(
            "mining_os.services.fetch_claim_records.fetch_claim_records_for_area",
            fake_fetch,
        )

        out = batch_fetch_claim_records([1, 1, 2, 99])
        assert out["ok"] is True
        assert out["processed"] == 3  # duplicate id 1 removed
        assert out["succeeded"] == 2
        assert out["failed"] == 1
        assert calls == [1, 2]
        by_id = {r["id"]: r for r in out["results"]}
        assert by_id[1]["ok"] is True
        assert by_id[1]["claims_count"] == 1
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
