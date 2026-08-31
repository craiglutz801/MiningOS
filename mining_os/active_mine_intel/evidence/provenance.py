"""Per-assertion provenance records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AssertionProvenance:
    """One auditable assertion about a mine site.

    ``contradiction`` is None when the assertion is uncontested; otherwise it
    names the conflicting assertion and why this one was not used.
    """

    assertion_type: str
    value: str
    source_id: str
    source_url: str | None = None
    effective_date: str | None = None
    retrieved_at: str | None = None
    match_method: str | None = None
    freshness: str = "unknown"  # current | stale | unknown
    confidence: float = 0.0
    usable: bool = True
    contradiction: dict[str, Any] | None = None
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        extra = payload.pop("extra") or {}
        for key, value in extra.items():
            if key not in payload:
                payload[key] = value
        return payload


def assertion(
    assertion_type: str,
    value: str,
    *,
    source_id: str,
    source_url: str | None = None,
    effective_date: str | None = None,
    retrieved_at: str | None = None,
    match_method: str | None = None,
    freshness: str = "unknown",
    confidence: float = 0.0,
    usable: bool = True,
    contradiction: dict[str, Any] | None = None,
    notes: str | None = None,
    **extra: Any,
) -> AssertionProvenance:
    return AssertionProvenance(
        assertion_type=assertion_type,
        value=value,
        source_id=source_id,
        source_url=source_url,
        effective_date=effective_date,
        retrieved_at=retrieved_at,
        match_method=match_method,
        freshness=freshness,
        confidence=max(0.0, min(1.0, float(confidence))),
        usable=usable,
        contradiction=contradiction,
        notes=notes,
        extra=extra,
    )
