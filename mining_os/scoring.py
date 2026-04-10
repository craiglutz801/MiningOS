"""
Candidate scoring.

Three sub-scores are summed (capped at 100):
  - commodity_score  (max 40): weighted by strategic importance
  - evidence_score   (max 35): MRDS hit count + reference text presence
  - opportunity_score (max 25): MVP placeholder (extend later)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Weights:
    heavy: Dict[str, int]
    medium: Dict[str, int]


DEFAULT_WEIGHTS = Weights(
    heavy={
        "fluorspar": 14,
        "fluorite": 12,
        "beryllium": 14,
        "tungsten": 14,
        "germanium": 14,
        "uranium": 14,
        "u3o8": 14,
    },
    medium={
        "rare earth": 8,
        "lithium": 8,
        "cobalt": 8,
        "nickel": 6,
        "antimony": 8,
        "graphite": 6,
        "vanadium": 6,
        "manganese": 6,
        "tin": 6,
    },
)


def score_commodities(commodities: List[str]) -> int:
    """Score based on which commodities are present (max 40)."""
    s = 0
    for c in commodities:
        if c in DEFAULT_WEIGHTS.heavy:
            s += DEFAULT_WEIGHTS.heavy[c]
        else:
            for k, w in DEFAULT_WEIGHTS.medium.items():
                if k in c:
                    s += w
    return min(s, 40)


def score_evidence(mrds_hits: int, has_reference_text: bool) -> int:
    """Evidence proxy score (max 35)."""
    score = min(mrds_hits * 8, 24)
    if has_reference_text:
        score += 11
    return min(score, 35)


def score_opportunity() -> int:
    """MVP placeholder — extend with closed disposition, timing, claim density."""
    return 5


def total_score(commodities: List[str], mrds_hits: int, has_reference_text: bool) -> int:
    return min(
        score_commodities(commodities)
        + score_evidence(mrds_hits, has_reference_text)
        + score_opportunity(),
        100,
    )
