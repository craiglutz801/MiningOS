"""Core record contracts, result containers, and pipeline exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SourceUnavailableError(Exception):
    """A data source could not be retrieved from network, cache, or manual file."""


class SourceSchemaError(Exception):
    """A data source was retrieved but its schema could not be interpreted."""


class DataValidationError(Exception):
    """Loaded data failed a validation gate."""


class FatalPipelineError(Exception):
    """The pipeline cannot produce a meaningful result."""


@dataclass
class SourceStatus:
    source_id: str
    status: str = "pending"  # success | cached | stale | empty | degraded | failed | skipped
    resolved_url: str | None = None
    retrieved_at: str | None = None
    record_count: int = 0
    cache_used: bool = False
    cache_age_hours: float | None = None
    message: str | None = None
    outcome: str | None = None  # ok | empty | failed | stale — distinct from record_count=0
    usable_for_assertions: bool | None = None
    failure_class: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        usable = self.usable_for_assertions
        if usable is None:
            usable = self.status not in {"failed", "stale", "unavailable", "degraded", "pending"}
        return {
            "status": self.status,
            "resolved_url": self.resolved_url,
            "retrieved_at": self.retrieved_at,
            "record_count": self.record_count,
            "cache_used": self.cache_used,
            "cache_age_hours": self.cache_age_hours,
            "message": self.message,
            "outcome": self.outcome
            or (
                "failed"
                if self.status == "failed"
                else "stale"
                if self.status in {"stale", "degraded"}
                else "empty"
                if self.status == "empty" or (self.status in {"success", "cached"} and self.record_count == 0)
                else "ok"
                if self.status in {"success", "cached"}
                else self.status
            ),
            "usable_for_assertions": usable,
            "failure_class": self.failure_class,
            "extra": self.extra or {},
        }


@dataclass
class PipelineResult:
    state_code: str
    run_id: str
    status: str = "failed"  # success | partial | failed
    degraded_mode: bool = False
    started_at: str = ""
    completed_at: str = ""
    sources: dict[str, SourceStatus] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    output_dir: str = ""
    output_files: list[str] = field(default_factory=list)
    # In-memory frames for Mining OS persistence (not serialized to manifest)
    site_summary: Any = None
    matches: Any = None
    qc: dict[str, Any] = field(default_factory=dict)

    def add_warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def manifest(self, software_version: str) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state_code,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "degraded_mode": self.degraded_mode,
            "sources": {sid: s.to_dict() for sid, s in self.sources.items()},
            "counts": self.counts,
            "warnings": self.warnings,
            "software_version": software_version,
        }
