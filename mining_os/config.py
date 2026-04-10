"""
Centralised settings — loaded from .env / environment variables.

pydantic-settings handles reading the .env file automatically via
``env_file = ".env"``; we don't need manual os.getenv() calls.
"""

from __future__ import annotations

from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
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
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
