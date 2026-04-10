"""Typed settings from environment (.env supported via python-dotenv in run.py)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from target_pipeline.filters import DEFAULT_TARGET_STATES


def _split_states(raw: Optional[str]) -> frozenset[str]:
    if not raw or not str(raw).strip():
        return frozenset()
    return frozenset(s.strip().upper()[:2] for s in raw.split(",") if s.strip())


def _database_url_from_env() -> str:
    """Same connection string as Mining OS when only POSTGRES_* is set in .env."""
    direct = (os.environ.get("DATABASE_URL") or "").strip()
    if direct:
        return direct
    user = (os.environ.get("POSTGRES_USER") or "miningos").strip()
    password = (os.environ.get("POSTGRES_PASSWORD") or "miningos").strip()
    host = (os.environ.get("POSTGRES_HOST") or "localhost").strip()
    port = (os.environ.get("POSTGRES_PORT") or "5432").strip()
    db = (os.environ.get("POSTGRES_DB") or "miningos").strip()
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def _opt_int(raw: Optional[str]) -> Optional[int]:
    if not raw or not str(raw).strip():
        return None
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return None


def _target_states_from_env() -> frozenset[str]:
    raw = (os.environ.get("TARGET_PIPELINE_STATES") or os.environ.get("TARGET_FOCUS_STATES") or "").strip()
    if raw:
        return _split_states(raw)
    return DEFAULT_TARGET_STATES


@dataclass(frozen=True)
class Settings:
    database_url: str
    target_states: frozenset[str] = frozenset()
    log_level: str = "INFO"
    target_pipeline_data_dir: str = "./target_pipeline/data"
    output_table: str = "areas_of_focus"
    merge_by_plss_for_app: bool = True
    max_rows_per_source: Optional[int] = None

    @classmethod
    def from_env(cls) -> Settings:
        url = _database_url_from_env()
        if not url:
            raise ValueError("Set DATABASE_URL or POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_HOST/POSTGRES_PORT/POSTGRES_DB")
        merge_raw = (os.environ.get("MERGE_BY_PLSS_FOR_APP") or "true").strip().lower()
        return cls(
            database_url=url,
            target_states=_target_states_from_env(),
            log_level=(os.environ.get("LOG_LEVEL") or "INFO").strip().upper(),
            target_pipeline_data_dir=(
                os.environ.get("TARGET_PIPELINE_DATA_DIR") or "./target_pipeline/data"
            ).strip(),
            output_table=(os.environ.get("OUTPUT_TABLE") or "areas_of_focus").strip().lower(),
            merge_by_plss_for_app=merge_raw in ("1", "true", "yes", "on"),
            max_rows_per_source=_opt_int(os.environ.get("TARGET_PIPELINE_MAX_ROWS")),
        )


# Mutable module-level for tests
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None
