"""SQLAlchemy engine helper (isolated from mining_os package)."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from target_pipeline.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


def clear_engine_cache() -> None:
    get_engine.cache_clear()
