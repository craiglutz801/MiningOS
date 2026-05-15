"""Tests for automation engine service — CRUD, filtering, action dispatch, email, execute_rule."""

import json
from unittest.mock import MagicMock, patch

import pytest

from mining_os.services.automation_engine import (
    ACTION_TYPES,
    OUTCOME_TYPES,
    FILTER_KEYS,
    MAX_TARGETS_CAP,
    FETCH_CLAIM_RECORDS_INCLUDE_EXISTING_KEY,
    _row_to_dict,
    _run_action_on_target,
    _filter_targets,
    _claim_status_already_set,
    _should_skip_fetch_claim_records,
    _send_outcome_email,
)


class TestConstants:
    def test_action_types_non_empty(self):
        assert len(ACTION_TYPES) >= 4
        assert "fetch_claim_records" in ACTION_TYPES
        assert "check_blm_status" in ACTION_TYPES

    def test_outcome_types_non_empty(self):
        assert len(OUTCOME_TYPES) >= 3
        assert "log_only" in OUTCOME_TYPES
        assert "email_on_change" in OUTCOME_TYPES

    def test_filter_keys(self):
        assert "priority" in FILTER_KEYS
        assert "mineral" in FILTER_KEYS


class TestRowToDict:
    def test_serializes_datetimes(self):
        from datetime import datetime, timezone
        row = {"created_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "name": "test"}
        d = _row_to_dict(row)
        assert isinstance(d["created_at"], str)
        assert "2024" in d["created_at"]

    def test_parses_json_string_filter_config(self):
        row = {"filter_config": '{"priority":"high"}'}
        d = _row_to_dict(row)
        assert isinstance(d["filter_config"], dict)
        assert d["filter_config"]["priority"] == "high"

    def test_parses_json_string_results(self):
        row = {"results": '[{"ok":true}]'}
        d = _row_to_dict(row)
        assert isinstance(d["results"], list)
        assert d["results"][0]["ok"] is True

    def test_handles_already_parsed(self):
        row = {"filter_config": {"x": 1}, "results": [1, 2]}
        d = _row_to_dict(row)
        assert d["filter_config"]["x"] == 1


class TestRunActionOnTarget:
    def test_unknown_action_type(self):
        res = _run_action_on_target("nonsense_action", {"id": 1})
        assert res["ok"] is False
        assert "Unknown" in (res.get("error") or "")

    @patch("mining_os.services.fetch_claim_records.run_fetch_claim_records_for_area_id")
    def test_fetch_claim_records_success(self, mock_fetch):
        mock_fetch.return_value = {"ok": True, "claims": [1, 2, 3, 4, 5]}
        area = {
            "id": 10, "name": "Test", "location_plss": "X", "state_abbr": "NV",
            "meridian": "M", "township": "T1S", "range": "R1E", "section": "1",
            "latitude": 38.0, "longitude": -117.0, "characteristics": {},
        }
        res = _run_action_on_target("fetch_claim_records", area)
        assert res["ok"] is True
        assert res["claims_count"] == 5
        mock_fetch.assert_called_once()

    @patch("mining_os.services.fetch_claim_records.run_fetch_claim_records_for_area_id")
    def test_fetch_claim_records_detects_change(self, mock_fetch):
        mock_fetch.return_value = {"ok": True, "claims": [1, 2, 3]}
        area = {
            "id": 10, "name": "Test", "location_plss": "X", "state_abbr": "NV",
            "characteristics": {"claim_records": {"claims": [1, 2]}},
        }
        res = _run_action_on_target("fetch_claim_records", area)
        assert res["ok"] is True
        assert res["changed"] is True

    @patch("mining_os.services.fetch_claim_records.run_fetch_claim_records_for_area_id")
    def test_fetch_claim_records_exception(self, mock_fetch):
        mock_fetch.return_value = {"ok": False, "error": "Boom", "claims": []}
        area = {"id": 1, "name": "T", "location_plss": "X", "characteristics": {}}
        res = _run_action_on_target("fetch_claim_records", area)
        assert res["ok"] is False
        assert "Boom" in res["error"]

    def test_check_blm_no_coords(self):
        area = {"id": 1, "name": "T", "latitude": None, "longitude": None}
        res = _run_action_on_target("check_blm_status", area)
        assert res["ok"] is False
        assert "coordinates" in (res.get("error") or "").lower()

    @patch("mining_os.services.blm_check.check_area_by_coords")
    def test_check_blm_status_changed(self, mock_blm):
        mock_blm.return_value = {"status": "paid"}
        area = {"id": 2, "name": "X", "latitude": 38.0, "longitude": -117.0, "status": "unpaid"}
        res = _run_action_on_target("check_blm_status", area)
        assert res["ok"] is True
        assert res["changed"] is True
        assert res["status"] == "paid"
        assert res["old_status"] == "unpaid"


class TestFilterTargets:
    @patch("mining_os.services.areas_of_focus.list_areas")
    def test_filter_with_priority(self, mock_list):
        mock_list.return_value = [
            {"id": 1, "name": "A", "priority": "monitoring_high"},
            {"id": 2, "name": "B", "priority": "monitoring_low"},
            {"id": 3, "name": "C", "priority": "monitoring_high"},
        ]
        result = _filter_targets({"priority": "monitoring_high"}, 10)
        assert len(result) == 2
        assert all(r["priority"] == "monitoring_high" for r in result)

    @patch("mining_os.services.areas_of_focus.list_areas")
    def test_filter_respects_max(self, mock_list):
        mock_list.return_value = [{"id": i, "name": f"N{i}", "priority": "x"} for i in range(20)]
        result = _filter_targets({}, 5)
        assert len(result) == 5

    @patch("mining_os.services.areas_of_focus.list_areas")
    def test_filter_passes_params(self, mock_list):
        mock_list.return_value = []
        _filter_targets({"mineral": "Gold", "state_abbr": "NV"}, 50)
        call_kwargs = mock_list.call_args
        assert call_kwargs.kwargs.get("mineral") == "Gold"
        assert call_kwargs.kwargs.get("state_abbr") == "NV"


class TestFetchClaimStatusSkip:
    def test_claim_status_already_set(self):
        assert _claim_status_already_set({"status": "paid"}) is True
        assert _claim_status_already_set({"status": "unpaid"}) is True
        assert _claim_status_already_set({"status": "unknown"}) is False

    def test_skip_fetch_claim_records_default(self):
        area = {"status": "paid"}
        assert _should_skip_fetch_claim_records(area, {}) is True

    def test_include_existing_claim_status_opt_in(self):
        area = {"status": "paid"}
        assert _should_skip_fetch_claim_records(
            area,
            {FETCH_CLAIM_RECORDS_INCLUDE_EXISTING_KEY: True},
        ) is False


class TestSendOutcomeEmail:
    @patch("mining_os.services.email_alerts.send_alert")
    @patch("mining_os.services.automation_engine.settings")
    def test_sends_email_with_changes(self, mock_settings, mock_send):
        mock_settings.ALERT_EMAIL = "test@example.com"
        mock_send.return_value = (True, None)

        rule = {"name": "Weekly check", "action_type": "check_blm_status"}
        results = [
            {"id": 1, "name": "Site A", "ok": True, "changed": True, "old_status": "unpaid", "status": "paid"},
            {"id": 2, "name": "Site B", "ok": True, "changed": False},
        ]
        ok = _send_outcome_email(rule, results, changes_found=1, targets_ok=2, targets_err=0)
        assert ok is True
        mock_send.assert_called_once()
        subject = mock_send.call_args[0][1]
        assert "Weekly check" in subject
        assert "1 change" in subject

    @patch("mining_os.services.automation_engine.settings")
    def test_no_email_without_alert_email(self, mock_settings):
        mock_settings.ALERT_EMAIL = ""
        rule = {"name": "R", "action_type": "x"}
        ok = _send_outcome_email(rule, [], 0, 0, 0)
        assert ok is False


class TestScheduler:
    def test_is_cron_due_matches(self):
        from datetime import datetime, timezone
        from mining_os.services.automation_scheduler import _is_cron_due
        now = datetime(2024, 6, 10, 8, 0, 0, tzinfo=timezone.utc)  # Mon 8:00 UTC
        assert _is_cron_due("0 8 * * 1", now) is True  # every Monday at 8am

    def test_is_cron_due_no_match(self):
        from datetime import datetime, timezone
        from mining_os.services.automation_scheduler import _is_cron_due
        now = datetime(2024, 6, 10, 9, 0, 0, tzinfo=timezone.utc)  # Mon 9:00 UTC
        assert _is_cron_due("0 8 * * 1", now) is False  # doesn't match 9am

    def test_is_cron_due_invalid_expression(self):
        from datetime import datetime, timezone
        from mining_os.services.automation_scheduler import _is_cron_due
        now = datetime(2024, 6, 10, 8, 0, 0, tzinfo=timezone.utc)
        assert _is_cron_due("bad cron expr", now) is False

    def test_scheduler_idempotent(self):
        from mining_os.services.automation_scheduler import is_running
        assert isinstance(is_running(), bool)
