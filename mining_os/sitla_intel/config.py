"""Feature flags for SITLA Intelligence."""

from __future__ import annotations

from mining_os.config import settings


def sitla_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_SITLA_API", False))


def sitla_admin_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_SITLA_ADMIN", False))


def sitla_jobs_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_SITLA_JOBS", False))
