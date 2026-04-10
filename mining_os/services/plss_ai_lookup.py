"""Use OpenAI (+ optional web snippets, regex, optional Gemini) to infer PLSS for targets missing location."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from mining_os.config import settings
from mining_os.services.areas_of_focus import (
    apply_plss_lookup_result,
    county_from_validity_notes,
    get_area,
    plss_lookup_would_conflict,
)
from mining_os.services.plss_text_extract import extract_plss_from_prose

log = logging.getLogger("mining_os.plss_ai_lookup")

BATCH = 8
# Preview / apply guardrails (UI review flow)
MAX_PREVIEW_IDS = 40
MAX_APPLY_ITEMS = 40
# Pause between DuckDuckGo lookups per target to reduce burst rate
WEB_SNIPPET_DELAY_SEC = 0.45


def _coerce_id(val: Any) -> int | None:
    try:
        if val is None:
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _web_snippets(name: str, state: str, county: str) -> str:
    """Pull longer snippets; PLSS is often buried mid-paragraph in MRDS / forum / overview text."""
    queries = [
        f"{name} mine {county} {state} PLSS township range section".strip(),
        f'"{name}" {state} MRDS township range section',
        f"{name} {state} mining claim T R Sec",
    ]
    seen: set[str] = set()
    chunks: list[str] = []
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            for q in queries:
                if not q:
                    continue
                try:
                    results = list(ddgs.text(q, max_results=6))
                except Exception:
                    continue
                for r in results:
                    title = (r.get("title") or "").strip()
                    body = (r.get("body") or "").strip()
                    if not body and not title:
                        continue
                    block = f"{title}\n{body}" if title else body
                    key = block[:120]
                    if key in seen:
                        continue
                    seen.add(key)
                    chunks.append(block[:2400])
                if len(chunks) >= 10:
                    break
        return "\n---\n".join(chunks[:10]) if chunks else ""
    except Exception as e:
        log.debug("Web search skipped: %s", e)
        return ""


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def _gemini_extract_plss(
    combined: str,
    *,
    name: str,
    state: str,
    county: str,
) -> dict[str, Any] | None:
    if not settings.GEMINI_API_KEY:
        return None
    key = settings.GEMINI_API_KEY.strip()
    if not key:
        return None
    prompt = (
        "You extract US Public Land Survey System (PLSS) for western mining sites.\n"
        f"Mine / prospect name: {name}\n"
        f"State: {state}  County: {county}\n\n"
        "The text may be long (search snippets, MRDS, AI summaries). "
        "Find ANY explicit township + range (and section if stated), e.g. T48N R2E Sec 15, "
        "or phrased as Township … Range … Section ….\n"
        "Return ONLY valid JSON, no markdown fences:\n"
        '{"plss":"T48N R2E Sec15 or null","township":"48N or null","range":"2E or null",'
        '"section":"15 or null","confidence":"high|medium|low","rationale":"quote the phrase you used"}\n'
        "If nothing explicit appears, set plss to null.\n\nTEXT:\n"
    ) + (combined[:14000] if combined else "")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash:generateContent"
    )
    try:
        r = requests.post(
            url,
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        parts = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return _parse_json_object(text)
    except Exception as e:
        log.debug("Gemini PLSS extract failed: %s", e)
        return None


def _openai_mini_extract_plss(
    client: Any,
    combined: str,
    *,
    name: str,
    state: str,
    county: str,
    target_id: int,
) -> dict[str, Any] | None:
    """Second-stage: pull PLSS buried in long prose (same idea as Gemini in-browser)."""
    prompt = (
        "Extract explicit US PLSS (township, range, section) from the text below. "
        "The text may be verbose; PLSS may appear mid-paragraph.\n"
        f"Target id: {target_id}\n"
        f"Mine name: {name}\nState: {state} County: {county}\n\n"
        "Return ONLY valid JSON:\n"
        '{"plss":"T12N R5E Sec14 or null","township":"12N or null","range":"5E or null",'
        '"section":"14 or null","confidence":"high|medium|low","rationale":"short quote"}\n\nTEXT:\n'
    ) + (combined[:14000] if combined else "")
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=800,
        )
        raw = resp.choices[0].message.content or ""
        return _parse_json_object(raw)
    except Exception as e:
        log.debug("OpenAI mini PLSS extract failed: %s", e)
        return None


def _normalize_llm_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    plss = (row.get("plss") or "").strip()
    twp = (row.get("township") or "").strip()
    rng = (row.get("range") or "").strip()
    sec = (row.get("section") or "").strip()
    if not plss and twp and rng:
        plss = "T" + twp + " R" + rng + (" Sec" + sec if sec else "")
    if not plss:
        return None
    return {
        "plss": plss,
        "township": twp or None,
        "range": rng or None,
        "section": sec or None,
        "confidence": (row.get("confidence") or "medium").strip(),
        "rationale": (row.get("rationale") or "").strip(),
    }


def lookup_plss_for_target_ids(ids: list[int], *, dry_run: bool = False) -> dict[str, Any]:
    """
    For each target id (expected: missing PLSS), call AI to infer PLSS from name, state, county, notes.
    When dry_run=False (default), updates DB on success. When dry_run=True, returns proposals only
    (pending_apply) for review — no writes. Requires OPENAI_API_KEY.
    """
    if not settings.OPENAI_API_KEY:
        return {
            "ok": False,
            "error": "OPENAI_API_KEY not set in .env",
            "results": [],
        }
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {"ok": True, "results": [], "message": "No ids provided"}

    if dry_run and len(ids) > MAX_PREVIEW_IDS:
        return {
            "ok": False,
            "error": f"Preview is limited to {MAX_PREVIEW_IDS} targets per request. Select fewer rows or run in batches.",
            "results": [],
        }

    log.info(
        "plss_ai_lookup: %s for %d target(s), dry_run=%s",
        "preview" if dry_run else "apply-immediate",
        len(ids),
        dry_run,
    )

    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    results: list[dict[str, Any]] = []
    web_lookups_done = 0

    for batch_start in range(0, len(ids), BATCH):
        batch_ids = ids[batch_start : batch_start + BATCH]
        entries: list[dict[str, Any]] = []
        for tid in batch_ids:
            row = get_area(tid)
            if not row:
                results.append(
                    {
                        "id": tid,
                        "name": None,
                        "ok": False,
                        "kind": "not_found",
                        "error": "Target not found",
                    }
                )
                continue
            plss_norm = (row.get("plss_normalized") or "").strip() if row.get("plss_normalized") else ""
            # Align with clean-preview "no PLSS": only skip when we already have a normalized key.
            # (location_plss may hold free text; that must not block AI fill.)
            if plss_norm:
                results.append(
                    {
                        "id": tid,
                        "name": (row.get("name") or "").strip() or None,
                        "ok": False,
                        "kind": "skipped_has_normalized_plss",
                        "error": "Target already has a normalized PLSS; skipped",
                    }
                )
                continue
            notes = row.get("validity_notes") or ""
            county = county_from_validity_notes(notes)
            st = (row.get("state_abbr") or "").strip().upper()[:2]
            name = (row.get("name") or "").strip()
            if name and web_lookups_done > 0 and WEB_SNIPPET_DELAY_SEC > 0:
                time.sleep(WEB_SNIPPET_DELAY_SEC)
            web = _web_snippets(name, st, county) if name else ""
            if name:
                web_lookups_done += 1
            notes_excerpt = notes[:1200] if notes else ""
            combined = (notes_excerpt + "\n---\n" + web).strip()

            quick = extract_plss_from_prose(combined)
            if quick:
                st_abbr = st or None
                note = (
                    f"[PLSS extracted from web/notes text — {quick.get('extract_kind', 'regex')}] "
                    f"{quick.get('span_quote', '')}"
                )
                if dry_run:
                    chk = plss_lookup_would_conflict(
                        tid,
                        location_plss=quick["plss"],
                        state_abbr=st_abbr,
                        township=quick.get("township"),
                        range_val=quick.get("range"),
                        section=quick.get("section"),
                    )
                    if not chk["ok"] and chk.get("reason") == "duplicate_plss":
                        oid = chk.get("conflicting_id")
                        oname = chk.get("conflicting_name")
                        dup = (
                            f'Extracted PLSS "{quick["plss"]}" is already assigned to '
                            f'"{oname or "another target"}"'
                            + (f" (id {oid})" if oid else "")
                        )
                        results.append(
                            {
                                "id": tid,
                                "name": name or None,
                                "ok": False,
                                "kind": "duplicate_plss",
                                "error": dup,
                                "skip_reason": "duplicate_plss",
                                "duplicate_of": oid,
                                "duplicate_name": oname,
                                "plss": quick["plss"],
                            }
                        )
                        continue
                    if not chk["ok"]:
                        results.append(
                            {
                                "id": tid,
                                "name": name or None,
                                "ok": False,
                                "kind": "regex_preview_failed",
                                "error": chk.get("reason") or "Could not validate PLSS",
                            }
                        )
                        continue
                    results.append(
                        {
                            "id": tid,
                            "name": name or None,
                            "ok": True,
                            "pending_apply": True,
                            "kind": "extracted_regex",
                            "plss": quick["plss"],
                            "township": quick.get("township"),
                            "range": quick.get("range"),
                            "section": quick.get("section"),
                            "latitude": None,
                            "longitude": None,
                            "notes_append": note.strip(),
                            "confidence": "medium",
                        }
                    )
                    continue
                applied = apply_plss_lookup_result(
                    tid,
                    location_plss=quick["plss"],
                    state_abbr=st_abbr,
                    township=quick.get("township"),
                    range_val=quick.get("range"),
                    section=quick.get("section"),
                    latitude=None,
                    longitude=None,
                    notes_append=note.strip(),
                )
                if applied.applied:
                    results.append(
                        {
                            "id": tid,
                            "name": name or None,
                            "ok": True,
                            "kind": "extracted_regex",
                            "plss": quick["plss"],
                            "confidence": "medium",
                        }
                    )
                    continue
                if applied.reason == "duplicate_plss":
                    dup = (
                        f'Extracted PLSS "{quick["plss"]}" is already assigned to '
                        f'"{applied.conflicting_name or "another target"}"'
                        + (f" (id {applied.conflicting_id})" if applied.conflicting_id else "")
                    )
                    results.append(
                        {
                            "id": tid,
                            "name": name or None,
                            "ok": False,
                            "kind": "duplicate_plss",
                            "error": dup,
                            "skip_reason": "duplicate_plss",
                            "duplicate_of": applied.conflicting_id,
                            "duplicate_name": applied.conflicting_name,
                        }
                    )
                    continue
                results.append(
                    {
                        "id": tid,
                        "name": name or None,
                        "ok": False,
                        "kind": "regex_apply_failed",
                        "error": applied.reason or "Could not save extracted PLSS",
                    }
                )
                continue

            entries.append(
                {
                    "id": tid,
                    "name": name,
                    "state": st,
                    "county": county,
                    "notes_excerpt": notes_excerpt,
                    "web_snippets": web[:12000],
                    "combined": combined[:16000],
                }
            )

        if not entries:
            continue

        system = (
            "You are a US mining geography expert. Given mine/prospect names and state/county context, "
            "infer the Public Land Survey System (PLSS) for historic western US mining sites.\n"
            "Web snippets are often LONG paragraphs from MRDS, forums, or search AI overviews — "
            "PLSS may appear in the middle (e.g. '…located in T48N R2E Sec 15…'). "
            "READ the full snippet text and extract any explicit T/R/Sec before concluding you cannot infer.\n"
            "Use general knowledge only when snippets do not contain explicit PLSS.\n"
            "Return ONLY valid JSON: "
            '{"results": [{"id": 123, "plss": "T12N R5E Sec14", "township": "12N", "range": "5E", "section": "14", '
            '"latitude": null, "longitude": null, "confidence": "high|medium|low", '
            '"rationale": "one sentence; cite snippet if used"}]} '
            "Rules:\n"
            "- id must match the input id\n"
            "- plss must use format like T12N R5E Sec14 (T then township N/S, R then range E/W, Sec + number)\n"
            "- If snippets contain explicit PLSS strings, use them (do not ignore them because text is long)\n"
            "- If you cannot find explicit township AND range in snippets or reliable knowledge, set plss to null\n"
            "- Prefer section-level PLSS when possible\n"
            "- Do not invent precise coordinates unless you have a well-known site; otherwise null\n"
            "- No markdown fences"
        )
        user = json.dumps(
            {"targets": [{k: v for k, v in ent.items() if k != "combined"} for ent in entries]},
            indent=2,
        )

        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.15,
                max_tokens=4000,
            )
            raw = resp.choices[0].message.content or ""
            data = _parse_json(raw)
            out = data.get("results", [])
        except Exception as exc:
            log.exception("PLSS AI batch failed")
            for ent in entries:
                results.append(
                    {
                        "id": ent["id"],
                        "name": (ent.get("name") or "").strip() or None,
                        "ok": False,
                        "kind": "openai_error",
                        "error": str(exc),
                    }
                )
            continue

        by_id: dict[int, dict[str, Any]] = {}
        for r in out:
            if not isinstance(r, dict):
                continue
            rid = _coerce_id(r.get("id"))
            if rid is not None:
                by_id[rid] = r

        for e in entries:
            tid = e["id"]
            r = by_id.get(tid)
            if not r:
                results.append(
                    {
                        "id": tid,
                        "name": (e.get("name") or "").strip() or None,
                        "ok": False,
                        "kind": "ai_missing_row",
                        "error": "AI returned no result for this id (check model JSON id types)",
                    }
                )
                continue
            plss = (r.get("plss") or "").strip()
            twp = (r.get("township") or "").strip()
            rng = (r.get("range") or "").strip()
            sec = (r.get("section") or "").strip()
            if not plss and twp and rng:
                plss = "T" + twp + " R" + rng + (" Sec" + sec if sec else "")
            result_kind: str = "applied"
            deep_row: dict[str, Any] | None = None
            if not plss:
                combined = e.get("combined") or ""
                deep_label = ""
                if settings.GEMINI_API_KEY:
                    deep_row = _normalize_llm_row(
                        _gemini_extract_plss(
                            combined,
                            name=e.get("name") or "",
                            state=e.get("state") or "",
                            county=e.get("county") or "",
                        )
                    )
                    if deep_row:
                        deep_label = "extracted_gemini"
                if not deep_row:
                    deep_row = _normalize_llm_row(
                        _openai_mini_extract_plss(
                            client,
                            combined,
                            name=e.get("name") or "",
                            state=e.get("state") or "",
                            county=e.get("county") or "",
                            target_id=tid,
                        )
                    )
                    if deep_row:
                        deep_label = "extracted_mini"
                if deep_row:
                    plss = deep_row["plss"]
                    twp = (deep_row.get("township") or "").strip()
                    rng = (deep_row.get("range") or "").strip()
                    sec = (deep_row.get("section") or "").strip()
                    result_kind = deep_label
                else:
                    results.append(
                        {
                            "id": tid,
                            "name": (e.get("name") or "").strip() or None,
                            "ok": False,
                            "kind": "ai_no_plss",
                            "error": (r.get("rationale") or "Could not infer PLSS"),
                        }
                    )
                    continue
            if result_kind == "applied":
                lat = r.get("latitude")
                lon = r.get("longitude")
                try:
                    lat = float(lat) if lat is not None else None
                except (TypeError, ValueError):
                    lat = None
                try:
                    lon = float(lon) if lon is not None else None
                except (TypeError, ValueError):
                    lon = None
                conf = (r.get("confidence") or "").strip()
                rationale = (r.get("rationale") or "").strip()
                note = f"[AI PLSS lookup, confidence {conf or 'unknown'}] {rationale}".strip()
            else:
                lat = None
                lon = None
                conf = (deep_row or {}).get("confidence", "medium") if deep_row else "medium"
                rationale = (deep_row or {}).get("rationale", "").strip() if deep_row else ""
                label = "Gemini" if result_kind == "extracted_gemini" else "mini-model"
                note = f"[PLSS from verbose text — {label}, confidence {conf or 'unknown'}] {rationale}".strip()
            row = get_area(tid)
            nm = (e.get("name") or "").strip() or None
            if row and row.get("name"):
                nm = (row.get("name") or "").strip() or nm
            st_abbr = (row.get("state_abbr") or "").strip().upper()[:2] if row else None
            if not st_abbr:
                st_abbr = e.get("state") or None
            if dry_run:
                chk = plss_lookup_would_conflict(
                    tid,
                    location_plss=plss,
                    state_abbr=st_abbr,
                    township=twp or None,
                    range_val=rng or None,
                    section=sec or None,
                )
                if not chk["ok"] and chk.get("reason") == "duplicate_plss":
                    oid = chk.get("conflicting_id")
                    oname = chk.get("conflicting_name")
                    dup = (
                        f'Inferred PLSS "{plss}" is already assigned to '
                        f'"{oname or "another target"}"'
                        + (f" (id {oid})" if oid else "")
                    )
                    results.append(
                        {
                            "id": tid,
                            "name": nm,
                            "ok": False,
                            "kind": "duplicate_plss",
                            "error": dup,
                            "skip_reason": "duplicate_plss",
                            "duplicate_of": oid,
                            "duplicate_name": oname,
                            "plss": plss,
                        }
                    )
                    continue
                if not chk["ok"]:
                    results.append(
                        {
                            "id": tid,
                            "name": nm,
                            "ok": False,
                            "kind": "ai_preview_failed",
                            "error": chk.get("reason") or "Validation failed",
                        }
                    )
                    continue
                results.append(
                    {
                        "id": tid,
                        "name": nm,
                        "ok": True,
                        "pending_apply": True,
                        "kind": result_kind,
                        "plss": plss,
                        "township": twp or None,
                        "range": rng or None,
                        "section": sec or None,
                        "latitude": lat,
                        "longitude": lon,
                        "notes_append": note,
                        "confidence": conf,
                    }
                )
                continue
            applied = apply_plss_lookup_result(
                tid,
                location_plss=plss,
                state_abbr=st_abbr,
                township=twp or None,
                range_val=rng or None,
                section=sec or None,
                latitude=lat,
                longitude=lon,
                notes_append=note,
            )
            if applied.applied:
                results.append(
                    {
                        "id": tid,
                        "name": nm,
                        "ok": True,
                        "kind": result_kind,
                        "plss": plss,
                        "confidence": conf,
                    }
                )
            elif applied.reason == "duplicate_plss":
                dup = (
                    f'Inferred PLSS "{plss}" is already assigned to '
                    f'"{applied.conflicting_name or "another target"}"'
                    + (f" (id {applied.conflicting_id})" if applied.conflicting_id else "")
                    + ". Notes were updated with details."
                )
                results.append(
                    {
                        "id": tid,
                        "name": nm,
                        "ok": False,
                        "kind": "duplicate_plss",
                        "error": dup,
                        "skip_reason": "duplicate_plss",
                        "duplicate_of": applied.conflicting_id,
                        "duplicate_name": applied.conflicting_name,
                    }
                )
            else:
                err = {
                    "not_found": "Target not found",
                    "empty": "Empty PLSS",
                    "no_update": "Database update failed",
                }.get(applied.reason or "", "Database update failed")
                results.append(
                    {
                        "id": tid,
                        "name": nm,
                        "ok": False,
                        "kind": "db_failed",
                        "error": err,
                    }
                )

    ok_count = sum(1 for x in results if x.get("ok") is True)
    pending_count = sum(1 for x in results if x.get("pending_apply") is True)

    def _kind(x: dict[str, Any]) -> str:
        return str(x.get("kind") or ("duplicate_plss" if x.get("skip_reason") == "duplicate_plss" else "unknown"))

    summary: dict[str, int] = {}
    for x in results:
        k = _kind(x)
        summary[k] = summary.get(k, 0) + 1

    dup_count = summary.get("duplicate_plss", 0)
    if dry_run:
        parts = [
            f"Preview: {pending_count} target(s) with proposed PLSS — review and click Apply to save.",
        ]
        if dup_count:
            parts.append(f"{dup_count} would conflict with an existing target’s PLSS.")
    else:
        parts = [f"Applied PLSS to {ok_count} target(s)."]
        if dup_count:
            parts.append(f"{dup_count} skipped — PLSS already used by another target.")

    if (pending_count if dry_run else ok_count) == 0 and results:
        explain: list[str] = []
        order = (
            ("skipped_has_normalized_plss", "already have normalized PLSS"),
            ("ai_no_plss", "AI could not infer township/range"),
            ("ai_missing_row", "AI JSON missing this id (often id type mismatch)"),
            ("duplicate_plss", "inferred PLSS matches another target"),
            ("extracted_regex", "applied via pattern match in web/notes"),
            ("extracted_gemini", "applied via Gemini extraction from long text"),
            ("extracted_mini", "applied via secondary model extraction"),
            ("regex_preview_failed", "regex PLSS failed validation in preview"),
            ("ai_preview_failed", "AI PLSS failed validation in preview"),
            ("regex_apply_failed", "regex found PLSS but save failed"),
            ("openai_error", "OpenAI / network error"),
            ("db_failed", "database update failed"),
            ("not_found", "target not found"),
        )
        for key, label in order:
            n = summary.get(key, 0)
            if n:
                explain.append(f"{n} {label}")
        if explain:
            parts.append(("Why none ready: " if dry_run else "Why none applied: ") + "; ".join(explain) + ".")
        else:
            parts.append(
                ("Why none ready: " if dry_run else "Why none applied: ")
                + ", ".join(f"{k}: {v}" for k, v in sorted(summary.items()) if v)
                + "."
            )

    log.info(
        "plss_ai_lookup finished dry_run=%s pending=%d ok=%d rows=%d",
        dry_run,
        pending_count,
        ok_count,
        len(results),
    )

    return {
        "ok": True,
        "results": results,
        "updated": ok_count if not dry_run else 0,
        "pending_count": pending_count,
        "dry_run": dry_run,
        "summary": summary,
        "message": " ".join(parts),
    }


def apply_plss_ai_proposals(items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Persist PLSS proposals after user review (Clean Targets flow).
    Each item: id, plss (or location_plss), optional township, range, section, latitude, longitude, notes_append.
    Skips targets that already have normalized PLSS (safety). Max MAX_APPLY_ITEMS per request.
    """
    if not items:
        return {"ok": True, "results": [], "updated": 0, "message": "No items to apply"}

    if len(items) > MAX_APPLY_ITEMS:
        return {
            "ok": False,
            "error": f"Apply is limited to {MAX_APPLY_ITEMS} targets per request.",
            "results": [],
        }

    log.info("plss_ai_apply_proposals: %d item(s)", len(items))

    results: list[dict[str, Any]] = []
    applied_n = 0

    for raw in items:
        if not isinstance(raw, dict):
            continue
        try:
            tid = int(raw.get("id"))
        except (TypeError, ValueError):
            results.append({"id": raw.get("id"), "ok": False, "kind": "bad_id", "error": "Invalid id"})
            continue

        plss = (raw.get("plss") or raw.get("location_plss") or "").strip()
        if not plss:
            results.append({"id": tid, "ok": False, "kind": "empty_plss", "error": "Missing plss"})
            continue

        row = get_area(tid)
        nm = (row.get("name") or "").strip() if row else None
        if not row:
            results.append({"id": tid, "name": nm, "ok": False, "kind": "not_found", "error": "Target not found"})
            continue

        plss_norm = (row.get("plss_normalized") or "").strip() if row.get("plss_normalized") else ""
        if plss_norm:
            results.append(
                {
                    "id": tid,
                    "name": nm,
                    "ok": False,
                    "kind": "skipped_has_normalized_plss",
                    "error": "Target already has normalized PLSS; apply skipped (refresh preview if needed)",
                }
            )
            continue

        st_abbr = (row.get("state_abbr") or "").strip().upper()[:2] or None

        def _opt_str_val(v: Any) -> str | None:
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        twp = _opt_str_val(raw.get("township"))
        rng = _opt_str_val(raw.get("range"))
        sec = _opt_str_val(raw.get("section"))

        lat = raw.get("latitude")
        lon = raw.get("longitude")
        try:
            lat = float(lat) if lat is not None and lat != "" else None
        except (TypeError, ValueError):
            lat = None
        try:
            lon = float(lon) if lon is not None and lon != "" else None
        except (TypeError, ValueError):
            lon = None

        notes_append = raw.get("notes_append")
        if notes_append is not None and not isinstance(notes_append, str):
            notes_append = str(notes_append)
        notes_append = (notes_append or "").strip() or None

        applied = apply_plss_lookup_result(
            tid,
            location_plss=plss,
            state_abbr=st_abbr,
            township=twp,
            range_val=rng,
            section=sec,
            latitude=lat,
            longitude=lon,
            notes_append=notes_append,
        )

        if applied.applied:
            applied_n += 1
            results.append(
                {
                    "id": tid,
                    "name": nm,
                    "ok": True,
                    "kind": "applied_from_preview",
                    "plss": plss,
                }
            )
        elif applied.reason == "duplicate_plss":
            dup = (
                f'PLSS "{plss}" is already assigned to '
                f'"{applied.conflicting_name or "another target"}"'
                + (f" (id {applied.conflicting_id})" if applied.conflicting_id else "")
            )
            results.append(
                {
                    "id": tid,
                    "name": nm,
                    "ok": False,
                    "kind": "duplicate_plss",
                    "error": dup,
                    "duplicate_of": applied.conflicting_id,
                    "duplicate_name": applied.conflicting_name,
                }
            )
        else:
            err = {
                "not_found": "Target not found",
                "empty": "Empty PLSS",
                "no_update": "Database update failed",
            }.get(applied.reason or "", "Database update failed")
            results.append({"id": tid, "name": nm, "ok": False, "kind": "db_failed", "error": err})

    log.info("plss_ai_apply_proposals finished applied=%d of %d", applied_n, len(items))

    return {
        "ok": True,
        "results": results,
        "updated": applied_n,
        "message": f"Applied PLSS to {applied_n} target(s).",
    }
