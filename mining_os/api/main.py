"""
FastAPI backend for Mining_OS.

Endpoints:
  GET  /health               – liveness check
  GET  /candidates            – ranked list (filters: min_score, state, commodity)
  GET  /candidates/{id}       – full detail for a single candidate
  POST /run-pipeline/init-db  – initialise DB schema
  POST /run-pipeline/ingest   – run all ingestion steps
  POST /run-pipeline/candidates – rebuild candidate scoring
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.logging_setup import setup_logging

setup_logging("INFO")
log = logging.getLogger("mining_os.api")

api_app = FastAPI(title="Mining_OS API", version="0.1.0")


def _is_db_connection_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "connection" in msg and ("refused" in msg or "5432" in msg)


@api_app.exception_handler(Exception)
def handle_db_unavailable(request, exc):
    """Return 503 with a clear message when the database is unreachable."""
    if _is_db_connection_error(exc):
        log.warning("Database unavailable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "error": "database_unavailable",
                "detail": "Postgres is not running. Start Docker (docker compose up -d) then run: python -m mining_os.pipelines.run_all --init-db",
            },
        )
    raise exc


@api_app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@api_app.get("/diag/environment")
def diag_environment() -> Dict[str, Any]:
    """
    Production self-diagnostic.

    Returns a single JSON blob summarizing everything that typically breaks
    on Render/Railway when this app is deployed without the companion
    BLM_ClaimAgent repo or with an unreachable DB. Safe to hit from any
    browser — it never returns secrets, only their presence / prefixes.
    """
    import os
    import sys

    def _truthy(v: str | None) -> bool:
        return bool(v) and v.strip() != ""

    result: Dict[str, Any] = {
        "ok": True,
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }

    # Environment flags (presence only, never values)
    env_flags = {
        "DATABASE_URL": _truthy(os.getenv("DATABASE_URL")),
        "OPENAI_API_KEY": _truthy(os.getenv("OPENAI_API_KEY")),
        "SMTP_HOST": _truthy(os.getenv("SMTP_HOST")),
        "MINING_OS_BLM_AGENT_PATH": os.getenv("MINING_OS_BLM_AGENT_PATH") or None,
        "PORT": os.getenv("PORT"),
    }
    result["env"] = env_flags

    # Runtime-critical Python modules: if any of these are missing, at
    # least one user-facing action will break. This is the single best
    # signal that the deploy is broken (e.g. requirements.txt wasn't
    # installed, build command is wrong, etc.).
    critical_modules = [
        "fastapi", "pydantic", "sqlalchemy", "psycopg",
        "requests", "openai", "duckduckgo_search",
        "croniter", "fitz", "pypdf", "dotenv",
    ]
    module_status: Dict[str, Any] = {}
    missing: list[str] = []
    for mod in critical_modules:
        try:
            __import__(mod)
            module_status[mod] = True
        except ImportError as e:
            module_status[mod] = {"error": str(e)}
            missing.append(mod)
    result["modules"] = module_status
    if missing:
        result["ok"] = False
        result["modules_missing"] = missing

    # BLM_ClaimAgent companion repo detection
    try:
        from mining_os.services.fetch_claim_records import _blm_agent_path, _use_blm_claim_agent_script
        agent_path = _blm_agent_path()
        use_agent = _use_blm_claim_agent_script()
        result["blm_claim_agent"] = {
            "path": str(agent_path) if agent_path else None,
            "present": bool(agent_path),
            "use_agent_script_env": use_agent,
            "fetch_claim_records_mode": (
                "agent_script_selenium" if (agent_path and use_agent) else "arcgis_api_only"
            ),
            "note": (
                "BLM_ClaimAgent is optional — when missing, fetch-claim-records "
                "automatically falls back to the built-in BLM ArcGIS API."
            ) if not agent_path else (
                "Agent installed; slow Selenium path active (MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1)."
                if use_agent
                else "Agent installed but skipped by default — same fast ArcGIS path as production. "
                     "Set MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1 for Selenium payment scrape."
            ),
        }
    except Exception as e:
        result["blm_claim_agent"] = {"error": str(e)}

    # Database reachability
    try:
        from mining_os.db import get_engine
        eng = get_engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
            count_row = conn.execute(
                text("SELECT COUNT(*) FROM areas_of_focus")
            ).scalar()
        result["database"] = {"reachable": True, "areas_count": int(count_row or 0)}
    except Exception as e:
        result["database"] = {"reachable": False, "error": str(e)[:500]}
        result["ok"] = False

    # BLM ArcGIS reachability (same endpoint fetch-claim-records relies on)
    try:
        import requests
        r = requests.get(
            "https://gis.blm.gov/nlsdb/rest/services/HUB/"
            "BLM_Natl_MLRS_Mining_Claims_Not_Closed/FeatureServer/0/query",
            params={"where": "1=2", "outFields": "*", "f": "json"},
            timeout=15,
        )
        result["blm_arcgis"] = {
            "reachable": r.status_code == 200,
            "status_code": r.status_code,
            "content_type": r.headers.get("content-type"),
        }
        if r.status_code != 200:
            result["ok"] = False
    except Exception as e:
        result["blm_arcgis"] = {"reachable": False, "error": str(e)[:500]}
        result["ok"] = False

    # MLRS payment banner (Playwright) — same stack Fetch Claim Records uses after ArcGIS.
    try:
        from mining_os.services.mlrs_case_payment import _should_try_headless

        pw_import_ok = False
        try:
            import playwright  # noqa: F401

            pw_import_ok = True
        except ImportError:
            pass
        result["mlrs_payment"] = {
            "headless_will_run": _should_try_headless(),
            "MINING_OS_MLRS_PAYMENT_HEADLESS": os.getenv("MINING_OS_MLRS_PAYMENT_HEADLESS"),
            "playwright_package_installed": pw_import_ok,
            "note": (
                "For UNPAID/PAID from MLRS case pages, build must run "
                "`python -m playwright install chromium` and set MINING_OS_MLRS_PAYMENT_HEADLESS=1 "
                "on production (see render.yaml). Verify with GET /api/diag/check-payment?case_url=..."
            ),
        }
    except Exception as e:
        result["mlrs_payment"] = {"error": str(e)}

    return result


@api_app.get("/diag/area/{area_id}")
def diag_area(area_id: int) -> Dict[str, Any]:
    """
    Per-target diagnostic — reports exactly what the backend sees for this
    target (PLSS fields, coords) so you can tell at a glance whether the
    problem is missing PLSS, unparseable PLSS, missing coords, etc.
    """
    try:
        from mining_os.services.areas_of_focus import get_area
        area = get_area(area_id)
    except Exception as e:
        return {"ok": False, "error": f"DB lookup failed: {e}"}

    if not area:
        return {"ok": False, "error": f"Area {area_id} not found"}

    fields = {
        "id": area.get("id"),
        "name": area.get("name"),
        "location_plss": area.get("location_plss"),
        "state_abbr": area.get("state_abbr"),
        "meridian": area.get("meridian"),
        "township": area.get("township"),
        "range": area.get("range"),
        "section": area.get("section"),
        "latitude": area.get("latitude"),
        "longitude": area.get("longitude"),
    }

    # Try to parse the PLSS to see if parsing is the issue
    parse_result = None
    try:
        from mining_os.services.fetch_claim_records import _parse_plss_for_script
        parsed = _parse_plss_for_script(area.get("location_plss"))
        parse_result = {"parsed": parsed, "parseable": parsed is not None}
    except Exception as e:
        parse_result = {"parseable": False, "error": str(e)}

    has_components = bool(area.get("state_abbr") and area.get("township") and area.get("range"))
    has_coords = area.get("latitude") is not None and area.get("longitude") is not None

    return {
        "ok": True,
        "fields": fields,
        "has_stored_plss_components": has_components,
        "has_coords": has_coords,
        "plss_parse": parse_result,
        "would_skip_with": None if (has_components or parse_result and parse_result.get("parseable") or has_coords)
            else "No stored components, unparseable PLSS, and no coordinates — target has no usable location.",
    }


@api_app.post("/diag/fetch-claim-records/{area_id}")
def diag_fetch_claim_records(area_id: int) -> Dict[str, Any]:
    """
    Runs Fetch Claim Records exactly like the real endpoint but returns
    the FULL response (including the log) so you can see which pass found
    claims or why every pass failed.
    """
    return _safe_fetch_claim_records(area_id)


@api_app.post("/diag/lr2000/{area_id}")
def diag_lr2000(area_id: int) -> Dict[str, Any]:
    """Runs Run LR2000 Report exactly like the real endpoint (full response)."""
    return _safe_lr2000_report(area_id)


@api_app.get("/diag/check-payment")
def diag_check_payment(case_url: str) -> Dict[str, Any]:
    """
    Run only the MLRS payment-banner enrichment for one case URL.

    Example:
        GET /api/diag/check-payment?case_url=https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT101527746

    Returns ``payment_status`` plus the path that decided it (``payment_check_source``).
    Useful for proving Playwright/Selenium are actually working without rerunning
    the whole Fetch Claim Records pipeline.
    """
    try:
        from mining_os.services.mlrs_case_payment import check_payment_for_url

        return {"ok": True, "case_url": case_url, **check_payment_for_url(case_url)}
    except Exception as e:
        log.exception("diag_check_payment failed")
        return {"ok": False, "case_url": case_url, "error": str(e)}


def _safe_fetch_claim_records(area_id: int, progress_cb=None) -> Dict[str, Any]:
    """
    Safe wrapper for the BLM Fetch Claim Records action.

    Always returns a structured JSON payload (`ok`, `claims`, `error`, `log`,
    `fetched_at`) — never bubbles to a 500. Used by both the `/api`-prefixed
    and bare-prefix route variants so prod always sees a clean error message.
    """
    log.info("safe_fetch_claim_records CALLED area_id=%s", area_id)
    try:
        from mining_os.services.areas_of_focus import get_area
        from mining_os.services.fetch_claim_records import fetch_claim_records_for_area

        area = get_area(area_id)
        if not area:
            log.warning("fetch_claim_records: area_id=%s not found", area_id)
            return {
                "ok": False,
                "log": "",
                "claims": [],
                "error": "Area not found. The target may have been deleted or the ID is invalid.",
                "fetched_at": None,
            }
        log.info(
            "fetch_claim_records: area_id=%s plss=%s state=%s meridian=%s twp=%s rng=%s sec=%s lat=%s lon=%s",
            area_id, area.get("location_plss"), area.get("state_abbr"), area.get("meridian"),
            area.get("township"), area.get("range"), area.get("section"),
            area.get("latitude"), area.get("longitude"),
        )
        return fetch_claim_records_for_area(
            area_id,
            area.get("name") or "",
            area.get("location_plss"),
            state_abbr=area.get("state_abbr"),
            meridian=area.get("meridian"),
            township=area.get("township"),
            range_val=area.get("range"),
            section=area.get("section"),
            latitude=area.get("latitude"),
            longitude=area.get("longitude"),
            previous_claim_records=(area.get("characteristics") or {}).get("claim_records"),
            progress_cb=progress_cb,
        )
    except Exception as e:
        log.exception("safe_fetch_claim_records failed for area_id=%s: %s", area_id, e)
        return {
            "ok": False,
            "log": "",
            "claims": [],
            "error": f"Fetch Claim Records failed: {e}",
            "fetched_at": None,
        }


# --- Background job registry ---------------------------------------------
#
# Long-running endpoints (Fetch Claim Records, LR2000) used to hold a single
# HTTP connection open for several minutes while Playwright scraped MLRS.
# That broke through Vite/proxies/load balancers ("Failed to fetch") even
# when the work itself finished server-side.
#
# The fix: kick the work onto a daemon thread, return a job_id immediately,
# and let the client poll a tiny GET /jobs/{id} endpoint. Each poll request
# is fast so nothing in the request path can time out.
#
# Storage is in-process (dict) — fine because both Render and Railway run a
# single uvicorn worker. If we ever scale to multiple workers we'd need to
# move this to Postgres (the result is already persisted to characteristics
# by the underlying service so end-state recovery still works).

import threading
import uuid as _uuid
from datetime import datetime as _datetime, timezone as _timezone

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_JOBS_RETENTION_SECONDS = 60 * 60  # purge finished jobs older than 1 hour


def _now_iso() -> str:
    return _datetime.now(_timezone.utc).isoformat()


def _purge_old_jobs() -> None:
    cutoff = _datetime.now(_timezone.utc).timestamp() - _JOBS_RETENTION_SECONDS
    with _JOBS_LOCK:
        stale = [
            jid for jid, j in _JOBS.items()
            if j.get("status") in ("done", "error")
            and _datetime.fromisoformat(j["updated_at"]).timestamp() < cutoff
        ]
        for jid in stale:
            _JOBS.pop(jid, None)


def _new_job(kind: str, **extra: Any) -> str:
    _purge_old_jobs()
    job_id = _uuid.uuid4().hex
    now = _now_iso()
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            **extra,
        }
    return job_id


def _set_job(job_id: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(fields)
            _JOBS[job_id]["updated_at"] = _now_iso()


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _JOBS_LOCK:
        return dict(_JOBS[job_id]) if job_id in _JOBS else None


def _run_job(job_id: str, fn, *args, **kwargs) -> None:
    """Run *fn* on a daemon thread and store its return value in the job."""
    def _worker() -> None:
        _set_job(job_id, status="running")
        try:
            result = fn(*args, **kwargs)
            _set_job(job_id, status="done", result=result)
        except Exception as exc:  # pragma: no cover - background path
            log.exception("job %s failed: %s", job_id, exc)
            _set_job(job_id, status="error", error=str(exc))
    threading.Thread(target=_worker, name=f"job-{job_id[:8]}", daemon=True).start()


def _safe_clear_characteristic_keys(area_id: int, keys: list[str]) -> Dict[str, Any]:
    """Remove known snapshot keys from ``characteristics`` — never raises."""
    try:
        from mining_os.services.areas_of_focus import get_area, remove_area_characteristic_keys

        area = get_area(area_id)
        if not area:
            return {"ok": False, "error": "Area not found.", "removed": []}
        ok = remove_area_characteristic_keys(area_id, keys)
        removed = [k for k in keys if k in ("claim_records", "lr2000_geographic_index")]
        return {
            "ok": bool(ok),
            "removed": removed,
            "error": None if ok else "Could not update characteristics.",
        }
    except Exception as e:
        log.exception("safe_clear_characteristic_keys failed area_id=%s keys=%s: %s", area_id, keys, e)
        return {"ok": False, "removed": [], "error": str(e)}


def _safe_lr2000_report(area_id: int) -> Dict[str, Any]:
    """Safe wrapper for the LR2000 / MLRS Geographic Index report (always structured JSON)."""
    log.info("safe_lr2000_report CALLED area_id=%s", area_id)
    try:
        from mining_os.services.areas_of_focus import get_area
        from mining_os.services.mlrs_geographic_index import run_lr2000_geographic_index_for_area

        area = get_area(area_id)
        if not area:
            return {
                "ok": False,
                "error": "Area not found. The target may have been deleted or the ID is invalid.",
                "claims": [],
                "fetched_at": None,
                "query_method": None,
                "log": "",
                "input": {},
                "source": None,
            }
        return run_lr2000_geographic_index_for_area(area_id, area)
    except Exception as e:
        log.exception("safe_lr2000_report failed for area_id=%s: %s", area_id, e)
        return {
            "ok": False,
            "error": f"LR2000 report failed: {e}",
            "claims": [],
            "fetched_at": None,
            "query_method": None,
            "log": "",
            "input": {},
            "source": None,
        }


@api_app.get("/map/sma-query")
def map_sma_query(
    lat: float = Query(..., ge=-90, le=90, description="WGS84 latitude"),
    lng: float = Query(..., ge=-180, le=180, description="WGS84 longitude"),
) -> Dict[str, Any]:
    """
    Proxy BLM National Surface Management Agency (SMA) feature query at a point.
    Used by the Map page for click-to-identify land manager (federal / state / private classification).
    """
    import requests as req_lib

    url = (
        "https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_Cached_with_PriUnk/MapServer/1/query"
    )
    params = {
        "f": "json",
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "resultRecordCount": "5",
    }
    try:
        r = req_lib.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except req_lib.RequestException as e:
        log.warning("map/sma-query BLM request failed: %s", e)
        raise HTTPException(status_code=502, detail=f"BLM SMA service unavailable: {e}") from e


@api_app.get("/candidates")
def list_candidates(
    limit: int = Query(200, ge=1, le=2000),
    min_score: int = Query(0, ge=0, le=100),
    state: Optional[str] = None,
    commodity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return candidates ordered by score (descending), with optional filters."""
    eng = get_engine()

    filters = ["score >= :min_score"]
    params: Dict[str, Any] = {"min_score": min_score, "limit": limit}

    if state:
        filters.append("state_abbr = :state")
        params["state"] = state.strip().upper()

    if commodity:
        filters.append(":commodity = ANY(commodities)")
        params["commodity"] = commodity.strip().lower()

    where = " AND ".join(filters)
    sql = f"""
    SELECT
      id, state_abbr, serial_num, claim_name, claim_type,
      case_status, case_disposition, trs,
      mrds_hit_count, commodities, has_reference_text, score,
      ST_Y(geom_centroid::geometry) AS lat,
      ST_X(geom_centroid::geometry) AS lon
    FROM candidates
    WHERE {where}
    ORDER BY score DESC
    LIMIT :limit;
    """
    with eng.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]


@api_app.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: int) -> Dict[str, Any]:
    eng = get_engine()
    sql = """
    SELECT
      id, claim_table, claim_id, state_abbr, serial_num, claim_name, claim_type,
      case_status, case_disposition, trs,
      mrds_hit_count, commodities, has_reference_text, score,
      ST_AsGeoJSON(geom) AS geom_geojson,
      ST_Y(geom_centroid::geometry) AS lat,
      ST_X(geom_centroid::geometry) AS lon
    FROM candidates
    WHERE id = :id;
    """
    with eng.begin() as conn:
        row = conn.execute(text(sql), {"id": candidate_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return dict(row)


# ---- Pipeline triggers (convenience for dashboard) -----------------------


@api_app.post("/run-pipeline/init-db")
def run_init_db() -> Dict[str, str]:
    from mining_os.pipelines.run_all import init_db

    init_db()
    return {"status": "initialised"}


@api_app.post("/run-pipeline/ingest")
def run_ingest(max_records: Optional[int] = None) -> Dict[str, Any]:
    from mining_os.pipelines.ingest_blm_claims import ingest_closed, ingest_open
    from mining_os.pipelines.ingest_mrds import ingest as ingest_mrds
    from mining_os.pipelines.ingest_plss import ingest as ingest_plss

    ingest_open(max_records=max_records)
    ingest_closed(max_records=max_records)
    ingest_plss(max_records=max_records)
    ingest_mrds(max_records=max_records or 20000)
    return {"status": "ingested", "max_records": max_records}


@api_app.post("/run-pipeline/candidates")
def run_candidates() -> Dict[str, str]:
    from mining_os.pipelines.build_candidates import build

    build()
    return {"status": "candidates built"}


# ---- Minerals of interest (editable list) -----------------------------------


@api_app.get("/minerals")
def api_list_minerals() -> List[Dict[str, Any]]:
    from mining_os.services.minerals import list_minerals
    return list_minerals()


@api_app.post("/minerals")
def api_add_mineral(name: str, sort_order: Optional[int] = None) -> Dict[str, Any]:
    from mining_os.services.minerals import add_mineral
    return add_mineral(name, sort_order)


@api_app.get("/minerals/{mineral_id}/report")
def api_mineral_report(mineral_id: int) -> Dict[str, Any]:
    """AI-generated detailed report for the mineral (uses, buyers, major miners, formations, locations, mining/milling)."""
    from mining_os.services.minerals import get_mineral
    from mining_os.services.mineral_report import get_mineral_report
    m = get_mineral(mineral_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mineral not found")
    return get_mineral_report(m["name"])


@api_app.get("/minerals/{mineral_id}")
def api_get_mineral(mineral_id: int) -> Dict[str, Any]:
    from mining_os.services.minerals import get_mineral
    m = get_mineral(mineral_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mineral not found")
    return m


@api_app.put("/minerals/{mineral_id}")
def api_update_mineral(mineral_id: int, name: Optional[str] = None, sort_order: Optional[int] = None) -> Dict[str, Any]:
    from mining_os.services.minerals import update_mineral
    m = update_mineral(mineral_id, name=name, sort_order=sort_order)
    if not m:
        raise HTTPException(status_code=404, detail="Mineral not found")
    return m


@api_app.delete("/minerals/{mineral_id}")
def api_delete_mineral(mineral_id: int) -> Dict[str, str]:
    from mining_os.services.minerals import delete_mineral
    if not delete_mineral(mineral_id):
        raise HTTPException(status_code=404, detail="Mineral not found")
    return {"status": "deleted"}


# ---- Areas of focus ---------------------------------------------------------


@api_app.get("/areas-of-focus")
def api_list_areas(
    mineral: Optional[str] = None,
    status: Optional[str] = None,
    state_abbr: Optional[str] = None,
    claim_type: Optional[str] = None,
    retrieval_type: Optional[str] = None,
    township: Optional[str] = None,
    range_val: Optional[str] = None,
    sector: Optional[str] = None,
    name: Optional[str] = None,
    limit: int = Query(500, ge=1, le=2000),
) -> List[Dict[str, Any]]:
    from mining_os.services.areas_of_focus import list_areas
    return list_areas(
        mineral=mineral,
        status=status,
        state_abbr=state_abbr,
        claim_type=claim_type,
        retrieval_type=retrieval_type,
        township=township,
        range_val=range_val,
        sector=sector,
        name=name,
        limit=limit,
    )


class CreateAreaBody(BaseModel):
    name: str
    location_plss: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    report_url: Optional[str] = None
    information: Optional[str] = None
    status: Optional[str] = None
    minerals: Optional[List[str]] = None
    priority: Optional[str] = None


def _finite_coord(v: Any) -> bool:
    try:
        f = float(v)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False


def _create_area_from_body(body: CreateAreaBody) -> Dict[str, Any]:
    """Shared create/merge logic: require name and either PLSS or both coordinates."""
    from mining_os.services.areas_of_focus import upsert_area, _normalize_target_status

    name = (body.name or "").strip()
    plss = (body.location_plss or "").strip()
    has_coords = _finite_coord(body.latitude) and _finite_coord(body.longitude)
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not plss and not has_coords:
        raise HTTPException(
            status_code=400,
            detail="Provide location_plss or both latitude and longitude",
        )
    report_links = [body.report_url] if body.report_url and body.report_url.strip() else []
    status_val = (body.status or "").strip().lower() or None
    if status_val and status_val not in ("paid", "unpaid", "unknown"):
        status_val = "unknown"
    minerals = body.minerals or []
    priority_val = _normalize_target_status(body.priority)
    lat_f = float(body.latitude) if has_coords else None
    lon_f = float(body.longitude) if has_coords else None
    location_coords = f"{lat_f}, {lon_f}" if has_coords and not plss else None
    skip_geo = bool(has_coords and not plss)
    area_id = upsert_area(
        name=name,
        location_plss=plss or None,
        location_coords=location_coords,
        latitude=lat_f,
        longitude=lon_f,
        report_links=report_links or None,
        validity_notes=(body.information or "").strip() or None,
        status=status_val,
        minerals=minerals if minerals else None,
        priority=priority_val,
        source="manual",
        is_uploaded=True,
        skip_plss_geocode=skip_geo,
    )
    return {
        "id": area_id,
        "name": name,
        "location_plss": plss or None,
        "latitude": lat_f,
        "longitude": lon_f,
    }


@api_app.post("/areas-of-focus")
def api_create_area(body: CreateAreaBody) -> Dict[str, Any]:
    """Create or merge a single target. Requires name and (PLSS or lat+lon)."""
    return _create_area_from_body(body)


@api_app.get("/areas-of-focus/minerals")
def api_list_area_minerals() -> List[str]:
    """Distinct mineral names from targets (for mineral filter autocomplete)."""
    from mining_os.services.areas_of_focus import list_distinct_minerals
    return list_distinct_minerals()


# Literal paths before {area_id} so they are not matched as GET /areas-of-focus/{area_id}
@api_app.get("/areas-of-focus/clean-preview")
def api_clean_preview() -> Dict[str, Any]:
    """Targets with no PLSS and duplicate groups by PLSS for the Clean Targets modal."""
    from mining_os.services.areas_of_focus import get_clean_preview
    return get_clean_preview()


@api_app.post("/areas-of-focus/fill-plss-ai")
def api_fill_plss_ai(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Infer PLSS from mine name, state, county (from notes), optional web snippets; update selected targets."""
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Request body must include ids: number[]")
    try:
        from mining_os.services.plss_ai_lookup import lookup_plss_for_target_ids

        return lookup_plss_for_target_ids([int(x) for x in ids])
    except HTTPException:
        raise
    except Exception as e:
        log.exception("fill-plss-ai failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_app.post("/areas-of-focus/fill-plss-ai-preview")
def api_fill_plss_ai_preview(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Same inference as fill-plss-ai but does not write to the DB; returns proposals for user review."""
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Request body must include ids: number[]")
    try:
        from mining_os.services.plss_ai_lookup import lookup_plss_for_target_ids

        return lookup_plss_for_target_ids([int(x) for x in ids], dry_run=True)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("fill-plss-ai-preview failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_app.post("/areas-of-focus/fill-plss-ai-apply")
def api_fill_plss_ai_apply(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Apply reviewed PLSS proposals from fill-plss-ai-preview. Body: { \"items\": [ { id, plss, ... } ] }."""
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Request body must include items: array")
    try:
        from mining_os.services.plss_ai_lookup import apply_plss_ai_proposals

        return apply_plss_ai_proposals(items)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("fill-plss-ai-apply failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class ConsolidateBody(BaseModel):
    keep_id: int
    merge_ids: List[int] = []


class BatchAreaIdsBody(BaseModel):
    """Target ids for sequential batch BLM actions (max 25 per request)."""

    ids: List[int]


@api_app.post("/areas-of-focus/consolidate")
def api_consolidate_duplicates(body: ConsolidateBody) -> Dict[str, Any]:
    """Merge duplicate targets into one. keep_id is kept; merge_ids are merged into it and deleted."""
    from mining_os.services.areas_of_focus import consolidate_duplicates
    merge_ids = [i for i in body.merge_ids if i != body.keep_id]
    return consolidate_duplicates(body.keep_id, merge_ids)


@api_app.post("/areas-of-focus/ingest")
def api_ingest_areas() -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import ingest_from_data_files
    return ingest_from_data_files()


@api_app.post("/areas-of-focus/import-csv-inspect")
async def api_import_csv_inspect(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Return CSV headers, sample rows, and suggested column mapping (no DB writes)."""
    from mining_os.services.areas_of_focus import inspect_csv_import

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    result = inspect_csv_import(content)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@api_app.post("/areas-of-focus/import-csv")
async def api_import_csv(
    file: UploadFile = File(...),
    bulk_priority: Optional[str] = Form(None),
    bulk_report_url: Optional[str] = Form(None),
    bulk_mineral: Optional[str] = Form(None),
    conflict_strategy: Optional[str] = Form(None),
    column_mapping: Optional[str] = Form(None),
    inspect_only: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """
    Import targets from CSV. Requires columns: Name, State (2-letter), and PLSS (or Location, or Township+Range+Section).
    Optional JSON column_mapping maps canonical fields (name, state, plss, township, range, section, …) to CSV header names.
    State is stored on the target; PLSS is stored and parsed into Township, Range, Section (sector).
    Optional: bulk_priority (low|medium|high), bulk_report_url (PDF link for all).
    If conflict_strategy omitted: returns preview with conflicts. If set (merge|use_old|use_new): applies import.
    If inspect_only is true: returns headers, sample rows, and suggested mapping only (same as import-csv-inspect).
    """
    log.info("api_import_csv handler: processing CSV import (via api_app mount)")
    import json

    from mining_os.services.areas_of_focus import inspect_csv_import, preview_csv_import, apply_csv_import

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    if inspect_only and str(inspect_only).strip().lower() in ("1", "true", "yes", "on"):
        result = inspect_csv_import(content)
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    mapping_obj: Optional[Dict[str, Any]] = None
    if column_mapping and column_mapping.strip():
        try:
            mapping_obj = json.loads(column_mapping)
            if not isinstance(mapping_obj, dict):
                raise ValueError("column_mapping must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid column_mapping JSON: {e}") from e
    preview = preview_csv_import(
        content,
        bulk_priority=bulk_priority,
        bulk_report_url=bulk_report_url,
        bulk_mineral=bulk_mineral,
        column_mapping=mapping_obj,
    )
    if preview.get("errors"):
        raise HTTPException(status_code=400, detail="; ".join(preview["errors"][:3]))
    valid = preview["valid_rows"]
    conflicts = preview["conflicts"]
    skip_reasons = preview.get("skip_reasons") or []
    source_row_count = int(preview.get("source_row_count") or 0)
    debug_first_row = preview.get("debug_first_row") or {}
    n_valid = len(valid)
    log.info("api_import_csv: source_rows=%d valid=%d skipped=%d conflicts=%d strategy=%s",
             source_row_count, n_valid, preview["skipped"], len(conflicts), conflict_strategy)
    if conflict_strategy is None:
        if n_valid == 0:
            if source_row_count == 0:
                msg = "No data rows found after the header. Add rows to the CSV or re-export the file."
            else:
                msg = (
                    f"No rows could be imported — {preview['skipped']} of {source_row_count} data row(s) were skipped "
                    f"(missing name, unparseable PLSS, or location could not be normalized)."
                )
                if skip_reasons:
                    msg += " Examples: " + " | ".join(skip_reasons[:3])
        elif conflicts:
            msg = f"{n_valid} row(s) valid; {len(conflicts)} already exist (choose merge / use old / use new)."
        else:
            msg = f"{n_valid} row(s) ready to import."
        return {
            "preview": True,
            "valid_rows": n_valid,
            "skipped": preview["skipped"],
            "skip_reasons": skip_reasons,
            "source_row_count": source_row_count,
            "conflicts": conflicts,
            "message": msg,
            "debug_first_row": debug_first_row,
        }
    if conflict_strategy not in ("merge", "use_old", "use_new"):
        raise HTTPException(status_code=400, detail="conflict_strategy must be merge, use_old, or use_new")
    result = apply_csv_import(
        valid, conflict_strategy,
        bulk_priority=bulk_priority, bulk_report_url=bulk_report_url, bulk_mineral=bulk_mineral,
    )
    return {
        "preview": False,
        "applied": result["applied"],
        "merged": result["merged"],
        "skipped": result["skipped"],
        "errors": result["errors"],
        "applied_names": result.get("applied_names", []),
        "merged_names": result.get("merged_names", []),
    }


@api_app.post("/areas-of-focus/plss-from-coordinates-batch")
def api_plss_from_coordinates_batch() -> Dict[str, Any]:
    """Batch: all targets with lat/lon and no plss_normalized."""
    from mining_os.services.areas_of_focus import batch_reverse_plss_from_coordinates

    return batch_reverse_plss_from_coordinates()


@api_app.post("/areas-of-focus/batch/fetch-claim-records")
def api_batch_fetch_claim_records(body: BatchAreaIdsBody = Body(...)) -> Dict[str, Any]:
    """Run MLRS scrape (BLM_ClaimAgent path) for each target id, sequentially."""
    from mining_os.services.area_batch_actions import batch_fetch_claim_records

    return batch_fetch_claim_records(body.ids)


@api_app.post("/areas-of-focus/batch/lr2000-geographic-report")
def api_batch_lr2000_geographic_report(body: BatchAreaIdsBody = Body(...)) -> Dict[str, Any]:
    """Run in-app LR2000 / Geographic Index query for each target id, sequentially."""
    from mining_os.services.area_batch_actions import batch_lr2000_geographic_report

    return batch_lr2000_geographic_report(body.ids)


@api_app.get("/areas-of-focus/{area_id}")
def api_get_area(area_id: int) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import get_area
    a = get_area(area_id)
    if not a:
        raise HTTPException(status_code=404, detail="Area not found")
    return a


@api_app.delete("/areas-of-focus/{area_id}")
def api_delete_area(area_id: int) -> Dict[str, str]:
    from mining_os.services.areas_of_focus import delete_area
    if not delete_area(area_id):
        raise HTTPException(status_code=404, detail="Target not found")
    return {"status": "deleted"}


class AreaPriorityBody(BaseModel):
    priority: str


@api_app.post("/areas-of-focus/{area_id}/priority")
def api_set_area_priority(area_id: int, body: AreaPriorityBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_priority
    ok = update_area_priority(area_id, body.priority)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid target status")
    return {"id": area_id, "priority": body.priority.lower()}


class AreaClaimTypeBody(BaseModel):
    claim_type: Optional[str] = None


@api_app.post("/areas-of-focus/{area_id}/claim-type")
def api_set_area_claim_type(area_id: int, body: AreaClaimTypeBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_claim_type
    ok = update_area_claim_type(area_id, body.claim_type)
    if not ok:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"id": area_id, "claim_type": body.claim_type}


class AreaNotesBody(BaseModel):
    notes: Optional[str] = None


@api_app.post("/areas-of-focus/{area_id}/notes")
def api_set_area_notes(area_id: int, body: AreaNotesBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_notes
    ok = update_area_notes(area_id, body.notes)
    if not ok:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"id": area_id, "notes": body.notes}


class AreaMineralsBody(BaseModel):
    minerals: List[str] = []


@api_app.post("/areas-of-focus/{area_id}/minerals")
def api_set_area_minerals(area_id: int, body: AreaMineralsBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_minerals
    ok = update_area_minerals(area_id, body.minerals)
    if not ok:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"id": area_id, "minerals": body.minerals}


class AreaNameBody(BaseModel):
    name: str


@api_app.post("/areas-of-focus/{area_id}/name")
def api_set_area_name(area_id: int, body: AreaNameBody = Body(...)) -> Dict[str, Any]:
    """Rename a target. 400 if name is empty; 404 if target doesn't exist."""
    from mining_os.services.areas_of_focus import get_area, update_area_name
    if not (body.name or "").strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not get_area(area_id):
        raise HTTPException(status_code=404, detail="Target not found")
    ok = update_area_name(area_id, body.name)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to update name")
    return {"id": area_id, "name": body.name.strip()[:500]}


class AreaPlssBody(BaseModel):
    location_plss: Optional[str] = None
    regeocode_coordinates: bool = True


class AreaPlssComponentsBody(BaseModel):
    state_abbr: Optional[str] = None
    township: Optional[str] = None
    range_val: Optional[str] = None
    section: Optional[str] = None
    meridian: Optional[str] = None
    regeocode_coordinates: bool = True


class AreaCoordinatesBody(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@api_app.post("/areas-of-focus/{area_id}/coordinates")
def api_set_area_coordinates(area_id: int, body: AreaCoordinatesBody = Body(...)) -> Dict[str, Any]:
    """Set WGS84 latitude/longitude on a target (both required when updating)."""
    from mining_os.services.areas_of_focus import get_area, update_area_coordinates

    if not get_area(area_id):
        raise HTTPException(status_code=404, detail="Target not found")
    if body.latitude is None or body.longitude is None:
        raise HTTPException(status_code=400, detail="latitude and longitude are required")
    if not _finite_coord(body.latitude) or not _finite_coord(body.longitude):
        raise HTTPException(status_code=400, detail="latitude and longitude must be finite numbers")
    update_area_coordinates(area_id, float(body.latitude), float(body.longitude))
    return {"id": area_id, "latitude": float(body.latitude), "longitude": float(body.longitude)}


@api_app.post("/areas-of-focus/{area_id}/plss-from-coordinates")
def api_plss_from_coordinates(area_id: int) -> Dict[str, Any]:
    """Resolve PLSS from stored coordinates via BLM Cadastral and save (keeps lat/lon)."""
    from mining_os.services.areas_of_focus import reverse_plss_from_coordinates_for_area

    return reverse_plss_from_coordinates_for_area(area_id)


@api_app.post("/areas-of-focus/{area_id}/plss")
def api_set_area_plss(area_id: int, body: AreaPlssBody = Body(...)) -> Dict[str, Any]:
    """
    User-initiated PLSS edit. Parses the provided string, overwrites state/
    township/range/section/meridian, and (by default) re-geocodes lat/lon
    from the new PLSS via BLM Cadastral.
    """
    from mining_os.services.areas_of_focus import update_area_plss
    return update_area_plss(
        area_id,
        body.location_plss,
        regeocode_coordinates=body.regeocode_coordinates,
    )


@api_app.post("/areas-of-focus/{area_id}/plss-components")
def api_set_area_plss_components(
    area_id: int, body: AreaPlssComponentsBody = Body(...)
) -> Dict[str, Any]:
    """
    User-initiated PLSS edit by individual Township / Range / Section /
    State / Meridian fields. Each input is normalized (``T12S`` → ``0120S``,
    ``Sec 35`` → ``035``) and persisted to the dedicated columns;
    ``location_plss`` is rebuilt as a canonical string so downstream
    consumers (Fetch Claim Records, BLM LR2000, etc.) pick up the change.
    """
    from mining_os.services.areas_of_focus import update_area_plss_components
    return update_area_plss_components(
        area_id,
        state_abbr=body.state_abbr,
        township=body.township,
        range_val=body.range_val,
        section=body.section,
        meridian=body.meridian,
        regeocode_coordinates=body.regeocode_coordinates,
    )


@api_app.post("/areas-of-focus/{area_id}/check-blm")
def api_check_blm(area_id: int) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import get_area
    from mining_os.services.blm_check import check_area_by_coords

    area = get_area(area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    lat, lon = area.get("latitude"), area.get("longitude")
    if lat is None or lon is None:
        return {"area_id": area_id, "error": "No coordinates; add latitude/longitude to run BLM check"}
    result = check_area_by_coords(area_id, float(lat), float(lon))
    if result is None:
        return {"area_id": area_id, "error": "BLM agent not available or no claims found"}
    return result


@api_app.post("/areas-of-focus/{area_id}/fetch-claim-records")
def api_fetch_claim_records(area_id: int) -> Dict[str, Any]:
    """Run BLM claim search using stored PLSS fields + spatial fallback. Returns 200 with error in body when area not found."""
    return _safe_fetch_claim_records(area_id)


@api_app.post("/areas-of-focus/{area_id}/fetch-claim-records/start")
def api_fetch_claim_records_start(area_id: int) -> Dict[str, Any]:
    """Start the BLM claim search on a background thread. Returns ``{job_id}`` so the client can poll ``/jobs/{job_id}``."""
    job_id = _new_job("fetch_claim_records", area_id=area_id)
    _set_job(
        job_id,
        progress={
            "phase": "queued",
            "message": "Queued Fetch Claim Records job…",
        },
    )

    def _job_progress(payload: Dict[str, Any]) -> None:
        _set_job(job_id, progress=payload)

    _run_job(job_id, _safe_fetch_claim_records, area_id, progress_cb=_job_progress)
    return {"ok": True, "job_id": job_id}


@api_app.get("/jobs/{job_id}")
def api_get_job(job_id: str) -> Dict[str, Any]:
    """Poll endpoint for background jobs (Fetch Claim Records, LR2000, etc.)."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@api_app.post("/areas-of-focus/{area_id}/lr2000-geographic-report")
def api_lr2000_geographic_report(area_id: int) -> Dict[str, Any]:
    """MLRS geographic mining-claims query (same national layer as BLM report 104), in-app — no browser."""
    return _safe_lr2000_report(area_id)


@api_app.post("/areas-of-focus/{area_id}/clear-claim-records")
def api_clear_claim_records_snapshot(area_id: int) -> Dict[str, Any]:
    """Remove stored ``characteristics.claim_records`` snapshot for this target."""
    return _safe_clear_characteristic_keys(area_id, ["claim_records"])


@api_app.post("/areas-of-focus/{area_id}/clear-lr2000-report")
def api_clear_lr2000_snapshot(area_id: int) -> Dict[str, Any]:
    """Remove stored ``characteristics.lr2000_geographic_index`` snapshot for this target."""
    return _safe_clear_characteristic_keys(area_id, ["lr2000_geographic_index"])


@api_app.post("/areas-of-focus/{area_id}/generate-report")
def api_generate_report(area_id: int) -> Dict[str, Any]:
    """Generate a Mineral Report for the target using OpenAI. Returns { ok, report, error }."""
    from mining_os.services.mineral_report import generate_report
    return generate_report(area_id, fetch_reports=True)


@api_app.post("/alerts/send-priority-unpaid")
def api_send_priority_unpaid() -> Dict[str, Any]:
    """Find areas with priority minerals and unpaid status; email ALERT_EMAIL."""
    from mining_os.config import settings
    from mining_os.services.minerals import list_minerals
    from mining_os.services.areas_of_focus import list_areas
    from mining_os.services.email_alerts import send_priority_unpaid_alert

    priority = {m["name"].lower() for m in list_minerals()}
    areas = list_areas(limit=1000)
    unpaid = [
        a for a in areas
        if (a.get("status") or "").lower() in ("unpaid", "unknown")
        and priority.intersection({str(x).lower() for x in (a.get("minerals") or [])})
    ]
    if not unpaid:
        return {
            "sent": False,
            "email_sent": False,
            "count": 0,
            "recipient": settings.ALERT_EMAIL,
            "message": (
                "No targets to email. Need at least one target whose minerals include a name from "
                "your Minerals-of-interest list, with claim status unpaid or unknown."
            ),
        }
    email_ok, err_detail = send_priority_unpaid_alert(unpaid)
    if email_ok:
        return {
            "sent": True,
            "email_sent": True,
            "count": len(unpaid),
            "recipient": settings.ALERT_EMAIL,
            "message": f"Email sent to {settings.ALERT_EMAIL} for {len(unpaid)} target(s).",
        }
    return {
        "sent": False,
        "email_sent": False,
        "count": len(unpaid),
        "recipient": settings.ALERT_EMAIL,
        "message": err_detail or "Email could not be sent.",
    }


# ---- Discovery agent (prompts + run) -----------------------------------------


@api_app.get("/discovery/prompts")
def api_get_discovery_prompts() -> List[Dict[str, Any]]:
    from mining_os.services.discovery_prompts import get_all_prompts
    return get_all_prompts()


@api_app.get("/discovery/prompts/default")
def api_get_default_prompt() -> Dict[str, Any]:
    """Return the default prompt (mineral_name = '') so the UI can always show it."""
    from mining_os.services.discovery_prompts import get_prompt_for_mineral
    row = get_prompt_for_mineral("")
    if not row:
        return {"mineral_name": "", "system_instruction": "", "user_prompt_template": ""}
    if row.get("mineral_name") is None:
        row = dict(row, mineral_name="")
    return row


class DiscoveryPromptBody(BaseModel):
    mineral_name: str = ""  # "" = default for all minerals
    system_instruction: str
    user_prompt_template: str


@api_app.put("/discovery/prompts")
def api_upsert_discovery_prompt(body: DiscoveryPromptBody = Body(...)) -> Dict[str, str]:
    from mining_os.services.discovery_prompts import upsert_prompt
    upsert_prompt(
        mineral_name=body.mineral_name,
        system_instruction=body.system_instruction,
        user_prompt_template=body.user_prompt_template,
    )
    return {"status": "saved"}


@api_app.post("/discovery/run")
def api_run_discovery(
    replace: bool = Query(False, description="True = replace discovery-sourced areas; False = add/supplement"),
    limit_per_mineral: int = Query(25, ge=1, le=100),
) -> Dict[str, Any]:
    """Always returns 200 with JSON. Discovery runs in-process."""
    try:
        from mining_os.services.discovery_agent import run_discovery
        from mining_os.services.discovery_runs import create_run
        log_lines: List[str] = []
        result = run_discovery(replace=replace, limit_per_mineral=limit_per_mineral, log_lines=log_lines)
        try:
            create_run(
                replace=replace,
                limit_per_mineral=limit_per_mineral,
                status=result.get("status", "ok"),
                message=result.get("message"),
                minerals_checked=result.get("minerals_checked"),
                areas_added=result.get("areas_added", 0),
                log=result.get("log"),
                errors=result.get("errors"),
                locations_from_ai=result.get("locations_from_ai"),
                urls_from_web_search=result.get("urls_from_web_search"),
            )
        except Exception as save_err:
            log.warning("Could not save discovery run to log: %s", save_err)
        return result
    except Exception as e:
        log.exception("Discovery run failed: %s", e)
        msg = str(e)
        if "invalid_api_key" in msg or "401" in msg or "Incorrect API key" in msg:
            msg = "OpenAI API key is invalid or expired. In .env set OPENAI_API_KEY to a key from https://platform.openai.com/account/api-keys (starts with sk-)."
        elif "Expecting value" in msg or "line 1 column 1" in msg or "char 0" in msg:
            msg = "A service returned invalid data. Try again in a moment."
        out = {"status": "error", "message": msg, "log": [f"Error: {msg}"], "areas_added": 0, "minerals_checked": [], "errors": [], "locations_from_ai": [], "urls_from_web_search": []}
        try:
            from mining_os.services.discovery_runs import create_run
            create_run(replace=replace, limit_per_mineral=limit_per_mineral, status="error", message=msg, areas_added=0, log=out.get("log"), errors=None)
        except Exception:
            pass
        return out


@api_app.get("/discovery/runs")
def api_list_discovery_runs(limit: int = Query(50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """List past discovery runs (newest first). Never 500."""
    try:
        from mining_os.services.discovery_runs import list_runs
        return list_runs(limit=limit)
    except Exception as e:
        log.warning("list_runs failed: %s", e)
        return []


@api_app.get("/discovery/runs/{run_id}")
def api_get_discovery_run(run_id: int) -> Dict[str, Any]:
    """Full details of one discovery run. Returns 404 if not found; never 500."""
    try:
        from mining_os.services.discovery_runs import get_run
        run = get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Discovery run not found")
        return run
    except HTTPException:
        raise
    except Exception as e:
        log.warning("get_run failed: %s", e)
        raise HTTPException(status_code=404, detail="Discovery run not found")


# ---- Automation Engine -------------------------------------------------------


class AutomationRuleBody(BaseModel):
    name: str
    action_type: str
    filter_config: Optional[Dict[str, Any]] = None
    outcome_type: str = "log_only"
    schedule_cron: Optional[str] = None
    max_targets: int = 50
    enabled: bool = True


@api_app.get("/automations/rules")
def api_list_automation_rules() -> List[Dict[str, Any]]:
    from mining_os.services.automation_engine import list_rules
    return list_rules()


@api_app.get("/automations/rules/{rule_id}")
def api_get_automation_rule(rule_id: int) -> Dict[str, Any]:
    from mining_os.services.automation_engine import get_rule
    rule = get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@api_app.post("/automations/rules")
def api_create_automation_rule(body: AutomationRuleBody) -> Dict[str, Any]:
    from mining_os.services.automation_engine import create_rule
    try:
        return create_rule(
            name=body.name,
            action_type=body.action_type,
            filter_config=body.filter_config,
            outcome_type=body.outcome_type,
            schedule_cron=body.schedule_cron,
            max_targets=body.max_targets,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_app.put("/automations/rules/{rule_id}")
def api_update_automation_rule(rule_id: int, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    from mining_os.services.automation_engine import update_rule
    try:
        result = update_rule(rule_id, **body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Rule not found")
    return result


@api_app.delete("/automations/rules/{rule_id}")
def api_delete_automation_rule(rule_id: int) -> Dict[str, str]:
    from mining_os.services.automation_engine import delete_rule
    if not delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "deleted"}


@api_app.get("/automations/runs")
def api_list_automation_runs(
    rule_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> List[Dict[str, Any]]:
    from mining_os.services.automation_engine import list_runs
    return list_runs(rule_id=rule_id, limit=limit, offset=offset)


@api_app.get("/automations/runs/{run_id}")
def api_get_automation_run(run_id: int) -> Dict[str, Any]:
    from mining_os.services.automation_engine import get_run
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@api_app.post("/automations/rules/{rule_id}/trigger")
def api_trigger_automation_rule(rule_id: int) -> Dict[str, Any]:
    from mining_os.services.automation_engine import execute_rule
    return execute_rule(rule_id, trigger_type="manual")


@api_app.get("/automations/meta")
def api_automation_meta() -> Dict[str, Any]:
    from mining_os.services.automation_engine import ACTION_TYPES, OUTCOME_TYPES, FILTER_KEYS
    from mining_os.services.automation_scheduler import is_running
    return {
        "action_types": ACTION_TYPES,
        "outcome_types": OUTCOME_TYPES,
        "filter_keys": FILTER_KEYS,
        "scheduler_running": is_running(),
    }


# ---- Serve React SPA when frontend is built ---------------------------------

app = FastAPI(title="Mining_OS")


def _request_log_path() -> Path:
    return Path(__file__).resolve().parents[2] / "logs" / "api_requests.log"


@app.middleware("http")
async def log_requests(request, call_next):
    """Log every request and response to logs/api_requests.log and logger so we can see what path is hit."""
    method = request.scope.get("method", "?")
    path = request.scope.get("path", "?")
    log.info("REQUEST %s %s", method, path)

    def _write(line: str) -> None:
        try:
            _request_log_path().parent.mkdir(parents=True, exist_ok=True)
            with open(_request_log_path(), "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    _write(f"REQUEST {method} {path}\n")
    try:
        response = await call_next(request)
    except Exception as e:
        log.exception("Request failed: %s %s", method, path)
        _write(f"RESPONSE {method} {path} -> 500 (exception)\n")
        raise
    status = response.status_code
    log.info("RESPONSE %s %s -> %s", method, path, status)
    _write(f"RESPONSE {method} {path} -> {status}\n")
    return response


# Debug: list route paths so you can verify fetch-claim-records is registered (GET /api/debug/routes)
@app.get("/api/debug/routes")
def debug_routes() -> Dict[str, Any]:
    routes = []
    for r in app.routes:
        if hasattr(r, "path") and hasattr(r, "methods") and r.methods:
            routes.append({"methods": list(r.methods), "path": r.path})
    return {"routes": routes, "hint": "Look for POST /api/areas-of-focus/{area_id}/fetch-claim-records"}


# Explicit API routes *before* mount so they are always matched (avoid 404 / Method Not Allowed)
@app.post("/api/areas-of-focus")
def create_area_toplevel(body: CreateAreaBody) -> Dict[str, Any]:
    """Create or merge a single target. Requires name and (PLSS or lat+lon)."""
    return _create_area_from_body(body)


@app.get("/api/areas-of-focus/clean-preview")
def clean_preview_toplevel() -> Dict[str, Any]:
    """Targets with no PLSS and duplicate groups by PLSS for the Clean Targets modal."""
    try:
        from mining_os.services.areas_of_focus import get_clean_preview
        return get_clean_preview()
    except Exception as e:
        log.exception("clean-preview failed: %s", e)
        return {"no_plss": [], "duplicates": []}


@app.post("/api/areas-of-focus/fill-plss-ai")
def fill_plss_ai_toplevel(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Infer PLSS from mine name, state, county (from notes), and optional web snippets; update selected targets."""
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Request body must include ids: number[]")
    try:
        from mining_os.services.plss_ai_lookup import lookup_plss_for_target_ids

        return lookup_plss_for_target_ids([int(x) for x in ids])
    except HTTPException:
        raise
    except Exception as e:
        log.exception("fill-plss-ai failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/areas-of-focus/fill-plss-ai-preview")
def fill_plss_ai_preview_toplevel(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Preview PLSS proposals without DB writes (review step before fill-plss-ai-apply)."""
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="Request body must include ids: number[]")
    try:
        from mining_os.services.plss_ai_lookup import lookup_plss_for_target_ids

        return lookup_plss_for_target_ids([int(x) for x in ids], dry_run=True)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("fill-plss-ai-preview failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/areas-of-focus/fill-plss-ai-apply")
def fill_plss_ai_apply_toplevel(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Apply reviewed proposals from fill-plss-ai-preview."""
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Request body must include items: array")
    try:
        from mining_os.services.plss_ai_lookup import apply_plss_ai_proposals

        return apply_plss_ai_proposals(items)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("fill-plss-ai-apply failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/areas-of-focus/backfill-plss")
def backfill_plss_toplevel() -> Dict[str, Any]:
    """Parse location_plss into state, township, range, section for all targets missing them.
    Use this to fix existing targets that were imported before PLSS component columns existed."""
    try:
        from mining_os.services.areas_of_focus import backfill_plss_components
        updated = backfill_plss_components()
        return {"updated": updated, "message": f"Backfilled state, township, range, section for {updated} target(s)."}
    except Exception as e:
        log.exception("backfill-plss failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/areas-of-focus/plss-from-coordinates-batch")
def plss_from_coordinates_batch_toplevel() -> Dict[str, Any]:
    """Batch PLSS from coordinates for targets missing plss_normalized."""
    from mining_os.services.areas_of_focus import batch_reverse_plss_from_coordinates

    return batch_reverse_plss_from_coordinates()


@app.post("/api/areas-of-focus/batch/fetch-claim-records")
def batch_fetch_claim_records_toplevel(body: BatchAreaIdsBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.area_batch_actions import batch_fetch_claim_records

    return batch_fetch_claim_records(body.ids)


@app.post("/api/areas-of-focus/batch/lr2000-geographic-report")
def batch_lr2000_geographic_report_toplevel(body: BatchAreaIdsBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.area_batch_actions import batch_lr2000_geographic_report

    return batch_lr2000_geographic_report(body.ids)


@app.post("/api/areas-of-focus/import-csv-inspect")
async def import_csv_inspect_toplevel(file: UploadFile = File(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import inspect_csv_import

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    result = inspect_csv_import(content)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/areas-of-focus/import-csv")
async def import_csv_toplevel(
    file: UploadFile = File(...),
    bulk_priority: Optional[str] = Form(None),
    bulk_report_url: Optional[str] = Form(None),
    bulk_mineral: Optional[str] = Form(None),
    conflict_strategy: Optional[str] = Form(None),
    column_mapping: Optional[str] = Form(None),
    inspect_only: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """Import targets from CSV. Literal path so it is not matched by {area_id}."""
    import json

    log.info("import_csv_toplevel handler: processing CSV import (explicit route)")
    from mining_os.services.areas_of_focus import inspect_csv_import, preview_csv_import, apply_csv_import
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV file")
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    if inspect_only and str(inspect_only).strip().lower() in ("1", "true", "yes", "on"):
        result = inspect_csv_import(content)
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    mapping_obj: Optional[Dict[str, Any]] = None
    if column_mapping and column_mapping.strip():
        try:
            mapping_obj = json.loads(column_mapping)
            if not isinstance(mapping_obj, dict):
                raise ValueError("column_mapping must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid column_mapping JSON: {e}") from e
    preview = preview_csv_import(
        content,
        bulk_priority=bulk_priority,
        bulk_report_url=bulk_report_url,
        bulk_mineral=bulk_mineral,
        column_mapping=mapping_obj,
    )
    if preview.get("errors"):
        raise HTTPException(status_code=400, detail="; ".join(preview["errors"][:3]))
    valid = preview["valid_rows"]
    conflicts = preview["conflicts"]
    skip_reasons = preview.get("skip_reasons") or []
    source_row_count = int(preview.get("source_row_count") or 0)
    debug_first_row = preview.get("debug_first_row") or {}
    n_valid = len(valid)
    log.info("import_csv_toplevel: source_rows=%d valid=%d skipped=%d conflicts=%d strategy=%s",
             source_row_count, n_valid, preview["skipped"], len(conflicts), conflict_strategy)
    if conflict_strategy is None:
        if n_valid == 0:
            if source_row_count == 0:
                msg = "No data rows found after the header. Add rows to the CSV or re-export the file."
            else:
                msg = (
                    f"No rows could be imported — {preview['skipped']} of {source_row_count} data row(s) were skipped "
                    f"(missing name, unparseable PLSS, or location could not be normalized)."
                )
                if skip_reasons:
                    msg += " Examples: " + " | ".join(skip_reasons[:3])
        elif conflicts:
            msg = f"{n_valid} row(s) valid; {len(conflicts)} already exist (choose merge / use old / use new)."
        else:
            msg = f"{n_valid} row(s) ready to import."
        return {
            "preview": True,
            "valid_rows": n_valid,
            "skipped": preview["skipped"],
            "skip_reasons": skip_reasons,
            "source_row_count": source_row_count,
            "conflicts": conflicts,
            "message": msg,
            "debug_first_row": debug_first_row,
        }
    if conflict_strategy not in ("merge", "use_old", "use_new"):
        raise HTTPException(status_code=400, detail="conflict_strategy must be merge, use_old, or use_new")
    result = apply_csv_import(
        valid, conflict_strategy,
        bulk_priority=bulk_priority, bulk_report_url=bulk_report_url, bulk_mineral=bulk_mineral,
    )
    return {
        "preview": False,
        "applied": result["applied"],
        "merged": result["merged"],
        "skipped": result["skipped"],
        "errors": result["errors"],
        "applied_names": result.get("applied_names", []),
        "merged_names": result.get("merged_names", []),
    }


@app.post("/api/areas-of-focus/{area_id}/priority")
def set_area_priority_toplevel(area_id: int, body: AreaPriorityBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_priority
    ok = update_area_priority(area_id, body.priority)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid target status")
    return {"id": area_id, "priority": body.priority.lower()}


@app.post("/api/areas-of-focus/{area_id}/notes")
def set_area_notes_toplevel(area_id: int, body: AreaNotesBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_notes
    ok = update_area_notes(area_id, body.notes)
    if not ok:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"id": area_id, "notes": body.notes}


@app.post("/api/areas-of-focus/{area_id}/minerals")
def set_area_minerals_toplevel(area_id: int, body: AreaMineralsBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_minerals
    ok = update_area_minerals(area_id, body.minerals)
    if not ok:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"id": area_id, "minerals": body.minerals}


@app.post("/api/areas-of-focus/{area_id}/claim-type")
def set_area_claim_type_toplevel(area_id: int, body: AreaClaimTypeBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_claim_type
    ok = update_area_claim_type(area_id, body.claim_type)
    if not ok:
        raise HTTPException(status_code=404, detail="Target not found")
    return {"id": area_id, "claim_type": body.claim_type}


@app.post("/api/areas-of-focus/{area_id}/coordinates")
def set_area_coordinates_toplevel(area_id: int, body: AreaCoordinatesBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import get_area, update_area_coordinates

    if not get_area(area_id):
        raise HTTPException(status_code=404, detail="Target not found")
    if body.latitude is None or body.longitude is None:
        raise HTTPException(status_code=400, detail="latitude and longitude are required")
    if not _finite_coord(body.latitude) or not _finite_coord(body.longitude):
        raise HTTPException(status_code=400, detail="latitude and longitude must be finite numbers")
    update_area_coordinates(area_id, float(body.latitude), float(body.longitude))
    return {"id": area_id, "latitude": float(body.latitude), "longitude": float(body.longitude)}


@app.post("/api/areas-of-focus/{area_id}/plss-from-coordinates")
def plss_from_coordinates_toplevel(area_id: int) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import reverse_plss_from_coordinates_for_area

    return reverse_plss_from_coordinates_for_area(area_id)


@app.post("/api/areas-of-focus/{area_id}/name")
def set_area_name_toplevel(area_id: int, body: AreaNameBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import get_area, update_area_name
    if not (body.name or "").strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not get_area(area_id):
        raise HTTPException(status_code=404, detail="Target not found")
    ok = update_area_name(area_id, body.name)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to update name")
    return {"id": area_id, "name": body.name.strip()[:500]}


@app.post("/api/areas-of-focus/{area_id}/plss")
def set_area_plss_toplevel(area_id: int, body: AreaPlssBody = Body(...)) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_plss
    return update_area_plss(
        area_id,
        body.location_plss,
        regeocode_coordinates=body.regeocode_coordinates,
    )


@app.post("/api/areas-of-focus/{area_id}/plss-components")
def set_area_plss_components_toplevel(
    area_id: int, body: AreaPlssComponentsBody = Body(...)
) -> Dict[str, Any]:
    from mining_os.services.areas_of_focus import update_area_plss_components
    return update_area_plss_components(
        area_id,
        state_abbr=body.state_abbr,
        township=body.township,
        range_val=body.range_val,
        section=body.section,
        meridian=body.meridian,
        regeocode_coordinates=body.regeocode_coordinates,
    )


@app.post("/api/areas-of-focus/{area_id}/fetch-claim-records")
def fetch_claim_records_toplevel(area_id: int) -> Dict[str, Any]:
    """Run BLM claim search using stored PLSS fields + spatial fallback. Explicit route so request is not 404."""
    return _safe_fetch_claim_records(area_id)


@app.post("/api/areas-of-focus/{area_id}/fetch-claim-records/start")
def fetch_claim_records_start_toplevel(area_id: int) -> Dict[str, Any]:
    """Toplevel mirror of :func:`api_fetch_claim_records_start` for prod parity."""
    return api_fetch_claim_records_start(area_id)


@app.get("/api/jobs/{job_id}")
def get_job_toplevel(job_id: str) -> Dict[str, Any]:
    """Toplevel mirror of :func:`api_get_job`."""
    return api_get_job(job_id)


@app.post("/api/areas-of-focus/{area_id}/lr2000-geographic-report")
def lr2000_geographic_report_toplevel(area_id: int) -> Dict[str, Any]:
    return _safe_lr2000_report(area_id)


@app.post("/api/areas-of-focus/{area_id}/clear-claim-records")
def clear_claim_records_snapshot_toplevel(area_id: int) -> Dict[str, Any]:
    return _safe_clear_characteristic_keys(area_id, ["claim_records"])


@app.post("/api/areas-of-focus/{area_id}/clear-lr2000-report")
def clear_lr2000_snapshot_toplevel(area_id: int) -> Dict[str, Any]:
    return _safe_clear_characteristic_keys(area_id, ["lr2000_geographic_index"])


@app.get("/api/diag/check-payment")
def diag_check_payment_toplevel(case_url: str) -> Dict[str, Any]:
    return diag_check_payment(case_url)


@app.post("/api/areas-of-focus/{area_id}/generate-report")
def generate_report_toplevel(area_id: int) -> Dict[str, Any]:
    """Generate a Mineral Report for the target using OpenAI. Returns { ok, report, error }."""
    log.info("generate_report_toplevel CALLED area_id=%s", area_id)
    from mining_os.services.mineral_report import generate_report
    return generate_report(area_id, fetch_reports=True)


@app.get("/api/discovery/runs")
def discovery_runs_list(limit: int = Query(50, ge=1, le=200)) -> List[Dict[str, Any]]:
    try:
        from mining_os.services.discovery_runs import list_runs
        return list_runs(limit=limit)
    except Exception as e:
        log.warning("list_runs failed: %s", e)
        return []


@app.get("/api/discovery/runs/{run_id}")
def discovery_run_detail(run_id: int) -> Dict[str, Any]:
    try:
        from mining_os.services.discovery_runs import get_run
        run = get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Discovery run not found")
        return run
    except HTTPException:
        raise
    except Exception as e:
        log.warning("get_run failed: %s", e)
        raise HTTPException(status_code=404, detail="Discovery run not found")


@app.get("/api/minerals/{mineral_id}/report")
def mineral_report_toplevel(mineral_id: int) -> Dict[str, Any]:
    from mining_os.services.minerals import get_mineral
    from mining_os.services.mineral_report import get_mineral_report
    m = get_mineral(mineral_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mineral not found")
    return get_mineral_report(m["name"])

_uploads_dir = Path(__file__).resolve().parent.parent.parent / "uploads" / "pdf_reports"


@app.post("/api/process-mine-report")
async def process_mine_report_toplevel(
    file: UploadFile = File(...),
    mineral: Optional[str] = Form(None),
    state: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """Upload a mining PDF report, save to disk, extract targets using AI."""
    log.info("process_mine_report: file=%s mineral=%s state=%s", file.filename, mineral, state)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF file")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF too large (max 50 MB)")

    import uuid
    _uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-]", "_", file.filename or "report.pdf")
    stored_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    stored_path = _uploads_dir / stored_name
    stored_path.write_bytes(pdf_bytes)
    pdf_url = f"/api/uploads/pdf_reports/{stored_name}"
    log.info("Saved PDF to %s (%d bytes)", stored_path, len(pdf_bytes))

    from mining_os.services.pdf_report_processor import process_pdf_report
    result = process_pdf_report(
        pdf_bytes,
        mineral=mineral.strip() if mineral else None,
        state=state.strip() if state else None,
    )
    result["pdf_url"] = pdf_url
    result["pdf_filename"] = file.filename
    return result


class ImportReportTargetsBody(BaseModel):
    targets: List[Dict[str, Any]]
    pdf_url: Optional[str] = None
    pdf_filename: Optional[str] = None


@app.post("/api/import-report-targets")
def import_report_targets_toplevel(body: ImportReportTargetsBody) -> Dict[str, Any]:
    """Import user-selected targets from a processed PDF report."""
    from mining_os.services.areas_of_focus import upsert_area
    imported = 0
    errors = []
    report_links = [body.pdf_url] if body.pdf_url else []
    for t in body.targets:
        name = (t.get("name") or "").strip()
        plss = (t.get("plss") or "").strip()
        if not name:
            continue
        try:
            upsert_area(
                name=name,
                location_plss=plss or None,
                state_abbr=(t.get("state") or "").strip().upper()[:2] or None,
                township=(t.get("township") or "").strip() or None,
                range_val=(t.get("range") or "").strip() or None,
                section=(t.get("section") or "").strip() or None,
                latitude=t.get("latitude"),
                longitude=t.get("longitude"),
                minerals=t.get("minerals") if isinstance(t.get("minerals"), list) else None,
                status="unknown",
                source="pdf_report",
                report_links=report_links,
                is_uploaded=True,
            )
            imported += 1
        except Exception as e:
            errors.append(f"{name}: {e}")
    return {"imported": imported, "errors": errors}


@app.post("/api/batch-process-reports/parse")
async def batch_parse_csv(
    file: UploadFile = File(...),
    report_series: str = Form("OME"),
) -> Dict[str, Any]:
    """Parse a batch report CSV and return rows with metadata (no PDF downloading).

    ``report_series``: OME | DMEA | DMA — selects USGS DS-1004 scan URL pattern
    (DMA uses /dma/NNNN_DMA.pdf; OME and DMEA use /ome/{docket}_OME.pdf).
    """
    csv_bytes = await file.read()
    from mining_os.services.batch_report_processor import parse_batch_csv

    rs = (report_series or "OME").strip().upper()
    if rs not in ("OME", "DMEA", "DMA"):
        rs = "OME"
    fn = (file.filename or "").lower()
    if rs == "OME" and "dma" in fn and "dmea" not in fn:
        rs = "DMA"
    elif rs == "OME" and "dmea" in fn:
        rs = "DMEA"

    rows = parse_batch_csv(csv_bytes, report_series=rs)
    downloadable = sum(1 for r in rows if r["downloadable"])
    return {
        "ok": True,
        "total": len(rows),
        "downloadable": downloadable,
        "rows": rows,
    }


@app.post("/api/batch-process-reports/process")
async def batch_process_rows(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Process a subset of batch rows: download PDFs + AI extraction.

    Body: {"rows": [...], "skip_pdf": false}
    """
    rows = body.get("rows", [])
    skip_pdf = body.get("skip_pdf", False)

    from mining_os.services.batch_report_processor import process_parsed_batch_rows

    results, _ = process_parsed_batch_rows(rows, skip_pdf=skip_pdf)
    return {"ok": True, "rows": results}


@app.post("/api/batch-import-targets")
def batch_import_targets(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Import targets from batch processing results.

    Body: {"targets": [{"name", "state", "plss", "minerals", "county", "notes", "url", ...}]}

    Targets without a resolvable PLSS (``plss`` string or township + range) are **skipped**
    so the catalog stays location-grounded. Use PDF extraction + geo pass, or add PLSS manually.
    """
    from mining_os.services.areas_of_focus import upsert_area
    from mining_os.services.batch_target_location import effective_plss_string, has_required_plss

    targets = body.get("targets", [])
    imported = 0
    errors = []
    skipped: list[Dict[str, str]] = []
    for t in targets:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        if not has_required_plss(t):
            skipped.append(
                {
                    "name": name,
                    "reason": "missing PLSS — need plss text or both township and range (from the report or geo pass)",
                }
            )
            continue
        plss = effective_plss_string(t)
        state = (t.get("state") or t.get("state_abbr") or "").strip().upper()[:2]
        county = (t.get("county") or "").strip()
        minerals_list = t.get("minerals")
        if isinstance(minerals_list, str):
            minerals_list = [m.strip() for m in minerals_list.split(",") if m.strip()]
        notes = (t.get("notes") or "").strip()
        report_url = (t.get("url") or t.get("report_url") or "").strip()
        report_links = [report_url] if report_url else []

        if county and notes:
            notes = f"County: {county}. {notes}"
        elif county:
            notes = f"County: {county}"

        try:
            upsert_area(
                name=name,
                location_plss=plss,
                state_abbr=state or None,
                township=(t.get("township") or "").strip() or None,
                range_val=(t.get("range") or "").strip() or None,
                section=(t.get("section") or "").strip() or None,
                latitude=t.get("latitude"),
                longitude=t.get("longitude"),
                minerals=minerals_list if minerals_list else None,
                status="unknown",
                source="batch_report",
                report_links=report_links or None,
                validity_notes=notes or None,
                is_uploaded=True,
                skip_plss_geocode=True,
            )
            imported += 1
        except Exception as e:
            errors.append(f"{name}: {e}")
    return {
        "imported": imported,
        "errors": errors,
        "skipped": skipped,
        "note": "Targets must include PLSS (or township + range). Rows without location were skipped. "
        "Batch import skips live BLM geocoding per row; run batch PLSS geocode after import to fill lat/long when needed.",
    }


@api_app.post("/areas-of-focus/geocode")
def api_geocode_targets() -> Dict[str, Any]:
    """Batch geocode all targets missing lat/long using BLM Cadastral PLSS service."""
    from mining_os.services.plss_geocode import batch_geocode_targets
    updated = batch_geocode_targets([])
    return {"updated": updated}


@api_app.post("/areas-of-focus/clean-minerals")
def api_clean_minerals() -> Dict[str, Any]:
    """Normalize all mineral names in existing targets (title-case, strip numbers/parens)."""
    from mining_os.services.areas_of_focus import _normalize_minerals
    eng = get_engine()
    updated = 0
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT id, minerals FROM areas_of_focus WHERE minerals IS NOT NULL AND array_length(minerals, 1) > 0")
        ).mappings().fetchall()
        for row in rows:
            old = list(row["minerals"])
            cleaned = _normalize_minerals(old)
            if cleaned != old:
                conn.execute(
                    text("UPDATE areas_of_focus SET minerals = :minerals, updated_at = now() WHERE id = :id"),
                    {"minerals": cleaned, "id": row["id"]},
                )
                updated += 1
    return {"updated": updated, "total": len(rows)}


@api_app.post("/areas-of-focus/migrate-target-status")
def api_migrate_target_status() -> Dict[str, Any]:
    """Migrate legacy priority values (low/medium/high) to new target status values."""
    eng = get_engine()
    mapping = {"low": "monitoring_low", "medium": "monitoring_med", "high": "monitoring_high"}
    updated = 0
    with eng.begin() as conn:
        for old_val, new_val in mapping.items():
            r = conn.execute(
                text("UPDATE areas_of_focus SET priority = :new WHERE priority = :old"),
                {"old": old_val, "new": new_val},
            )
            updated += r.rowcount
    return {"migrated": updated}


_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/api/uploads/pdf_reports", StaticFiles(directory=str(_uploads_dir)), name="pdf_uploads")

app.mount("/api", api_app)


# ---- Start automation scheduler on uvicorn boot ---
@app.on_event("startup")
def _start_automation_scheduler() -> None:
    try:
        from mining_os.services.automation_scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        log.warning("Automation scheduler failed to start: %s", e)


_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
_index_html = _frontend_dist / "index.html"

if _index_html.exists():
    # Serve static assets (JS, CSS, etc.)
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="assets")
    # SPA fallback: serve index.html for root and all SPA paths (not /api)
    @app.get("/")
    def root():
        return FileResponse(_index_html)

    @app.get("/{full_path:path}")
    def spa_catchall(full_path: str):
        # Never serve SPA for API paths — let API routes handle them
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        asset = _frontend_dist / full_path
        if asset.is_file():
            return FileResponse(asset)
        return FileResponse(_index_html)
