"""Active Mine Search — feature flags."""

from __future__ import annotations

import os

from mining_os.config import settings

SUPPORTED_STATES = ("NV", "UT")


def _env_bool(name: str) -> bool | None:
    """Return True/False if ``name`` is set in the process env; else None."""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _on_render() -> bool:
    return os.getenv("RENDER", "").strip().lower() in {"1", "true", "yes"}


def active_mines_enabled() -> bool:
    # Explicit env wins. On Render, match render.yaml when the dashboard
    # never synced ENABLE_ACTIVE_MINES_* (common for existing services).
    explicit = _env_bool("ENABLE_ACTIVE_MINES_API")
    if explicit is not None:
        return explicit
    if _on_render():
        return True
    return bool(getattr(settings, "ENABLE_ACTIVE_MINES_API", False))


def active_mines_admin_enabled() -> bool:
    explicit = _env_bool("ENABLE_ACTIVE_MINES_ADMIN")
    if explicit is not None:
        return explicit
    return bool(getattr(settings, "ENABLE_ACTIVE_MINES_ADMIN", False))


def active_mines_jobs_enabled() -> bool:
    explicit = _env_bool("ENABLE_ACTIVE_MINES_JOBS")
    if explicit is not None:
        return explicit
    if _on_render():
        return True
    return bool(getattr(settings, "ENABLE_ACTIVE_MINES_JOBS", False))


def disabled_payload() -> dict:
    return {
        "ok": False,
        "enabled": False,
        "error": "Active Mine Search is disabled. Set ENABLE_ACTIVE_MINES_API=true.",
    }
