"""
Discovery agent: find areas of focus via OpenAI, web search, and BLM.

- Loads editable prompts from discovery_prompts (per mineral or default).
- Calls OpenAI to get structured locations (name, state, PLSS, lat/lon, report_urls).
- Optionally augments with web search (DuckDuckGo) for report links.
- Queries BLM by coords or PLSS for claim status; upserts areas with source='discovery_agent'.
- User chooses replace (clear discovery-sourced areas first) or add/supplement.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, List

from mining_os.config import settings
from mining_os.services.areas_of_focus import delete_areas_by_source, upsert_area, update_area_state_meridian
from mining_os.services.discovery_prompts import get_prompt_for_mineral
from mining_os.services.minerals import list_minerals

log = logging.getLogger("mining_os.discovery_agent")

DISCOVERY_SOURCE = "discovery_agent"
# Rate limits: delay (seconds) between external calls to avoid throttling
OPENAI_DELAY_SEC = 2.0
DDG_DELAY_SEC = 1.5
BLM_DELAY_SEC = 1.0


def _web_search(query: str, max_results: int = 5) -> List[str]:
    """Return list of URLs from a web search (DuckDuckGo). Requires: pip install duckduckgo-search."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        urls = []
        for r in results:
            u = (r.get("href") or r.get("url") or "").strip()
            if u and u.startswith("http"):
                urls.append(u)
        time.sleep(DDG_DELAY_SEC)
        return urls[:max_results]
    except ImportError:
        log.debug("duckduckgo_search not installed; skip web search")
        return []
    except Exception as e:
        log.warning("Web search failed for %s: %s", query[:50], e)
        return []


def _blm_by_coords(lat: float, lon: float) -> List[dict]:
    """Get BLM claims at (lat, lon). Returns list with status info if available."""
    try:
        from mining_os.services.blm_check import check_by_coords, check_payment_status
        claims = check_by_coords(lat, lon)
        for c in claims:
            sn = c.get("serial_number")
            if sn:
                pay = check_payment_status(sn)
                c["payment_status"] = pay.get("payment_status", "unknown")
        return claims
    except Exception as e:
        log.warning("BLM coords check failed: %s", e)
        return []


def _blm_by_plss(state: str, plss_str: str) -> List[dict]:
    """Parse PLSS string and query BLM; return claims with payment status."""
    try:
        from mining_os.services.blm_plss import parse_plss_string, query_claims_by_plss
        from mining_os.services.blm_check import check_payment_status
        parsed = parse_plss_string(plss_str, default_state=state)
        if not parsed:
            return []
        claims = query_claims_by_plss(
            state=parsed["state"],
            township=parsed["township"],
            range_val=parsed["range"],
            section=parsed.get("section"),
        )
        for c in claims:
            pay = check_payment_status(c.get("serial_number", ""))
            c["payment_status"] = pay.get("payment_status", "unknown")
        return claims
    except Exception as e:
        log.warning("BLM PLSS check failed: %s", e)
        return []


def _call_openai(system: str, user: str) -> str | None:
    """Call OpenAI chat, return assistant message content or None. Requires: pip install openai, OPENAI_API_KEY in .env."""
    if not settings.OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY not set; skipping OpenAI call")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai package not installed; pip install openai")
        return None
    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        if resp.choices:
            content = resp.choices[0].message.content
            if content and isinstance(content, str):
                return content
            return None
    except json.JSONDecodeError as e:
        log.warning("OpenAI response was not valid JSON: %s", e)
        return None
    except Exception as e:
        log.exception("OpenAI call failed: %s", e)
    return None


def _parse_locations_from_response(content: str) -> List[dict]:
    """Extract locations array from JSON. Tolerate markdown code blocks."""
    if not content or not content.strip():
        return []
    text = content.strip()
    # Strip markdown code block if present
    if "```json" in text:
        text = re.sub(r"^```json\s*", "", text).strip()
    if "```" in text:
        text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        data = json.loads(text)
        locs = data.get("locations")
        if isinstance(locs, list):
            return locs
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        log.warning("Failed to parse OpenAI JSON: %s", e)
    return []


def run_discovery(
    replace: bool = False,
    limit_per_mineral: int = 25,
    log_lines: list | None = None,
) -> dict:
    """
    Run the discovery agent: OpenAI + optional web search + BLM, then upsert areas.

    If replace=True, delete all areas with source='discovery_agent' first, then add new ones.
    If replace=False, only add/supplement (do not delete existing discovery areas).
    If log_lines is provided, append step messages for the UI.
    """
    def step(msg: str) -> None:
        if log_lines is not None:
            log_lines.append(msg)
        log.info("%s", msg)

    minerals = list_minerals()
    if not minerals:
        return {"status": "no_minerals", "message": "Add minerals of interest first.", "areas_added": 0, "log": log_lines or []}

    step("Starting discovery agent…")
    step(f"Target states: {', '.join(settings.TARGET_STATES) if settings.TARGET_STATES else 'UT, ID, NV, MT, WY'}")

    if replace:
        deleted = delete_areas_by_source(DISCOVERY_SOURCE)
        step(f"Replaced discovery list: removed {deleted} existing area(s).")
        log.info("Replaced discovery areas: deleted %s", deleted)

    states_str = ", ".join(settings.TARGET_STATES) if settings.TARGET_STATES else "UT, ID, NV, MT, WY"
    areas_added = 0
    errors = []
    locations_from_ai: List[dict] = []
    urls_from_web_search: List[str] = []

    for mineral in minerals:
        name = mineral.get("name", "")
        if not name:
            continue
        prompt_row = get_prompt_for_mineral(name)
        if not prompt_row:
            log.warning("No discovery prompt for mineral %s; skipping", name)
            step(f"Skip {name}: no prompt.")
            continue
        system = prompt_row.get("system_instruction", "")
        user_tpl = prompt_row.get("user_prompt_template", "")
        user = user_tpl.replace("{{mineral}}", name).replace("{{states}}", states_str)

        step(f"Querying OpenAI for {name}…")
        content = _call_openai(system, user)
        time.sleep(OPENAI_DELAY_SEC)
        if not content:
            errors.append(f"{name}: OpenAI returned no response")
            step(f"  ✗ No response for {name}.")
            continue
        locations = _parse_locations_from_response(content)
        if not locations:
            step(f"  No locations parsed for {name}.")
            log.info("No locations parsed for %s", name)
            continue
        step(f"  Got {len(locations)} location(s) for {name}.")

        for loc in locations[:limit_per_mineral]:
            try:
                loc_name = (loc.get("name") or "").strip() or "Unknown"
                state = (loc.get("state") or "").strip().upper()[:2]
                if not state or state not in [s.upper() for s in settings.TARGET_STATES]:
                    continue

                ai_meridian = (loc.get("meridian") or "").strip() or None
                ai_township = (loc.get("township") or "").strip() or None
                ai_range = (loc.get("range") or "").strip() or None
                ai_section = (loc.get("section") or "").strip() if loc.get("section") else None

                plss = (loc.get("plss") or "").strip() or None
                if not plss and ai_township and ai_range:
                    plss = f"{state} {ai_township} {ai_range}"
                    if ai_section:
                        plss += f" Sec {ai_section}"

                lat = loc.get("latitude") if isinstance(loc.get("latitude"), (int, float)) else None
                lon = loc.get("longitude") if isinstance(loc.get("longitude"), (int, float)) else None
                report_urls = loc.get("report_urls")
                if isinstance(report_urls, list):
                    report_urls = [str(u).strip() for u in report_urls if u and str(u).startswith("http")]
                else:
                    report_urls = []
                notes = (loc.get("notes") or "").strip() or None
                claim_type = (loc.get("claim_type") or "").strip().lower() or None
                owner = (loc.get("owner_or_source") or "").strip() or None

                locations_from_ai.append({
                    "name": loc_name,
                    "state": state,
                    "meridian": ai_meridian or "",
                    "township": ai_township or "",
                    "range": ai_range or "",
                    "section": ai_section or "",
                    "plss": plss or "",
                    "mineral": name,
                    "notes": notes or "",
                })

                # Optional: web search for more reports
                if len(report_urls) < 3:
                    q = f"{name} mine {state} report USGS OR site:ngmdb.usgs.gov OR site:mrdata.usgs.gov"
                    extra = _web_search(q, max_results=3)
                    for u in extra:
                        if u not in report_urls:
                            report_urls.append(u)
                        if u not in urls_from_web_search:
                            urls_from_web_search.append(u)

                # BLM: by coords first, then by PLSS
                status = "unknown"
                blm_case_url = None
                blm_serial = None
                if lat is not None and lon is not None:
                    claims = _blm_by_coords(lat, lon)
                    time.sleep(BLM_DELAY_SEC)
                    if claims:
                        c = claims[0]
                        status = c.get("payment_status", "unknown")
                        blm_case_url = c.get("case_page")
                        blm_serial = c.get("serial_number")
                elif plss and state:
                    claims = _blm_by_plss(state, plss)
                    time.sleep(BLM_DELAY_SEC)
                    if claims:
                        c = claims[0]
                        status = c.get("payment_status", "unknown")
                        blm_case_url = c.get("case_page")
                        blm_serial = c.get("serial_number")
                        if (lat is None or lon is None) and c.get("geometry"):
                            geom = c.get("geometry")
                            if isinstance(geom, dict):
                                rings = geom.get("rings") or geom.get("coordinates")
                                if rings:
                                    ring = rings[0] if isinstance(rings[0], list) else rings
                                    if ring and isinstance(ring[0], (list, tuple)) and len(ring[0]) >= 2:
                                        lons = [float(p[0]) for p in ring if len(p) >= 2]
                                        lats = [float(p[1]) for p in ring if len(p) >= 2]
                                        if lons and lats:
                                            lon = sum(lons) / len(lons)
                                            lat = sum(lats) / len(lats)
                                            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                                                lat = lon = None

                validity = notes
                if owner:
                    validity = f"{notes or ''}\nOwner/source: {owner}".strip()

                roi = 30
                if status == "unpaid":
                    roi += 25
                if report_urls:
                    roi += 20
                if blm_case_url:
                    roi += 10

                area_id = upsert_area(
                    name=loc_name,
                    location_plss=plss,
                    latitude=lat,
                    longitude=lon,
                    minerals=[name],
                    status=status,
                    report_links=report_urls if report_urls else None,
                    validity_notes=validity,
                    source=DISCOVERY_SOURCE,
                    blm_case_url=blm_case_url,
                    blm_serial_number=blm_serial,
                    roi_score=min(100, roi),
                )
                if area_id and (state or ai_meridian):
                    from mining_os.services.fetch_claim_records import STATE_MERIDIAN
                    meridian_to_save = ai_meridian or STATE_MERIDIAN.get(state, "")
                    if state and meridian_to_save:
                        update_area_state_meridian(area_id, state, meridian_to_save)
                areas_added += 1
                if log_lines is not None and areas_added <= 20:
                    step(f"    + {loc_name} ({state})")
            except Exception as e:
                log.exception("Failed to upsert location %s: %s", loc.get("name"), e)
                errors.append(f"{loc.get('name', '?')}: {e}")
                if log_lines is not None:
                    step(f"    ✗ {loc.get('name', '?')}: {e}")

    step(f"Done. Areas added: {areas_added}.")
    if errors:
        step(f"Errors: {len(errors)}.")

    return {
        "status": "ok",
        "minerals_checked": [m["name"] for m in minerals],
        "areas_added": areas_added,
        "replace": replace,
        "errors": errors[:20],
        "log": log_lines or [],
        "locations_from_ai": locations_from_ai,
        "urls_from_web_search": urls_from_web_search,
    }


def compute_roi_score(area: dict) -> int:
    """
    Simple ROI heuristic 0–100: priority mineral + unpaid + has report links = higher.
    """
    from mining_os.services.minerals import list_minerals
    priority = {m["name"].lower() for m in list_minerals()}
    minerals = area.get("minerals") or []
    mineral_match = priority.intersection({str(m).lower() for m in minerals})
    status = (area.get("status") or "").lower()
    reports = area.get("report_links") or []
    score = 20
    if mineral_match:
        score += 25
    if status == "unpaid":
        score += 25
    if reports:
        score += 20
    if area.get("validity_notes"):
        score += 10
    return min(100, score)
