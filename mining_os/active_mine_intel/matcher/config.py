"""Typed configuration for the Mine Claim Matcher pipeline.

All state-specific behavior differences are expressed as configuration
overrides here, never scattered through the pipeline code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# Cache/raw data lives under Mining OS data_files (not the package tree).
_REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = _REPO_ROOT / "data_files" / "active_mines"

SOFTWARE_VERSION = "0.1.0"

# Generous bounding boxes (lon_min, lat_min, lon_max, lat_max) used to reject
# wildly wrong coordinates, not to clip precisely at the state line.
STATE_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "NV": (-121.0, 34.5, -113.0, 43.0),
    "UT": (-115.5, 36.4, -108.0, 43.0),
}

MAP_CENTERS: dict[str, tuple[float, float, float]] = {
    # latitude, longitude, zoom
    "NV": (39.3, -116.6, 5.7),
    "UT": (39.3, -111.7, 5.7),
}


@dataclass(frozen=True)
class Paths:
    """Filesystem layout. Overridable so tests can run in a temp directory."""

    root: Path = PROJECT_ROOT
    data_raw: Path = None  # type: ignore[assignment]
    data_processed: Path = None  # type: ignore[assignment]
    data_manual: Path = None  # type: ignore[assignment]
    outputs: Path = None  # type: ignore[assignment]
    logs: Path = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_raw", self.data_raw or self.root / "data" / "raw")
        object.__setattr__(
            self, "data_processed", self.data_processed or self.root / "data" / "processed"
        )
        object.__setattr__(self, "data_manual", self.data_manual or self.root / "data" / "manual")
        object.__setattr__(self, "outputs", self.outputs or self.root / "outputs")
        object.__setattr__(self, "logs", self.logs or self.root / "logs")

    def raw_dir(self, scope: str) -> Path:
        return self.data_raw / scope

    def processed_dir(self, scope: str) -> Path:
        return self.data_processed / scope

    def manual_dir(self, scope: str) -> Path:
        return self.data_manual / scope

    def output_dir(self, state_name: str) -> Path:
        return self.outputs / state_name.lower()

    def ensure(self, state_name: str) -> None:
        for scope in ("common", "nevada", "utah"):
            self.raw_dir(scope).mkdir(parents=True, exist_ok=True)
            self.processed_dir(scope).mkdir(parents=True, exist_ok=True)
            self.manual_dir(scope).mkdir(parents=True, exist_ok=True)
        (self.data_manual / "common").mkdir(parents=True, exist_ok=True)
        self.output_dir(state_name).mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class PipelineConfig:
    state_code: str
    state_name: str
    projected_crs: str = "EPSG:5070"
    exact_match_buffer_m: float = 0.0
    near_match_distance_m: float = 250.0
    review_match_distance_m: float = 1000.0
    mine_state_merge_distance_m: float = 5000.0
    claim_cluster_distance_m: float = 125.0
    high_score_threshold: int = 85
    strong_score_threshold: int = 70
    review_score_threshold: int = 55
    weak_score_threshold: int = 40
    recent_inspection_months: int = 18
    recent_hours_quarters: int = 8
    cache_ttl_hours: int = 24
    launch_browser: bool = True
    # Association thresholds (mine <-> BLM operation)
    operation_name_assoc_distance_m: float = 2000.0
    operation_operator_assoc_distance_m: float = 5000.0
    operation_name_similarity_min: float = 70.0
    operation_operator_similarity_min: float = 90.0
    # Entity-resolution thresholds (state mine <-> MSHA mine)
    merge_name_similarity_min: float = 75.0
    merge_operator_similarity_min: float = 85.0
    paths: Paths = field(default_factory=Paths)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return STATE_BOUNDS[self.state_code]


_STATE_NAMES = {"NV": "Nevada", "UT": "Utah"}


def get_config(state_code: str, paths: Paths | None = None, **overrides) -> PipelineConfig:
    state_code = state_code.upper()
    if state_code not in _STATE_NAMES:
        raise ValueError(f"Unsupported state code: {state_code!r}. Use 'NV' or 'UT'.")
    kwargs: dict = {
        "state_code": state_code,
        "state_name": _STATE_NAMES[state_code],
    }
    ttl = os.environ.get("MCM_CACHE_TTL_HOURS")
    if ttl and ttl.strip().isdigit():
        kwargs["cache_ttl_hours"] = int(ttl)
    if paths is not None:
        kwargs["paths"] = paths
    cfg = PipelineConfig(**kwargs)
    if overrides:
        cfg = replace(cfg, **overrides)
    return cfg
