"""Tests for advanced Target search parsing (single values and ranges)."""

from mining_os.services.areas_of_focus import _normalize_plss_filter_spec


class TestAdvancedSearchRanges:
    def test_township_range_is_normalized(self):
        spec = _normalize_plss_filter_spec("22S-24S", "township")
        assert spec is not None
        assert spec["mode"] == "range"
        assert spec["direction"] == "S"
        assert spec["start_num"] < spec["end_num"]

    def test_range_range_is_normalized(self):
        spec = _normalize_plss_filter_spec("R14E - R18E", "range")
        assert spec is not None
        assert spec["mode"] == "range"
        assert spec["direction"] == "E"
        assert spec["start_num"] < spec["end_num"]

    def test_sector_range_is_normalized(self):
        spec = _normalize_plss_filter_spec("5-12", "section")
        assert spec == {
            "mode": "range",
            "kind": "section",
            "start": "005",
            "end": "012",
            "direction": None,
            "start_num": 5,
            "end_num": 12,
        }

    def test_reversed_range_is_auto_sorted(self):
        spec = _normalize_plss_filter_spec("24S-22S", "township")
        assert spec is not None
        assert spec["mode"] == "range"
        assert spec["start_num"] < spec["end_num"]

    def test_mismatched_direction_range_is_rejected(self):
        assert _normalize_plss_filter_spec("22S-24N", "township") is None

    def test_single_value_still_works(self):
        spec = _normalize_plss_filter_spec("12S", "township")
        assert spec == {"mode": "single", "kind": "township", "value": "0120S"}
