"""Helpers for batch import: require resolvable PLSS before creating a Target."""

from __future__ import annotations

from typing import Any


def effective_plss_string(t: dict[str, Any]) -> str:
    """
    Build a single PLSS line from ``plss`` or from township + range + optional section.
    Matches the logic in ``pdf_report_processor._clean_target``.
    """
    plss = (t.get("plss") or "").strip()
    if plss:
        return plss
    twp = (t.get("township") or "").strip()
    rng = (t.get("range") or "").strip()
    sec = (t.get("section") or "").strip()
    if twp and rng:
        return "T" + twp + " R" + rng + (" Sec" + sec if sec else "")
    return ""


def has_required_plss(t: dict[str, Any]) -> bool:
    """True if we can store a non-empty ``location_plss`` for this payload."""
    return bool(effective_plss_string(t).strip())
