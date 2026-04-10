"""Unit tests for PLSS AI preview/apply guardrails (no live OpenAI or web)."""

import pytest

from mining_os.services import plss_ai_lookup as pal


class TestPreviewLimits:
    def test_preview_rejects_too_many_ids(self, monkeypatch):
        monkeypatch.setattr(pal.settings, "OPENAI_API_KEY", "sk-test-not-used")
        ids = list(range(pal.MAX_PREVIEW_IDS + 1))
        out = pal.lookup_plss_for_target_ids(ids, dry_run=True)
        assert out["ok"] is False
        assert out.get("error")
        assert "40" in (out["error"] or "") or str(pal.MAX_PREVIEW_IDS) in (out["error"] or "")

    def test_no_key_returns_error(self, monkeypatch):
        monkeypatch.setattr(pal.settings, "OPENAI_API_KEY", "")
        out = pal.lookup_plss_for_target_ids([1], dry_run=True)
        assert out["ok"] is False
        assert "OPENAI_API_KEY" in (out.get("error") or "")


class TestApplyProposals:
    def test_empty_items(self):
        out = pal.apply_plss_ai_proposals([])
        assert out["ok"] is True
        assert out["updated"] == 0

    def test_too_many_items(self):
        items = [{"id": i, "plss": "T1N R1E Sec1"} for i in range(pal.MAX_APPLY_ITEMS + 1)]
        out = pal.apply_plss_ai_proposals(items)
        assert out["ok"] is False
        assert out.get("error")

    def test_apply_calls_persist(self, monkeypatch):
        from mining_os.services.areas_of_focus import ApplyPlssLookupResult

        monkeypatch.setattr(
            pal,
            "get_area",
            lambda aid: {
                "id": aid,
                "name": "Test",
                "state_abbr": "UT",
                "plss_normalized": None,
                "validity_notes": "",
            },
        )
        calls: list[dict] = []

        def fake_apply(area_id, **kwargs):
            calls.append({"id": area_id, **kwargs})
            return ApplyPlssLookupResult(True)

        monkeypatch.setattr(pal, "apply_plss_lookup_result", fake_apply)

        out = pal.apply_plss_ai_proposals(
            [
                {
                    "id": 42,
                    "plss": "T12N R5E Sec14",
                    "township": "12N",
                    "range": "5E",
                    "section": "14",
                    "notes_append": "[test]",
                }
            ]
        )
        assert out["ok"] is True
        assert out["updated"] == 1
        assert len(calls) == 1
        assert calls[0]["id"] == 42
        assert calls[0]["location_plss"] == "T12N R5E Sec14"
        assert calls[0]["notes_append"] == "[test]"

    def test_apply_skips_when_already_normalized(self, monkeypatch):
        monkeypatch.setattr(
            pal,
            "get_area",
            lambda aid: {
                "id": aid,
                "name": "Test",
                "state_abbr": "UT",
                "plss_normalized": "UT 0120N 0050E 014",
                "validity_notes": "",
            },
        )

        def boom(*args, **kwargs):
            raise AssertionError("apply_plss_lookup_result should not be called")

        monkeypatch.setattr(pal, "apply_plss_lookup_result", boom)

        out = pal.apply_plss_ai_proposals([{"id": 1, "plss": "T12N R5E Sec14"}])
        assert out["ok"] is True
        assert out["updated"] == 0
        assert any(r.get("kind") == "skipped_has_normalized_plss" for r in (out.get("results") or []))
