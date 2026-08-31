"""Staging isolation — refuse production hosts, secrets, and databases.

Also guards the reverse: production must not be wired to ephemeral tunnels
or the staging Render service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PRODUCTION_HOST_MARKERS = (
    "miningos.onrender.com",
    "dpg-",  # typical Render managed Postgres hostname fragment is too broad; see extras
)

# Explicit production database hostnames / URL fragments Craig's prod has used.
PRODUCTION_DB_MARKERS = (
    "miningos.onrender.com",
    "dpg-production",
)

PRODUCTION_SECRET_ENV = (
    "PRODUCTION_DATABASE_URL",
    "PROD_DATABASE_URL",
)

EPHEMERAL_TUNNEL_MARKERS = (
    "trycloudflare.com",
    "ngrok.io",
    "ngrok.app",
    "ngrok-free.app",
    "loca.lt",
)

PRODUCTION_WEB_SERVICE = "mining-os-api"
STAGING_WEB_SERVICE = "mining-os-api-staging"
PRODUCTION_API_ORIGIN = "https://miningos.onrender.com"
STAGING_API_ORIGIN = "https://mining-os-api-staging.onrender.com"

# Mergeable production config: must never mention ephemeral tunnels.
PRODUCTION_CONFIG_RELPATHS = (
    "frontend/vercel.json",
    "frontend/.env.production",
    "render.yaml",
)

# Staging config: must never mention the production API/DB hosts.
STAGING_CONFIG_RELPATHS = (
    "render.staging.yaml",
    "docker-compose.staging.yml",
    "config/staging.env.example",
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
        if svc in {PRODUCTION_WEB_SERVICE, "miningos"}:
            return "production"
    return "development"


def is_staging() -> bool:
    return mining_os_environment() == "staging"


def is_production() -> bool:
    return mining_os_environment() == "production"


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def looks_like_ephemeral_tunnel(url: str | None) -> bool:
    if not url:
        return False
    text = str(url).strip().lower()
    return any(marker in text for marker in EPHEMERAL_TUNNEL_MARKERS)


def looks_like_staging_url(url: str | None) -> bool:
    if not url:
        return False
    text = str(url).strip().lower()
    host = _host(text) or text
    if "staging" in host or host.startswith("mining-os-api-staging."):
        return True
    if looks_like_ephemeral_tunnel(text):
        return True
    return False


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
        if os.getenv("RENDER_SERVICE_NAME", "").strip().lower() in {PRODUCTION_WEB_SERVICE, "miningos"}:
            violations.append("RENDER_SERVICE_NAME is the production web service")
    return {
        "ok": not violations,
        "environment": env,
        "staging": env == "staging",
        "violations": violations,
        "database_host": _host(db) if db else None,
        "api_origin_host": _host(origin) if origin else None,
    }


def production_wiring_report(
    *,
    database_url: str | None = None,
    api_origin: str | None = None,
) -> dict[str, Any]:
    """Validate that production is not wired to staging or ephemeral tunnels."""
    env = mining_os_environment()
    db = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    origin = api_origin if api_origin is not None else os.getenv("API_ORIGIN", "")
    violations: list[str] = []
    if env == "production":
        if looks_like_ephemeral_tunnel(db) or looks_like_ephemeral_tunnel(origin):
            violations.append("production is wired to an ephemeral tunnel (trycloudflare/ngrok)")
        if looks_like_staging_url(origin) and not looks_like_production_url(origin):
            violations.append("production API_ORIGIN points at staging")
        if looks_like_staging_url(db) and not looks_like_production_url(db):
            violations.append("production DATABASE_URL points at staging")
        svc = os.getenv("RENDER_SERVICE_NAME", "").strip().lower()
        if svc == STAGING_WEB_SERVICE or svc.endswith("-staging"):
            violations.append("RENDER_SERVICE_NAME is a staging web service")
    return {
        "ok": not violations,
        "environment": env,
        "production": env == "production",
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


def assert_environment_wiring() -> dict[str, Any]:
    """Fail closed at process start when staging↔production wiring is wrong."""
    env = mining_os_environment()
    if env == "staging":
        return assert_staging_isolated()
    if env == "production":
        report = production_wiring_report()
        if not report["ok"]:
            raise RuntimeError(
                "Production wiring failed: " + "; ".join(report["violations"])
            )
        return report
    return {"ok": True, "environment": env, "violations": []}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strip_config_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def scan_production_config_files(root: Path | None = None) -> dict[str, Any]:
    """CI/regression: mergeable production files must not reference trycloudflare."""
    base = root or _repo_root()
    violations: list[str] = []
    vercel_path = base / "frontend" / "vercel.json"
    vercel = json.loads(vercel_path.read_text(encoding="utf-8"))
    raw_vercel = vercel_path.read_text(encoding="utf-8")
    if any(marker in raw_vercel.lower() for marker in EPHEMERAL_TUNNEL_MARKERS):
        violations.append("frontend/vercel.json references an ephemeral tunnel")
    dests = [str(r.get("destination") or "") for r in vercel.get("rewrites") or []]
    if PRODUCTION_API_ORIGIN + "/api/:path*" not in dests:
        violations.append("frontend/vercel.json default API rewrite is not production Render")
    for rewrite in vercel.get("rewrites") or []:
        dest = str(rewrite.get("destination") or "")
        has = rewrite.get("has") or []
        host_vals = " ".join(
            str(item.get("value") or "") for item in has if isinstance(item, dict)
        )
        if "mining-os-git-" in host_vals:
            if looks_like_production_url(dest):
                violations.append("Vercel git-preview rewrite points at production API")
            if looks_like_ephemeral_tunnel(dest):
                violations.append("Vercel git-preview rewrite points at an ephemeral tunnel")
            if STAGING_API_ORIGIN not in dest:
                violations.append("Vercel git-preview rewrite is not the staging Render service")
    for rel in PRODUCTION_CONFIG_RELPATHS:
        text = (base / rel).read_text(encoding="utf-8")
        if any(marker in text.lower() for marker in EPHEMERAL_TUNNEL_MARKERS):
            violations.append(f"{rel} references an ephemeral tunnel")
    return {"ok": not violations, "violations": violations}


def scan_staging_config_files(root: Path | None = None) -> dict[str, Any]:
    """CI/regression: staging files must not reference production API/DB hosts."""
    base = root or _repo_root()
    violations: list[str] = []
    forbidden = ("miningos.onrender.com", "dpg-production")
    for rel in STAGING_CONFIG_RELPATHS:
        path = base / rel
        if not path.exists():
            violations.append(f"{rel} is missing")
            continue
        text = _strip_config_comments(path.read_text(encoding="utf-8"))
        for marker in forbidden:
            if marker in text.lower():
                violations.append(f"{rel} references production marker {marker}")
    staging_yaml = _strip_config_comments((base / "render.staging.yaml").read_text(encoding="utf-8"))
    if f"name: {PRODUCTION_WEB_SERVICE}\n" in staging_yaml and STAGING_WEB_SERVICE not in staging_yaml:
        violations.append("render.staging.yaml names the production web service")
    if "MINING_OS_ENVIRONMENT" in staging_yaml and "staging" not in staging_yaml:
        violations.append("render.staging.yaml does not set MINING_OS_ENVIRONMENT=staging")
    return {"ok": not violations, "violations": violations}
