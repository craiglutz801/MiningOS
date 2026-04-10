"""Tests for flexible PLSS parsing in blm_plss.parse_plss_string."""

import pytest
from mining_os.services.blm_plss import parse_plss_string


def _assert_result(parsed, state, township, range_val, section=None):
    assert parsed is not None
    assert parsed["state"] == state
    assert parsed["township"] == township
    assert parsed["range"] == range_val
    assert parsed["section"] == section


class TestPLSSStandardFormats:
    """Standard T-R-S formats with explicit direction markers."""

    def test_township_range_section_with_labels(self):
        _assert_result(parse_plss_string("T28S R11W S18"), "UT", "0280S", "0110W", "018")

    def test_compact_with_spaces(self):
        _assert_result(parse_plss_string("12S 18W 10"), "UT", "0120S", "0180W", "010")

    def test_compact_concat(self):
        _assert_result(parse_plss_string("12S18W10"), "UT", "0120S", "0180W", "010")

    def test_north_east(self):
        _assert_result(parse_plss_string("12N 18E 10"), "UT", "0120N", "0180E", "010")


class TestPLSSThreeNumberFallback:
    """Flexible three-number formats (T-R-S convention when no direction)."""

    def test_hyphen_separated(self):
        _assert_result(parse_plss_string("28-11-18"), "UT", "0280S", "0110W", "018")

    def test_space_separated(self):
        _assert_result(parse_plss_string("28 11 18"), "UT", "0280S", "0110W", "018")

    def test_comma_separated(self):
        _assert_result(parse_plss_string("28,11,18"), "UT", "0280S", "0110W", "018")

    def test_three_numbers_all_in_section_range(self):
        """Township/range can be 1-36 (e.g. T12 R18 S10)."""
        _assert_result(parse_plss_string("12-18-10"), "UT", "0120S", "0180W", "010")


class TestPLSSStatePrefix:
    """State prefix at start of string."""

    def test_state_prefix(self):
        _assert_result(parse_plss_string("NV 21N 57E 18"), "NV", "0210N", "0570E", "018")

    def test_state_with_plss(self):
        _assert_result(parse_plss_string("UT T28S R11W S18"), "UT", "0280S", "0110W", "018")


class TestPLSSSpelledOutDirections:
    """Spelled-out North/South/East/West, Township, Range, Section."""

    def test_township_south_range_west(self):
        r = parse_plss_string("Twp 28 South Range 11 West Sec 18")
        _assert_result(r, "UT", "0280S", "0110W", "018")

    def test_t12s_r18w_sec10(self):
        r = parse_plss_string("T. 12 S. R. 18 W. Sec 10")
        _assert_result(r, "UT", "0120S", "0180W", "010")


class TestPLSSNoSection:
    """T-R only; section optional."""

    def test_township_range_only(self):
        r = parse_plss_string("12S 18W")
        assert r is not None
        assert r["township"] == "0120S"
        assert r["range"] == "0180W"
        assert r["section"] is None


class TestPLSSInvalidOrEmpty:
    """Invalid inputs return None."""

    def test_empty(self):
        assert parse_plss_string("") is None

    def test_not_string(self):
        assert parse_plss_string(None) is None  # type: ignore

    def test_whitespace_only(self):
        assert parse_plss_string("   ") is None
