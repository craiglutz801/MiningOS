"""
Centralised settings — loaded from .env / environment variables.

pydantic-settings handles reading the .env file automatically via
``env_file = ".env"``; we don't need manual os.getenv() calls.
"""

from __future__ import annotations

import logging
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = ""
    POSTGRES_USER: str = "miningos"
    POSTGRES_PASSWORD: str = "miningos"
    POSTGRES_DB: str = "miningos"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # API / Dashboard
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    DASHBOARD_PORT: int = 8501

    # Business knobs (env: comma-separated; use .TARGET_STATES / .COMMODITIES in code)
    TARGET_STATES_STR: str = Field(default="UT,ID,NV,MT,WY", validation_alias="TARGET_STATES")
    COMMODITIES_STR: str = Field(
        default="tungsten,scandium,beryllium,uranium,fluorspar,fluorite,germanium",
        validation_alias="COMMODITIES",
    )

    # Spatial knobs
    MRDS_RADIUS_KM: float = 10.0

    # Alerts: email for high-priority unpaid claims
    ALERT_EMAIL: str = "craiglutz801@gmail.com"

    # Optional: OpenAI for discovery agent
    OPENAI_API_KEY: str = ""

    # Optional: Gemini for PLSS extraction from long web/Gemini-style blurbs
    GEMINI_API_KEY: str = ""

    # Batch USGS PDF download / extraction (OME, DMA, DMEA lists)
    BATCH_MAX_PDF_MB: float = 150.0
    BATCH_OCR_MAX_PAGES: int = 12
    # Per-page OCR subprocess timeout (pytesseract; 0 = wait indefinitely — not recommended)
    BATCH_OCR_PAGE_TIMEOUT_SEC: int = 120
    # Full path to tesseract binary if PATH is wrong (e.g. /opt/homebrew/bin/tesseract on Apple Silicon)
    TESSERACT_CMD: str = ""

    # ---- parsed lists (use these in code) ------------------------------------

    @property
    def TARGET_STATES(self) -> List[str]:
        return [s.strip() for s in self.TARGET_STATES_STR.split(",") if s.strip()]

    @property
    def COMMODITIES(self) -> List[str]:
        return [s.strip().lower() for s in self.COMMODITIES_STR.split(",") if s.strip()]

    # ---- derived -----------------------------------------------------------

    @property
    def db_url(self) -> str:
        raw = self.DATABASE_URL
        if not raw:
            log.warning(
                "[config] DATABASE_URL is empty — falling back to individual "
                "POSTGRES_* variables (host=%s port=%s db=%s user=%s)",
                self.POSTGRES_HOST,
                self.POSTGRES_PORT,
                self.POSTGRES_DB,
                self.POSTGRES_USER,
            )
            url = (
                f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
            log.info("[config] db_url (fallback): %s", url)
            return url

        log.info("[config] DATABASE_URL raw value (scheme only): %s",
                 raw.split("://", 1)[0] if "://" in raw else "<invalid>")

        # Managed Postgres providers hand out URLs in a variety of schemes.
        # We need to normalize them to `postgresql+psycopg://` so SQLAlchemy
        # uses psycopg (v3) which is what requirements.txt installs
        # (`psycopg[binary]`). Without this, SQLAlchemy tries to import
        # psycopg2 on bare `postgresql://` URLs and fails in prod.
        converted = raw
        if converted.startswith("postgres://"):
            converted = "postgresql://" + converted[len("postgres://"):]
        if converted.startswith("postgresql+psycopg2://"):
            converted = "postgresql+psycopg://" + converted[len("postgresql+psycopg2://"):]
        elif converted.startswith("postgresql://"):
            converted = "postgresql+psycopg://" + converted[len("postgresql://"):]

        if converted != raw:
            log.info("[config] Dialect normalized to postgresql+psycopg:// (psycopg v3)")
        log.info("[config] db_url scheme (final): %s",
                 converted.split("://", 1)[0] if "://" in converted else "<invalid>")
        return converted


settings = Settings()
