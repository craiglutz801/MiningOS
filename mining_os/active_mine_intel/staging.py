"""Staging isolation — refuse production hosts, secrets, and databases."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

PRODUCTION_HOST_MARKERS = (
    "miningos.onrender.com",
    "dpg-",  # typical Render managed Postgres hostname fragment is too broad; see extras
)

# Explicit production database hostnames / URL fragments Craig's prod has used.
PRODUCTION_DB_MARKERS = (
    "miningos.onrender.com",
    "oregon-postgres.render.com",
    "dpg-production",
)

PRODUCTION_SECRET_ENV = (
    "PRODUCTION_DATABASE_URL",
    "PROD_DATABASE_URL",
)


def mining_os_environment() -> str:
    raw = (os.getenv("MINING_OS_ENVIRONMENT") or os.getenv("VITE_MINING_OS_ENV") or "").strip().lower()
    if raw in {"staging", "stage", "preview"}:
        return "staging"
    if raw in {"production", "prod"}:
        return "production"
    if os.getenv("RENDER"):
        # Render production web service is named mining-os-api in render.yaml.
        svc = (os.getenv("RENDER_SERVICE_NAME") or "").strip().lower()
        if "staging" in svc or svc.endswith("-staging"):
            return "staging"
        if svc in {"mining-os-api", "miningos"}:
            return "production"
    return "development"


def is_staging() -> bool:
    return mining_os_environment() == "staging"


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def looks_like_production_url(url: str | None) -> bool:
    if not url:
        return False
    text = str(url).strip().lower()
    if not text:
        return False
    host = _host(text) or text
    if "staging" in host or "preview" in host:
        return False
    if host == "miningos.onrender.com":
        return True
    for marker in PRODUCTION_DB_MARKERS:
        if marker in text and "staging" not in text:
            return True
    return False


def staging_isolation_report(
    *,
    database_url: str | None = None,
    api_origin: str | None = None,
) -> dict[str, Any]:
    """Validate that staging is not wired to production data or credentials."""
    env = mining_os_environment()
    db = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    origin = api_origin if api_origin is not None else os.getenv("API_ORIGIN", "")
    violations: list[str] = []
    if env == "staging":
        if looks_like_production_url(db):
            violations.append("DATABASE_URL points at a production host")
        if looks_like_production_url(origin):
            violations.append("API_ORIGIN points at production miningos.onrender.com")
        for name in PRODUCTION_SECRET_ENV:
            if os.getenv(name):
                violations.append(f"{name} is set in a staging process")
        if os.getenv("RENDER_SERVICE_NAME", "").strip().lower() in {"mining-os-api", "miningos"}:
            violations.append("RENDER_SERVICE_NAME is the production web service")
    return {
        "ok": not violations,
        "environment": env,
        "staging": env == "staging",
        "violations": violations,
        "database_host": _host(db) if db else None,
        "api_origin_host": _host(origin) if origin else None,
    }


def assert_staging_isolated() -> dict[str, Any]:
    report = staging_isolation_report()
    if mining_os_environment() == "staging" and not report["ok"]:
        raise RuntimeError(
            "Staging isolation failed: " + "; ".join(report["violations"])
        )
    return report
