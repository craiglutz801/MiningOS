"""Extract PLSS (township / range / section) from verbose prose, MRDS blurbs, search snippets."""

from __future__ import annotations

import re
from typing import Any


# T48N R2E, T. 48 N. R. 2 E., etc.
_RE_TR = re.compile(
    r"""
    \bT\.?\s*
    (?P<twp>\d{1,3})\s*
    (?P<tns>[NS])\s*
    ,?\s*
    R\.?\s*
    (?P<rng>\d{1,3})\s*
    (?P<rew>[EW])
    """,
    re.I | re.VERBOSE,
)

# Section near a TR match (look after TR, within window)
_RE_SEC_NEAR = re.compile(
    r"\bSec(?:tion|t)?\.?\s*(?P<sec>\d{1,2})\b",
    re.I,
)

# "Township 48 North Range 2 East"
_RE_WORDY = re.compile(
    r"""
    Township\s+(?P<twp>\d{1,3})\s+(?P<tns>North|South)\s+
    Range\s+(?P<rng>\d{1,3})\s+(?P<rew>East|West)
    """,
    re.I | re.VERBOSE,
)

_MAP_NS = {"NORTH": "N", "SOUTH": "S"}
_MAP_EW = {"EAST": "E", "WEST": "W"}


def _sec_after_tr(text: str, tr_end: int) -> str | None:
    window = text[tr_end : min(len(text), tr_end + 160)]
    m = _RE_SEC_NEAR.search(window)
    return m.group("sec") if m else None


def extract_plss_from_prose(text: str | None) -> dict[str, Any] | None:
    """
    Pull an explicit PLSS string from long text (snippets, MRDS, Gemini-style blurbs).

    Returns dict with keys: plss, township, range, section (optional), extract_kind, span_quote
    or None if nothing reliable found.
    """
    if not text or len(text.strip()) < 8:
        return None
    t = text
    # Prefer first clear TR pattern
    for rx in (_RE_TR,):
        for m in rx.finditer(t):
            twp = f"{m.group('twp')}{m.group('tns').upper()}"
            rng = f"{m.group('rng')}{m.group('rew').upper()}"
            sec = _sec_after_tr(t, m.end())
            plss = f"T{twp} R{rng}" + (f" Sec{sec}" if sec else "")
            quote = t[max(0, m.start() - 20) : min(len(t), m.end() + 40)].replace("\n", " ")
            return {
                "plss": plss,
                "township": twp,
                "range": rng,
                "section": sec,
                "extract_kind": "regex_tr",
                "span_quote": quote.strip()[:180],
            }

    m = _RE_WORDY.search(t)
    if m:
        twp = f"{m.group('twp')}{_MAP_NS[m.group('tns').upper()]}"
        rng = f"{m.group('rng')}{_MAP_EW[m.group('rew').upper()]}"
        tr_end = m.end()
        sec = _sec_after_tr(t, tr_end)
        plss = f"T{twp} R{rng}" + (f" Sec{sec}" if sec else "")
        quote = t[max(0, m.start() - 12) : min(len(t), m.end() + 50)].replace("\n", " ")
        return {
            "plss": plss,
            "township": twp,
            "range": rng,
            "section": sec,
            "extract_kind": "regex_wordy",
            "span_quote": quote.strip()[:180],
        }

    return None
