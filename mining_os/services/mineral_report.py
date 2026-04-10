"""
Generate a Mineral Report for a target (area) using OpenAI.
Report is aimed at mining groups/businesses for potential sale or royalty deals.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from mining_os.config import settings
from mining_os.services.areas_of_focus import get_area

log = logging.getLogger("mining_os.mineral_report")

HINKINITE_BIO = """Hinkinite Resources: Bryson Hinkins is the founder of Hinkinite Resources, a Utah-based firm focused on prospecting and revitalizing precious, base-metal, and industrial mineral deposits across the western U.S. With a Mining Engineering degree from the University of Utah, Bryson spent over a decade in the aggregates industry, ultimately overseeing geology and engineering for more than 100 operations. In 2022, he shifted his focus to independent exploration and development projects. He is a Qualified Professional member of the Mining and Metallurgical Society of America (MMSA)."""

REPORT_SYSTEM = """You are an expert mining and mineral-resource analyst writing a professional Mineral Report. The report is for a mining group or business audience—potential buyers of claims or royalty partners. Write in clear sections with headers. Be factual and persuasive; cite the data provided and use your knowledge to add context on minerals, geography, and economics. Output the report as plain text (no markdown code blocks). Use clear section headings in ALL CAPS or with a line break before each major section."""

def _fetch_url_text(url: str, max_chars: int = 12000, timeout_sec: int = 12) -> str:
    """Fetch URL and return plain text, truncated. Returns empty string on failure."""
    try:
        import requests
        resp = requests.get(url, timeout=timeout_sec, headers={"User-Agent": "MiningOS/1.0"})
        resp.raise_for_status()
        text = (resp.text or "").strip()
        if not text:
            return ""
        # Strip HTML tags roughly
        text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", text, flags=re.I)
        text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] if len(text) > max_chars else text
    except Exception as e:
        log.debug("Could not fetch report URL %s: %s", url[:60], e)
        return ""


def generate_report(area_id: int, fetch_reports: bool = True) -> dict[str, Any]:
    """
    Generate a Mineral Report for the given target using OpenAI.
    Returns dict with keys: ok (bool), report (str | None), error (str | None).
    """
    area = get_area(area_id)
    if not area:
        return {"ok": False, "report": None, "error": "Target not found."}

    if not settings.OPENAI_API_KEY:
        return {"ok": False, "report": None, "error": "OpenAI API key is not configured. Set OPENAI_API_KEY in .env."}

    name = (area.get("name") or "Unknown").strip()
    location_plss = (area.get("location_plss") or "").strip()
    state = (area.get("state_abbr") or "").strip()
    township = (area.get("township") or "").strip()
    range_val = (area.get("range") or "").strip()
    section = (area.get("section") or "").strip()
    minerals = area.get("minerals") or []
    minerals_str = ", ".join(str(m) for m in minerals) if minerals else "Not specified"
    status = (area.get("status") or "unknown").strip()
    report_links = list(area.get("report_links") or [])
    validity_notes = (area.get("validity_notes") or "").strip()
    claim_records = (area.get("characteristics") or {}).get("claim_records")
    claim_summary = ""
    if claim_records and isinstance(claim_records, dict):
        claims = claim_records.get("claims") or []
        if isinstance(claims, list) and claims:
            types = set()
            for c in claims:
                if isinstance(c, dict) and c.get("BLM_PROD"):
                    types.add(str(c.get("BLM_PROD")).strip())
            claim_summary = f"BLM claim records on file: {len(claims)} claim(s). Types: {', '.join(sorted(types)) if types else 'N/A'}. Payment status from last fetch: see target status ({status})."

    # Optionally fetch text from report URLs
    report_snippets = ""
    if fetch_reports and report_links:
        for url in report_links[:5]:
            if not url or not str(url).startswith("http"):
                continue
            snippet = _fetch_url_text(str(url))
            if snippet:
                report_snippets += f"\n--- Content from {url} ---\n{snippet}\n"

    user_parts = [
        "Generate a Mineral Report for the following mining target. Use the data below and your knowledge to produce a comprehensive report.",
        "",
        "TARGET DATA:",
        f"  Name: {name}",
        f"  Location (PLSS): {location_plss or 'Not specified'}",
        f"  State: {state}",
        f"  Township: {township}",
        f"  Range: {range_val}",
        f"  Section: {section}",
        f"  Minerals: {minerals_str}",
        f"  BLM / payment status: {status}",
        f"  Validity / notes: {validity_notes or 'None'}",
        f"  Report URLs on file: {', '.join(report_links) if report_links else 'None'}",
        "",
    ]
    if claim_summary:
        user_parts.append(f"  {claim_summary}")
        user_parts.append("")
    user_parts.append("REQUIRED SECTIONS (write each with a clear heading):")
    user_parts.append("1. ABOUT HINKINITE RESOURCES — Use this exact description: " + HINKINITE_BIO)
    user_parts.append("2. CLAIM / PROPERTY OVERVIEW — Summarize what we know about this claim from the data above. If report content was fetched below, summarize and cite it. Note BLM status and any claim types.")
    user_parts.append("3. MINERAL SUMMARY — For each mineral listed: how it is mined, current use cases, why it is important, and market context.")
    user_parts.append("4. GEOGRAPHY & ACCESS — Describe the region (state, terrain), transportation options, roads, and approximate distances to major cities or highways where relevant.")
    user_parts.append("5. ECONOMICS — Brief discussion of the mineral economics, value proposition, and why this property may be of interest to a mining group or royalty partner.")
    user_parts.append("")
    user_parts.append("Write in a professional tone suitable for a potential buyer or partner. Output the full report as plain text with clear section headings.")
    if report_snippets:
        user_parts.append("\n--- EXTRACTED CONTENT FROM ATTACHED REPORTS (use to support the report) ---")
        user_parts.append(report_snippets[:50000])

    user_content = "\n".join(user_parts)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": REPORT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
            max_tokens=8192,
        )
        if resp.choices and resp.choices[0].message.content:
            report = (resp.choices[0].message.content or "").strip()
            return {"ok": True, "report": report, "error": None}
    except Exception as e:
        log.exception("OpenAI report generation failed: %s", e)
        return {"ok": False, "report": None, "error": str(e)}

    return {"ok": False, "report": None, "error": "OpenAI did not return report content."}
