"""Feature flags for the Tax Sales module."""

from __future__ import annotations

from mining_os.config import settings


def tax_sales_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_TAX_SALES_API", False))


def tax_sales_admin_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_TAX_SALES_ADMIN", False))


def tax_sales_jobs_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_TAX_SALES_JOBS", False))
