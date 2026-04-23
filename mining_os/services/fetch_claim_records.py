"""
Run BLM_ClaimAgent get_mlrs_from_PLSS.py for a single target PLSS, capture terminal output
and generated JSON, and save to the target's characteristics.claim_records.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("mining_os.fetch_claim_records")

DEFAULT_STATE = "UT"

# Primary BLM Principal Meridian number per state.
# https://www.blm.gov/services/land-survey/principal-meridians
STATE_MERIDIAN = {
    "AL": "29",  # Huntsville
    "AK": "04",  # Copper River (most common; AK has several)
    "AZ": "12",  # Gila and Salt River
    "AR": "07",  # 5th Principal
    "CA": "21",  # Mount Diablo (primary; also 14 Humboldt, 27 San Bernardino)
    "CO": "28",  # 6th Principal (also 22 New Mexico, 31 Ute)
    "FL": "30",  # Tallahassee
    "ID": "01",  # Boise
    "IL": "09",  # 3rd Principal
    "IN": "09",  # 3rd Principal
    "IA": "07",  # 5th Principal
    "KS": "28",  # 6th Principal
    "LA": "17",  # Louisiana
    "MI": "20",  # Michigan
    "MN": "08",  # 4th Principal
    "MS": "29",  # Huntsville (also 06 Choctaw, 32 Washington)
    "MO": "07",  # 5th Principal
    "MT": "24",  # Principal (Montana)
    "NE": "28",  # 6th Principal
    "NV": "21",  # Mount Diablo
    "NM": "22",  # New Mexico
    "ND": "07",  # 5th Principal
    "OH": "10",  # 1st Principal (also others)
    "OK": "15",  # Indian
    "OR": "33",  # Willamette
    "SD": "07",  # 5th Principal (also 02 Black Hills)
    "UT": "26",  # Salt Lake
    "WA": "33",  # Willamette
    "WI": "08",  # 4th Principal
    "WY": "28",  # 6th Principal (also 34 Wind River)
}
DEFAULT_MERIDIAN = "26"


def _blm_agent_path() -> Path | None:
    env = Path(__file__).resolve().parents[2]
    sibling = env.parent / "BLM_ClaimAgent"
    if sibling.exists() and (sibling / "get_mlrs_from_PLSS.py").exists():
        return sibling
    custom = os.getenv("MINING_OS_BLM_AGENT_PATH")
    if custom and Path(custom).exists():
        return Path(custom)
    return None


def _parse_plss_for_script(location_plss: str | None) -> dict | None:
    """
    Parse location_plss into CSV row: Township, Range, Section, State, Meridian.
    Examples: "12S 14E 23", "UT 12S 14E Sec 23" -> Township=12S, Range=14E, Section=23, State=UT, Meridian=26.
    """
    if not location_plss or not location_plss.strip():
        return None
    from mining_os.services.blm_plss import parse_plss_string

    parsed = parse_plss_string(location_plss.strip(), default_state=DEFAULT_STATE)
    if not parsed or not parsed.get("township") or not parsed.get("range"):
        return None
    state = parsed.get("state") or DEFAULT_STATE
    meridian = STATE_MERIDIAN.get(state, DEFAULT_MERIDIAN)
    return {
        "Township": parsed["township"],
        "Range": parsed["range"],
        "Section": parsed.get("section") or "",
        "State": state,
        "Meridian": meridian,
    }


def _run_blm_script(agent_path: Path, plss_row: dict, area_name: str, out_name: str) -> tuple[subprocess.CompletedProcess, str, list]:
    """Write temp CSV, run BLM_ClaimAgent script, collect results. Returns (proc, log_text, claims)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ProjectName", "Township", "Range", "Section", "State", "Meridian"])
        w.writeheader()
        w.writerow({
            "ProjectName": (area_name or "Target")[:200],
            "Township": plss_row["Township"],
            "Range": plss_row["Range"],
            "Section": plss_row["Section"],
            "State": plss_row["State"],
            "Meridian": plss_row["Meridian"],
        })
        csv_path = f.name

    python_bin = "/usr/bin/python3"
    if not os.path.exists(python_bin):
        python_bin = "python3"

    env = {**os.environ}
    venv_bin = os.environ.get("VIRTUAL_ENV")
    if venv_bin:
        venv_bin_path = os.path.join(venv_bin, "bin")
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep) if p != venv_bin_path
        )
        env.pop("VIRTUAL_ENV", None)

    try:
        proc = subprocess.run(
            [python_bin, "get_mlrs_from_PLSS.py", csv_path, out_name],
            cwd=str(agent_path),
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
    finally:
        try:
            os.unlink(csv_path)
        except OSError:
            pass

    log_text = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")

    claims: list = []
    json_path = agent_path / "DataOutput" / f"{out_name}.json"
    csv_out = agent_path / "DataOutput" / f"{out_name}.csv"
    log_out = agent_path / "DataOutput" / f"{out_name}.log"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as jf:
                claims = json.load(jf)
            if not isinstance(claims, list):
                claims = [claims] if claims else []
        except Exception as e:
            log.warning("Could not read claim JSON %s: %s", json_path, e)

    for c in claims:
        c.pop("geometry", None)
        c.pop("Created_epoch_ms", None)
        c.pop("Modified_epoch_ms", None)
        c.pop("Shape__Length", None)
        c.pop("Shape__Area", None)

    for p in (json_path, csv_out, log_out):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    return proc, log_text, claims


def fetch_claim_records_for_area(
    area_id: int,
    area_name: str,
    location_plss: str | None,
    *,
    state_abbr: str | None = None,
    meridian: str | None = None,
    township: str | None = None,
    range_val: str | None = None,
    section: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    """
    Query BLM for mining claims.  Strategy:
      1. Run BLM_ClaimAgent script with section.
      2. If 0 claims and section was set, re-run without section (whole township/range).
      3. If still 0 and lat/lon available, try spatial query.
      4. If still 0, try built-in direct API query.
    """
    # Prefer already-parsed DB fields over re-parsing the PLSS string
    if state_abbr and township and range_val:
        plss_row = {
            "Township": township,
            "Range": range_val,
            "Section": section or "",
            "State": state_abbr,
            "Meridian": meridian or STATE_MERIDIAN.get(state_abbr, DEFAULT_MERIDIAN),
        }
        log.info("fetch_claim_records: using stored fields state=%s mer=%s twp=%s rng=%s sec=%s",
                 plss_row["State"], plss_row["Meridian"], plss_row["Township"], plss_row["Range"], plss_row["Section"])
    else:
        plss_row = _parse_plss_for_script(location_plss)

    if not plss_row:
        log.warning("fetch_claim_records: no valid PLSS parsed from %r for area %s (%s)", location_plss, area_id, area_name)
        if not location_plss or not location_plss.strip():
            detail = "This target has no PLSS location set (location_plss is empty). Edit the target and add a PLSS value like '12S 14E 23' or 'NV 21 0210N 0570E Sec 023'."
        else:
            detail = (
                f"Could not parse the PLSS value '{location_plss}' into Township/Range/Section. "
                f"Edit the target and fix Location (PLSS) to a format like '12S 14E 23', 'T12S R14E Sec 23', or 'NV 21 0210N 0570E Sec 023'."
            )
        return {"ok": False, "log": "", "claims": [], "error": detail, "fetched_at": None}

    agent_path = _blm_agent_path()
    out_name = f"area_{area_id}"
    had_section = bool(plss_row["Section"])

    # In production (Render/Railway), the BLM_ClaimAgent companion repo is not deployed.
    # We still want this endpoint to work — fall back to the built-in BLM ArcGIS API
    # (Pass 3 + Pass 4 below) which queries the same national MLRS layer.
    has_script = False
    proc = None  # subprocess.CompletedProcess | None
    log_text = ""
    claims: list = []
    query_method = "built_in_api_only"

    if agent_path:
        script_path = agent_path / "get_mlrs_from_PLSS.py"
        if script_path.exists():
            has_script = True
            query_method = "plss_script"
        else:
            log_text = f"BLM_ClaimAgent script not found at {script_path}; using built-in API fallback.\n"
            log.info("fetch_claim_records: script missing at %s — using built-in API fallback", script_path)
    else:
        log_text = (
            "BLM_ClaimAgent companion repo not present in this environment "
            "(set MINING_OS_BLM_AGENT_PATH or place repo at Agents/BLM_ClaimAgent for the script-based path); "
            "using built-in BLM ArcGIS API fallback.\n"
        )
        log.info("fetch_claim_records: BLM_ClaimAgent not found — using built-in API fallback")

    try:
        if has_script:
            # ── Pass 1: run with section ──
            log.info("fetch_claim_records [pass1]: twp=%s rng=%s sec=%r state=%s mer=%s",
                     plss_row["Township"], plss_row["Range"], plss_row["Section"],
                     plss_row["State"], plss_row["Meridian"])
            proc, script_log, claims = _run_blm_script(agent_path, plss_row, area_name, out_name)
            log_text += script_log

            # ── Pass 2: if 0 claims and had section, broaden to whole township/range ──
            if not claims and had_section:
                log.info("fetch_claim_records [pass2]: section-level returned 0 — broadening to full T/R")
                broad_row = {**plss_row, "Section": ""}
                proc2, log2, claims2 = _run_blm_script(agent_path, broad_row, area_name, out_name)
                if claims2:
                    claims = claims2
                    log_text += f"\n\n--- Broadened search (removed section {plss_row['Section']}, searched full T/R) ---\n" + log2
                    query_method = "plss_script_broadened"
                    log.info("fetch_claim_records: broadened search found %d claims", len(claims))

        # Track the first fatal environment error (e.g. missing `requests` module)
        # so we can surface it instead of the generic "no claims" message.
        fatal_env_error: str | None = None

        # ── Pass 3: spatial fallback via lat/lon ──
        if not claims and latitude is not None and longitude is not None:
            log.info("fetch_claim_records [spatial]: trying coords (%.5f, %.5f)", latitude, longitude)
            try:
                from mining_os.services.blm_plss import query_claims_by_coords
                spatial = query_claims_by_coords(latitude, longitude, radius_meters=2000)
                if spatial:
                    for c in spatial:
                        c.pop("geometry", None)
                    claims = spatial
                    query_method = "spatial"
                    log_text += f"\n[spatial] Found {len(claims)} claim(s) within 2 km of ({latitude}, {longitude})"
            except ModuleNotFoundError as e:
                fatal_env_error = (
                    f"Server dependency missing: {e.name!r}. "
                    "Re-deploy with all runtime dependencies installed "
                    "(see requirements.txt)."
                )
                log.error("fetch_claim_records: spatial import failed: %s", e)
            except Exception as e:
                log.warning("fetch_claim_records: spatial fallback failed: %s", e)
                log_text += f"\n[spatial] error: {e}"

        # ── Pass 4: built-in direct API (last resort) ──
        # Track whether the built-in API actually got a successful response from BLM
        # (vs. a network/service failure). "Successful query, 0 claims" is a valid
        # answer and should NOT be reported as an error.
        built_in_api_queried_ok: bool | None = None
        if not claims:
            log.info("fetch_claim_records [api]: trying built-in PLSS API query")
            try:
                from mining_os.services.blm_plss import query_claims_by_plss_with_status
                queried_ok, api_claims = query_claims_by_plss_with_status(
                    state=plss_row["State"],
                    township=plss_row["Township"],
                    range_val=plss_row["Range"],
                    section=None,
                    meridian=plss_row["Meridian"],
                )
                built_in_api_queried_ok = queried_ok
                if api_claims:
                    for c in api_claims:
                        c.pop("geometry", None)
                    claims = api_claims
                    query_method = "built_in_api"
                    log_text += f"\n[built-in API] Found {len(claims)} claim(s) via direct query (no section filter)"
                elif queried_ok:
                    log_text += "\n[built-in API] BLM responded successfully with 0 claims for this PLSS."
                else:
                    log_text += "\n[built-in API] BLM MLRS service did not respond successfully."
            except ModuleNotFoundError as e:
                built_in_api_queried_ok = False
                fatal_env_error = fatal_env_error or (
                    f"Server dependency missing: {e.name!r}. "
                    "Re-deploy with all runtime dependencies installed "
                    "(see requirements.txt)."
                )
                log.error("fetch_claim_records: built-in API import failed: %s", e)
            except Exception as e:
                built_in_api_queried_ok = False
                log.warning("fetch_claim_records: built-in API failed: %s", e)
                log_text += f"\n[built-in API] error: {e}"

        # ── Save results ──
        fetched_at = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "fetched_at": fetched_at,
            "log": log_text.strip(),
            "claims": claims,
            "plss": location_plss,
            "query_method": query_method,
            "ok": True,
        }
        if fatal_env_error and not claims:
            payload["error"] = fatal_env_error
            payload["ok"] = False
        elif proc is not None and proc.returncode != 0 and not claims:
            payload["error"] = f"Script exited with code {proc.returncode}"
            payload["ok"] = False
        elif not has_script and not claims and built_in_api_queried_ok is False:
            # Only treat this as an error when BLM itself was unreachable.
            # "Queried successfully, 0 claims" is a valid answer (no recorded MLRS claims).
            payload["error"] = (
                "BLM ArcGIS MLRS service is temporarily unreachable. Please try again in a moment."
            )
            payload["ok"] = False

        from mining_os.services.areas_of_focus import merge_area_characteristics, update_area_status
        merge_area_characteristics(area_id, {"claim_records": payload})

        from mining_os.services.areas_of_focus import update_area_state_meridian
        update_area_state_meridian(area_id, plss_row["State"], plss_row["Meridian"])

        if claims:
            statuses = {(c.get("payment_status") or "unknown").lower() for c in claims}
            if "unpaid" in statuses:
                derived_status = "unpaid"
            elif statuses == {"paid"}:
                derived_status = "paid"
            else:
                derived_status = "unknown"

            blm_prod_types = sorted({
                (c.get("BLM_PROD") or "").strip()
                for c in claims
                if (c.get("BLM_PROD") or "").strip()
            })

            update_area_status(area_id, status=derived_status)
            if blm_prod_types:
                merge_area_characteristics(area_id, {"blm_prod_types": blm_prod_types})

        return {
            "ok": payload["ok"],
            "log": payload["log"],
            "claims": payload["claims"],
            "error": payload.get("error"),
            "fetched_at": payload["fetched_at"],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "log": "", "claims": [], "error": "Script timed out (10 minutes).", "fetched_at": None}
    except Exception as e:
        log.exception("fetch_claim_records failed: %s", e)
        return {"ok": False, "log": "", "claims": [], "error": str(e), "fetched_at": None}
