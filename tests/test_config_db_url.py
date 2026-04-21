"""
Tests for DATABASE_URL dialect normalization.

Managed Postgres providers (Render, Supabase, Heroku, Railway) hand out
DATABASE_URL values in several different forms. SQLAlchemy must receive
`postgresql+psycopg://` so it uses psycopg v3 (what requirements.txt
installs). If the scheme is left as bare `postgresql://` SQLAlchemy tries
to import psycopg2 and fails at runtime.
"""

from __future__ import annotations

import pytest

from mining_os.config import Settings


def _url_for(raw: str) -> str:
    s = Settings(DATABASE_URL=raw)
    return s.db_url


class TestDialectNormalization:
    def test_bare_postgresql_becomes_psycopg(self):
        """Render's default — `postgresql://user:pw@host/db`."""
        assert _url_for("postgresql://u:p@h:5432/d").startswith("postgresql+psycopg://")

    def test_heroku_style_postgres_prefix(self):
        """Heroku / some older providers use `postgres://`."""
        assert _url_for("postgres://u:p@h:5432/d").startswith("postgresql+psycopg://")

    def test_explicit_psycopg2_driver_rewritten(self):
        assert _url_for("postgresql+psycopg2://u:p@h:5432/d").startswith(
            "postgresql+psycopg://"
        )

    def test_already_psycopg_untouched(self):
        url = "postgresql+psycopg://u:p@h:5432/d"
        assert _url_for(url) == url

    def test_credentials_preserved(self):
        raw = "postgresql://miningos_db_user:ewjM5UcpUiJQjAuRFhENdk5SdWAatDlb@host:5432/miningos_db"
        out = _url_for(raw)
        assert "miningos_db_user" in out
        assert "ewjM5UcpUiJQjAuRFhENdk5SdWAatDlb" in out
        assert "host:5432/miningos_db" in out

    def test_empty_falls_back_to_postgres_vars(self):
        out = Settings(DATABASE_URL="", POSTGRES_HOST="h", POSTGRES_USER="u",
                       POSTGRES_PASSWORD="p", POSTGRES_DB="d", POSTGRES_PORT=5432).db_url
        assert out.startswith("postgresql+psycopg://")
        assert "h:5432/d" in out
