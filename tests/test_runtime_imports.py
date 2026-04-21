"""
Deploy-hygiene tests: fail fast if the prod environment is missing any
runtime dependency that the API relies on.

Motivated by a production incident where Render shipped without `requests`,
which caused LR2000 to error with "No module named 'requests'" and Fetch
Claim Records to silently return empty (both rely on `requests` to hit
BLM ArcGIS).

If a NEW dependency is introduced in the code, add it here AND to
requirements.txt + pyproject.toml.
"""

from __future__ import annotations

import importlib

import pytest


CRITICAL_RUNTIME_MODULES = [
    # Web framework
    "fastapi",
    "uvicorn",
    "starlette",
    "pydantic",
    "pydantic_settings",

    # Database
    "sqlalchemy",
    "psycopg",

    # External HTTP — used by BLM ArcGIS queries, LR2000 report,
    # Fetch Claim Records fallback, USGS downloads, etc.
    "requests",

    # AI + PLSS lookup
    "openai",
    "duckduckgo_search",

    # PDF processing
    "fitz",  # pymupdf
    "pypdf",

    # Scheduling
    "croniter",

    # Config
    "dotenv",
]


@pytest.mark.parametrize("module_name", CRITICAL_RUNTIME_MODULES)
def test_critical_module_importable(module_name: str):
    """Each runtime module the API uses must import cleanly."""
    importlib.import_module(module_name)


def test_api_main_imports_cleanly():
    """The FastAPI app module itself must import with no missing deps."""
    from mining_os.api import main  # noqa: F401


def test_blm_plss_service_imports_cleanly():
    """The service that hits BLM ArcGIS must import — depends on `requests`."""
    from mining_os.services import blm_plss  # noqa: F401
    assert hasattr(blm_plss, "query_claims_by_plss")
    assert hasattr(blm_plss, "query_claims_by_coords")


def test_mlrs_service_imports_cleanly():
    """LR2000 report service must import."""
    from mining_os.services import mlrs_geographic_index  # noqa: F401
    assert hasattr(mlrs_geographic_index, "run_lr2000_geographic_index_for_area")


def test_fetch_claim_records_service_imports_cleanly():
    from mining_os.services import fetch_claim_records  # noqa: F401
    assert hasattr(fetch_claim_records, "fetch_claim_records_for_area")
