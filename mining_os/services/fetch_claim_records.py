"""
Fetch MLRS mining claims for a target PLSS and save under ``characteristics.claim_records``.

**Production path (fast, ~seconds):** BLM national MLRS ArcGIS FeatureServer only — same as Render.

**Optional slow path:** If ``BLM_ClaimAgent`` is installed next to this repo *and*
``MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1``, runs ``get_mlrs_from_PLSS.py`` end-to-end
(can take many minutes).

**Payment banner (Step 3):** After ArcGIS returns claims, ``mlrs_case_payment`` loads each
``case_page`` and detects the same red maintenance-fee text as ``BLM_ClaimAgent/get_mlrs_links.py``
(HTTP first; Selenium on typical dev machines, skipped on Render/Railway unless enabled — see
``.env.example``).
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
from typing import Any, Callable

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


def _progress(progress_cb: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if progress_cb:
        progress_cb(payload)


def _blm_agent_path() -> Path | None:
    env = Path(__file__).resolve().parents[2]
    sibling = env.parent / "BLM_ClaimAgent"
    if sibling.exists() and (sibling / "get_mlrs_from_PLSS.py").exists():
        return sibling
    custom = os.getenv("MINING_OS_BLM_AGENT_PATH")
    if custom and Path(custom).exists():
        return Path(custom)
    return None


def _use_blm_claim_agent_script() -> bool:
    """
    When True, run BLM_ClaimAgent's get_mlrs_from_PLSS.py (slow Selenium payment scrape).

    Default False so developers with ../BLM_ClaimAgent cloned get the same fast ArcGIS-only
    behavior as production. Set MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1 to enable.
    """
    v = (os.getenv("MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT") or "").strip().lower()
    return v in ("1", "true", "yes")


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

    claims = _normalize_claims(claims)

    for p in (json_path, csv_out, log_out):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    return proc, log_text, claims


# Heavy / redundant fields that are useful to BLM_ClaimAgent internally but bloat
# our DB payload and aren't shown in the UI.
_CLAIM_DROP_FIELDS = (
    "geometry",
    "Created_epoch_ms",
    "Modified_epoch_ms",
    "Shape__Length",
    "Shape__Area",
    "OBJECTID",
    "STAGE_ID",
    "LEG_CSE_NR",
    "SRC",
    "QLTY",
    "RCRD_ACRS",
    "ID",
    "project_name",
    "project_latitude",
    "project_longitude",
    "plss_state",
    "plss_meridian",
    "plss_township",
    "plss_range",
    "plss_section",
    "Location_PLSS",
)

# Heuristics for the .gov banner that the Selenium scraper sometimes mis-captures
# when the case page hasn't fully rendered. Treat these as "no owner".
_BAD_ACCOUNT_NAME_FRAGMENTS = (
    "official website",
    "here's how you know",
    "an official website of the united states",
)


def _clean_account_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    for frag in _BAD_ACCOUNT_NAME_FRAGMENTS:
        if frag in lower:
            return None
    return cleaned


def _normalize_claims(claims: list[dict]) -> list[dict]:
    """
    Normalize claim dicts produced by either the BLM_ClaimAgent script (CSE_NAME / CSE_NR)
    or the built-in BLM ArcGIS API path (claim_name / serial_number) into a single shape.

    Always sets:
      - ``claim_name``    (from claim_name or CSE_NAME)
      - ``serial_number`` (from serial_number or CSE_NR)
      - ``payment_status`` (defaults to 'unknown' when not enriched)

    Drops noise fields (geometry, OBJECTID, project_*) and strips the ".gov" banner
    text the Selenium owner-scrape sometimes mis-captures into ``account_name``.
    """
    if not isinstance(claims, list):
        return []
    normalized: list[dict] = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        for key in _CLAIM_DROP_FIELDS:
            c.pop(key, None)

        name = c.get("claim_name") or c.get("CSE_NAME")
        if name:
            c["claim_name"] = name
        serial = c.get("serial_number") or c.get("CSE_NR")
        if serial:
            c["serial_number"] = serial

        if "payment_status" not in c or not c.get("payment_status"):
            c["payment_status"] = "unknown"

        c["account_name"] = _clean_account_name(c.get("account_name"))

        normalized.append(c)
    return normalized


def _claim_identity_key(claim: dict[str, Any]) -> str | None:
    serial = str(claim.get("serial_number") or claim.get("CSE_NR") or "").strip().upper()
    if serial:
        return f"serial:{serial}"
    case_page = str(claim.get("case_page") or "").strip()
    if case_page:
        return f"case:{case_page}"
    name = str(claim.get("claim_name") or claim.get("CSE_NAME") or "").strip().upper()
    plss = str(claim.get("plss") or claim.get("CSE_META") or "").strip().upper()
    if name:
        return f"name:{name}|plss:{plss}"
    return None


def _merge_claim_lists(*claim_groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: dict[str, dict[str, Any]] = {}

    for group in claim_groups:
        for claim in _normalize_claims(group):
            if not isinstance(claim, dict):
                continue
            key = _claim_identity_key(claim)
            if not key:
                merged.append(claim)
                continue
            existing = seen.get(key)
            if existing is None:
                seen[key] = claim
                merged.append(claim)
                continue
            for field, value in claim.items():
                if existing.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                    existing[field] = value
    return merged


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
    previous_claim_records: dict[str, Any] | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    account_id: int | None = None,
) -> dict[str, Any]:
    """
    Query BLM for mining claims.  Strategy:
      1. Run BLM_ClaimAgent script with section.
      2. If 0 claims and section was set, re-run without section (whole township/range).
      3. If still 0, try built-in direct PLSS API query.
      4. If lat/lon is available, augment with nearby spatial claims so the result
         reflects the broader mine area rather than only the saved section.
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

    _progress(
        progress_cb,
        phase="plss_ready",
        message="PLSS resolved. Looking up claim records…",
    )

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
        if script_path.exists() and _use_blm_claim_agent_script():
            has_script = True
            query_method = "plss_script"
        elif script_path.exists() and not _use_blm_claim_agent_script():
            log_text = (
                "BLM_ClaimAgent is installed but skipped (same as production). "
                "Using built-in BLM ArcGIS API only (~seconds). "
                "For slow Selenium payment scrape per claim, set "
                "MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1 and restart the API.\n"
            )
            log.info(
                "fetch_claim_records: BLM_ClaimAgent present but disabled — "
                "set MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1 to use script path"
            )
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
            _progress(progress_cb, phase="script_query", message="Running BLM_ClaimAgent script…")
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

        # ── Pass 3: built-in direct API ──
        # Track whether the built-in API actually got a successful response from BLM
        # (vs. a network/service failure). "Successful query, 0 claims" is a valid
        # answer and should NOT be reported as an error.
        built_in_api_queried_ok: bool | None = None
        if not claims:
            _progress(progress_cb, phase="api_query", message="Querying the BLM MLRS API…")
            log.info("fetch_claim_records [api]: trying built-in PLSS API query")
            try:
                from mining_os.services.blm_plss import query_claims_by_plss_with_status

                section_filter = plss_row["Section"] or None
                queried_ok, api_claims = query_claims_by_plss_with_status(
                    state=plss_row["State"],
                    township=plss_row["Township"],
                    range_val=plss_row["Range"],
                    section=section_filter,
                    meridian=plss_row["Meridian"],
                )
                built_in_api_queried_ok = queried_ok
                if api_claims:
                    claims = _normalize_claims(api_claims)
                    query_method = "built_in_api"
                    if section_filter:
                        log_text += (
                            f"\n[built-in API] Found {len(claims)} claim(s) via direct query "
                            f"for section {section_filter}."
                        )
                    else:
                        log_text += (
                            f"\n[built-in API] Found {len(claims)} claim(s) via direct query "
                            "(no section filter)."
                        )
                elif queried_ok:
                    if section_filter:
                        log.info(
                            "fetch_claim_records [api]: section-level returned 0 — broadening to full T/R"
                        )
                        queried_ok, api_claims = query_claims_by_plss_with_status(
                            state=plss_row["State"],
                            township=plss_row["Township"],
                            range_val=plss_row["Range"],
                            section=None,
                            meridian=plss_row["Meridian"],
                        )
                        built_in_api_queried_ok = queried_ok
                        if api_claims:
                            claims = _normalize_claims(api_claims)
                            query_method = "built_in_api_broadened"
                            log_text += (
                                f"\n[built-in API] Section {section_filter} returned 0 claims; "
                                f"broadened to township/range and found {len(claims)} claim(s)."
                            )
                        elif queried_ok:
                            log_text += (
                                f"\n[built-in API] Section {section_filter} returned 0 claims. "
                                "Township/range broadening also returned 0 claims."
                            )
                        else:
                            log_text += (
                                f"\n[built-in API] Section {section_filter} returned 0 claims, "
                                "then the broadened township/range query did not respond successfully."
                            )
                    else:
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

        # ── Pass 4: spatial augmentation / fallback via lat/lon ──
        if latitude is not None and longitude is not None:
            progress_message = (
                "Augmenting with nearby-coordinate claim search…"
                if claims
                else "Trying nearby-coordinate claim search…"
            )
            _progress(progress_cb, phase="spatial_query", message=progress_message)
            log.info("fetch_claim_records [spatial]: trying coords (%.5f, %.5f)", latitude, longitude)
            try:
                from mining_os.services.blm_plss import query_claims_by_coords
                spatial = query_claims_by_coords(latitude, longitude, radius_meters=2000)
                if spatial:
                    spatial_claims = _normalize_claims(spatial)
                    if claims:
                        merged_claims = _merge_claim_lists(claims, spatial_claims)
                        added = len(merged_claims) - len(claims)
                        claims = merged_claims
                        if added > 0:
                            query_method = f"{query_method}_plus_spatial"
                            log_text += (
                                f"\n[spatial] Added {added} nearby claim(s) within 2 km of "
                                f"({latitude}, {longitude}); total claims now {len(claims)}."
                            )
                        else:
                            log_text += (
                                f"\n[spatial] Nearby-coordinate query found only claims already present "
                                f"in the PLSS result set ({len(claims)} total)."
                            )
                    else:
                        claims = spatial_claims
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

        # ── Step 3 (same as BLM_ClaimAgent get_mlrs_from_PLSS): MLRS case-page banner ──
        # ArcGIS does not include payment text; scrape case_page for the maintenance-fee message.
        if claims:
            from mining_os.services.mlrs_case_payment import (
                enrich_claims_from_mlrs_case_pages,
                prime_payment_cache,
            )

            prior_claims = []
            prior_fetched_at = None
            if isinstance(previous_claim_records, dict):
                prior_claims = previous_claim_records.get("claims") or []
                prior_fetched_at = previous_claim_records.get("fetched_at")
            seeded = prime_payment_cache(prior_claims, fetched_at=prior_fetched_at)
            if seeded:
                _progress(
                    progress_cb,
                    phase="payment_cache_seed",
                    message=f"Loaded {seeded} cached payment result(s) from the previous fetch.",
                )

            before = sum(
                1
                for c in claims
                if isinstance(c, dict) and (c.get("payment_status") or "").strip().lower() == "unpaid"
            )
            _progress(
                progress_cb,
                phase="payment_begin",
                current=0,
                total=len(claims),
                message=f"Checking payment status for {len(claims)} claim(s)…",
            )
            if progress_cb:
                claims = enrich_claims_from_mlrs_case_pages(claims, progress_cb=progress_cb)
            else:
                claims = enrich_claims_from_mlrs_case_pages(claims)
            after = sum(
                1
                for c in claims
                if isinstance(c, dict) and (c.get("payment_status") or "").strip().lower() == "unpaid"
            )
            log_text += (
                f"\n[case-page payment] Enriched {len(claims)} claim(s); "
                f"unpaid count {before} → {after}."
            )
            _progress(
                progress_cb,
                phase="payment_done",
                current=len(claims),
                total=len(claims),
                message=f"Finished checking {len(claims)} claim(s).",
            )

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
        merge_area_characteristics(area_id, {"claim_records": payload}, account_id=account_id)

        from mining_os.services.areas_of_focus import update_area_state_meridian
        update_area_state_meridian(
            area_id,
            plss_row["State"],
            plss_row["Meridian"],
            account_id=account_id,
        )

        if claims:
            statuses = {(c.get("payment_status") or "unknown").lower() for c in claims}
            if "unpaid" in statuses:
                derived_status = "unpaid"
            elif "paid" in statuses:
                derived_status = "paid"
            else:
                derived_status = "unknown"

            blm_prod_types = sorted({
                (c.get("BLM_PROD") or "").strip()
                for c in claims
                if (c.get("BLM_PROD") or "").strip()
            })

            update_area_status(area_id, status=derived_status, account_id=account_id)
            if blm_prod_types:
                merge_area_characteristics(area_id, {"blm_prod_types": blm_prod_types}, account_id=account_id)

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


def run_fetch_claim_records_for_area_id(
    area_id: int,
    *,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    account_id: int | None = None,
) -> dict[str, Any]:
    """
    Canonical target-level Fetch Claim Records runner used by both the
    target-detail action and automation/batch workflows.

    Always returns a structured payload with ``ok``, ``claims``, ``error``,
    ``log``, and ``fetched_at`` keys.
    """
    log.info("run_fetch_claim_records_for_area_id CALLED area_id=%s", area_id)
    try:
        from mining_os.services.areas_of_focus import get_area

        try:
            area = get_area(area_id, account_id=account_id) if account_id is not None else get_area(area_id)
        except TypeError as exc:
            if account_id is not None and "account_id" in str(exc):
                area = get_area(area_id)
            else:
                raise

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

        fetch_kwargs = {
            "state_abbr": area.get("state_abbr"),
            "meridian": area.get("meridian"),
            "township": area.get("township"),
            "range_val": area.get("range"),
            "section": area.get("section"),
            "latitude": area.get("latitude"),
            "longitude": area.get("longitude"),
            "previous_claim_records": (area.get("characteristics") or {}).get("claim_records"),
            "progress_cb": progress_cb,
        }
        if account_id is not None:
            fetch_kwargs["account_id"] = account_id

        return fetch_claim_records_for_area(
            area_id,
            area.get("name") or "",
            area.get("location_plss"),
            **fetch_kwargs,
        )
    except Exception as e:
        log.exception("run_fetch_claim_records_for_area_id failed for area_id=%s: %s", area_id, e)
        return {
            "ok": False,
            "log": "",
            "claims": [],
            "error": f"Fetch Claim Records failed: {e}",
            "fetched_at": None,
        }
