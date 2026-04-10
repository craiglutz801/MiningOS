"""
Database helpers: engine, session, raw SQL execution.

Key fix vs. the original spec: VACUUM ANALYZE cannot run inside a
transaction.  We use a separate autocommit connection for maintenance
commands.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from mining_os.config import settings

log = logging.getLogger("mining_os.db")

_ENGINE: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(settings.db_url, pool_pre_ping=True)
    return _ENGINE


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    sm = get_sessionmaker()
    session = sm()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def exec_sql(sql: str) -> None:
    """Execute a multi-statement SQL string (e.g. schema init)."""
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text(sql))


def vacuum_analyze(table_name: str) -> None:
    """Run VACUUM ANALYZE outside a transaction (requires autocommit)."""
    eng = get_engine()
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"VACUUM ANALYZE {table_name};"))


def table_exists(table_name: str, schema: str = "public") -> bool:
    eng = get_engine()
    q = """
    SELECT EXISTS (
      SELECT 1
      FROM information_schema.tables
      WHERE table_schema = :schema AND table_name = :table
    ) AS exists;
    """
    with eng.begin() as conn:
        return bool(conn.execute(text(q), {"schema": schema, "table": table_name}).scalar())
