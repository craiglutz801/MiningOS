"""Process mining PDF reports via AI to extract targets."""
from __future__ import annotations
import json
import logging
import re
from mining_os.config import settings
from mining_os.services.pdf_text_extract import extract_pdf_text

log = logging.getLogger("mining_os.pdf_report_processor")

BATCH_SIZE = 15


def _parse_json_response(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _clean_target(t: dict, fallback_state: str | None = None) -> dict | None:
    if not isinstance(t, dict):
        return None
    name = (t.get("name") or "").strip()
    if not name:
        return None
    plss = (t.get("plss") or "").strip()
    twp = (t.get("township") or "").strip()
    rng = (t.get("range") or "").strip()
    sec = (t.get("section") or "").strip()
    if not plss and twp and rng:
        plss = "T" + twp + " R" + rng + (" Sec" + sec if sec else "")
    lat = t.get("latitude")
    lon = t.get("longitude")
    try:
        lat = float(lat) if lat is not None else None
    except (ValueError, TypeError):
        lat = None
    try:
        lon = float(lon) if lon is not None else None
    except (ValueError, TypeError):
        lon = None
    mv = t.get("minerals") or []
    if isinstance(mv, str):
        mv = [m.strip() for m in mv.split(",") if m.strip()]
    st = (t.get("state") or fallback_state or "").strip().upper()[:2]
    return {
        "name": name, "state": st, "plss": plss,
        "township": twp, "range": rng, "section": sec,
        "latitude": lat, "longitude": lon, "minerals": mv,
        "county": (t.get("county") or "").strip(),
        "notes": (t.get("notes") or "").strip(),
    }


def _target_needs_location(t: dict) -> bool:
    has_plss = bool(t.get("plss"))
    has_coords = t.get("latitude") is not None and t.get("longitude") is not None
    return not has_plss and not has_coords


def _synthesize_plss_from_components(targets: list[dict]) -> None:
    """After geo pass, fill ``plss`` from township/range/section when the model split fields."""
    for t in targets:
        if (t.get("plss") or "").strip():
            continue
        twp = (t.get("township") or "").strip()
        rng = (t.get("range") or "").strip()
        sec = (t.get("section") or "").strip()
        if twp and rng:
            t["plss"] = "T" + twp + " R" + rng + (" Sec" + sec if sec else "")


def _geolocate_targets(client, targets: list[dict]) -> list[dict]:
    """Second AI pass: resolve PLSS and/or lat/long for targets missing location data."""
    needs_geo = [(i, t) for i, t in enumerate(targets) if _target_needs_location(t)]
    if not needs_geo:
        log.info("All %d targets already have location data, skipping geo-locate pass", len(targets))
        return targets

    log.info("%d of %d targets need geo-location, running lookup pass", len(needs_geo), len(targets))

    for batch_start in range(0, len(needs_geo), BATCH_SIZE):
        batch = needs_geo[batch_start:batch_start + BATCH_SIZE]
        entries = []
        for idx, (orig_idx, t) in enumerate(batch):
            entries.append({
                "idx": idx,
                "name": t["name"],
                "state": t["state"],
                "county": t["county"],
                "minerals": t["minerals"],
                "notes": t["notes"],
            })

        system = (
            "You are a US mining geography expert with deep knowledge of mine locations, "
            "mining districts, PLSS (Public Land Survey System), and coordinates. "
            "Given a list of mines/claims/prospects with name, state, county, minerals, and notes, "
            "determine the PLSS location and/or approximate latitude/longitude for each.\n\n"
            "Use your knowledge of known mines, mining districts, geological formations, and "
            "historical mining records. Mining district names, nearby landmarks, and county "
            "information can help triangulate locations.\n\n"
            "Return ONLY valid JSON: "
            '{"results": [{"idx": 0, "plss": "T30S R18W Sec10", "township": "30S", '
            '"range": "18W", "section": "10", "latitude": 36.123, "longitude": -115.456, '
            '"confidence": "high", "location_source": "known mine location"}]} \n\n'
            "Rules:\n"
            "- idx must match the input idx\n"
            "- Normalize PLSS to T##S/N R##E/W Sec## format\n"
            "- Provide lat/lon as decimal degrees (negative for West longitude)\n"
            "- confidence: 'high' (well-known mine, exact location known), "
            "'medium' (mining district known, approximate section), "
            "'low' (county-level or rough estimate)\n"
            "- location_source: brief explanation of how you determined the location "
            "(e.g. 'MRDS record', 'known mine in X district', 'county centroid')\n"
            "- Prefer returning plss (or township+range) over empty location fields; "
            "if only county-level or district-level is known, give best-effort plss or "
            "township+range with confidence 'low' and explain in location_source\n"
            "- If you truly cannot determine any location info, set all fields to null "
            "but still include the entry\n"
            "- Do NOT invent precise coordinates — if you only know the district or county, "
            "provide district/county-level coordinates and mark confidence as 'low'\n"
            "- No markdown fences, no explanation text"
        )
        user = "Determine PLSS and/or lat/long for these mining locations:\n\n" + json.dumps(entries, indent=2)

        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                max_tokens=4000,
            )
            raw = resp.choices[0].message.content or ""
            data = _parse_json_response(raw)
            results = data.get("results", [])
        except Exception:
            log.exception("Geo-locate batch failed (batch_start=%d)", batch_start)
            continue

        result_map = {r["idx"]: r for r in results if isinstance(r, dict) and "idx" in r}

        for idx, (orig_idx, t) in enumerate(batch):
            geo = result_map.get(idx)
            if not geo:
                continue

            new_plss = (geo.get("plss") or "").strip()
            new_twp = (geo.get("township") or "").strip()
            new_rng = (geo.get("range") or "").strip()
            new_sec = (geo.get("section") or "").strip()
            new_lat = geo.get("latitude")
            new_lon = geo.get("longitude")
            confidence = (geo.get("confidence") or "").strip()
            source = (geo.get("location_source") or "").strip()

            try:
                new_lat = float(new_lat) if new_lat is not None else None
            except (ValueError, TypeError):
                new_lat = None
            try:
                new_lon = float(new_lon) if new_lon is not None else None
            except (ValueError, TypeError):
                new_lon = None

            if new_plss:
                targets[orig_idx]["plss"] = new_plss
            if new_twp:
                targets[orig_idx]["township"] = new_twp
            if new_rng:
                targets[orig_idx]["range"] = new_rng
            if new_sec:
                targets[orig_idx]["section"] = new_sec
            if new_lat is not None and new_lon is not None:
                targets[orig_idx]["latitude"] = new_lat
                targets[orig_idx]["longitude"] = new_lon

            location_note = ""
            if confidence:
                location_note += f"[Location confidence: {confidence}]"
            if source:
                location_note += f" [{source}]"
            if location_note:
                existing_notes = targets[orig_idx].get("notes") or ""
                targets[orig_idx]["notes"] = (existing_notes + " " + location_note).strip()

    located = sum(1 for t in targets if not _target_needs_location(t))
    log.info("After geo-locate: %d of %d targets have location data", located, len(targets))
    return targets


def process_pdf_report(pdf_bytes, mineral=None, state=None):
    """Extract text from PDF (PyMuPDF / pypdf / optional OCR), send to OpenAI, return structured targets.

    Return dict keys used by batch processing:
    - ``extraction_reached_ai``: True if document text was passed to the model.
    - ``pdf_document_opened``: True if bytes were a readable PDF container.
    - ``had_extractable_text``: True if enough text for AI was extracted.
    - ``pdf_note``: when AI runs but returns zero targets, explains why (not an error).
    """
    if not settings.OPENAI_API_KEY:
        return {
            "ok": False,
            "targets": [],
            "error": "OPENAI_API_KEY not set in .env",
            "extraction_reached_ai": False,
            "pdf_document_opened": False,
            "had_extractable_text": False,
        }

    er = extract_pdf_text(
        pdf_bytes,
        ocr_max_pages=settings.BATCH_OCR_MAX_PAGES,
        ocr_page_timeout_sec=settings.BATCH_OCR_PAGE_TIMEOUT_SEC,
    )
    if not er.pdf_opened:
        msg = er.open_error or "Unknown error opening PDF."
        log.warning("PDF open failed: %s", msg)
        return {
            "ok": False,
            "targets": [],
            "error": f"PDF could not be opened or parsed as a document: {msg}",
            "extraction_reached_ai": False,
            "pdf_document_opened": False,
            "had_extractable_text": False,
        }

    if not er.had_meaningful_text:
        ocr_hint = (
            " Optional: install Tesseract on the server and pip install `pytesseract` + `Pillow` "
            "(extras: `pip install 'mining-os[pdf-ocr]'`) to OCR scanned pages."
            if not er.ocr_used
            else " OCR ran but still produced too little text — file may be low-quality scans."
        )
        return {
            "ok": False,
            "targets": [],
            "error": (
                "PDF opened, but almost no text could be extracted (typical for image-only scans). "
                "Mining OS could not send readable content to the AI."
                + ocr_hint
            ),
            "extraction_reached_ai": False,
            "pdf_document_opened": True,
            "had_extractable_text": False,
        }

    pdf_text = er.text
    text_length = len(pdf_text)
    log.info(
        "Extracted %d chars from PDF (methods=%s, ocr=%s)",
        text_length,
        ",".join(er.methods_tried),
        er.ocr_used,
    )

    mc = f"Focus on locations related to {mineral}. " if mineral else ""
    sc = ""
    if state:
        st2 = state.strip().upper()[:2]
        sc = f"All locations are in {state} ({st2}). If state not mentioned, use {st2}. "

    system = (
        "You are a mining geologist assistant. Read mining reports and extract every "
        "identifiable mining location, prospect, mine, claim, or deposit. "
        "For each, extract: name, state (2-letter), plss (e.g. T30S R18W Sec10), "
        "township, range, section, latitude, longitude, minerals (list), county, "
        "mining_district, notes. "
        "Return ONLY valid JSON with this structure: "
        '{"targets": [{"name": "..", "state": "XX", "plss": "..", '
        '"township": "..", "range": "..", "section": "..", '
        '"latitude": null, "longitude": null, "minerals": [".."], '
        '"county": "..", "mining_district": "..", "notes": ".."}]} '
        "Rules: Extract ALL locations. Normalize PLSS to T30S R18W Sec10 format. "
        "CRITICAL: Every target MUST include either a non-empty ``plss`` field OR "
        "both ``township`` and ``range`` (and ``section`` when known). "
        "Do not emit a target that has only a name with no PLSS/township/range — "
        "merge such cases into notes on the nearest located target, or omit. "
        "Include mining district or camp name if mentioned in context. "
        "Include any geographic references (nearby towns, mountain ranges, drainages) in notes. "
        "Set lat/lon to null if not explicitly stated in the document. "
        "Infer minerals from context. "
        "Do not invent locations. No markdown fences, no explanation text."
    )
    user = mc + sc + "Extract all mining targets from this report:\n\n" + pdf_text

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=8000,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        log.exception("OpenAI call failed")
        return {
            "ok": False,
            "targets": [],
            "text_length": text_length,
            "error": f"OpenAI error: {e}",
            "extraction_reached_ai": True,
            "pdf_document_opened": True,
            "had_extractable_text": True,
        }

    try:
        data = _parse_json_response(raw)
    except json.JSONDecodeError as e:
        log.error("OpenAI returned invalid JSON: %s", raw[:500])
        return {
            "ok": False,
            "targets": [],
            "text_length": text_length,
            "error": f"AI returned invalid JSON: {e}",
            "extraction_reached_ai": True,
            "pdf_document_opened": True,
            "had_extractable_text": True,
        }

    raw_targets = data.get("targets", [])
    if not isinstance(raw_targets, list):
        raw_targets = []

    cleaned = []
    for t in raw_targets:
        c = _clean_target(t, fallback_state=state)
        if c:
            district = (t.get("mining_district") or "").strip()
            if district:
                existing = c["notes"]
                c["notes"] = (f"District: {district}. {existing}" if existing else f"District: {district}").strip()
            cleaned.append(c)

    log.info("Pass 1: extracted %d targets from PDF report", len(cleaned))

    # Pass 2: geo-locate targets missing PLSS and coordinates
    if cleaned:
        try:
            cleaned = _geolocate_targets(client, cleaned)
        except Exception:
            log.exception("Geo-locate pass failed, returning targets without location enrichment")
        _synthesize_plss_from_components(cleaned)

    zero_note = None
    if not cleaned:
        zero_note = (
            f"PDF was read successfully ({text_length:,} characters extracted). "
            "The AI did not return any importable mining targets — the report may list only administrative "
            "or narrative content, or locations may not match the extraction schema."
        )

    return {
        "ok": True,
        "targets": cleaned,
        "text_length": text_length,
        "extraction_reached_ai": True,
        "pdf_document_opened": True,
        "had_extractable_text": True,
        "pdf_note": zero_note,
    }
