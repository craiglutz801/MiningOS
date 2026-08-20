"""Active Mine Search — feature flags."""

from __future__ import annotations

from mining_os.config import settings

SUPPORTED_STATES = ("NV", "UT")


def active_mines_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_ACTIVE_MINES_API", False))


def active_mines_admin_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_ACTIVE_MINES_ADMIN", False))


def active_mines_jobs_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_ACTIVE_MINES_JOBS", False))


def disabled_payload() -> dict:
    return {
        "ok": False,
        "enabled": False,
        "error": "Active Mine Search is disabled. Set ENABLE_ACTIVE_MINES_API=true.",
    }
