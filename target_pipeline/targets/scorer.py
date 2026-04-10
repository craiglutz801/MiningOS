"""V1 scoring for target groups."""

from __future__ import annotations

from target_pipeline.models import TargetGroup


def score_target(t: TargetGroup) -> TargetGroup:
    """
    +3 if at least one deposit-like record
    +2 if at least one report/reference on a deposit
    +2 if more than one deposit
    +1 if any related claims
    """
    deposits = t.get("deposits") or []
    claims = t.get("claims") or []
    notes: list[str] = []
    score = 0
    if deposits:
        score += 3
        notes.append("has_deposit")
    has_report = False
    for d in deposits:
        reps = d.get("reports") or []
        if reps and any(str(x).strip() for x in reps):
            has_report = True
            break
    if not has_report and t.get("report_links"):
        has_report = True
    if has_report:
        score += 2
        notes.append("has_report")
    if len(deposits) > 1:
        score += 2
        notes.append("multiple_deposits")
    if claims:
        score += 1
        notes.append("has_claims")

    t["has_report"] = has_report
    t["score"] = score
    t["score_notes"] = notes
    return t
