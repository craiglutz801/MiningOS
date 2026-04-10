"""Batch process mining report PDFs from a CSV of report metadata (USGS DS 1004 scans)."""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from typing import Any

from mining_os.config import settings

log = logging.getLogger("mining_os.batch_report_processor")

USGS_DS1004_BASE = "https://pubs.usgs.gov/ds/1004/scans"
# OME / DMEA inventory rows use the same scan folder and _OME.pdf suffix on USGS.
OME_URL_TEMPLATE = USGS_DS1004_BASE + "/{state}/ome/{docket}_OME.pdf"
# Defense Minerals Administration — docket is zero-padded to 4 digits in the path.
DMA_URL_TEMPLATE = USGS_DS1004_BASE + "/{state}/dma/{docket_padded}_DMA.pdf"


def _max_pdf_bytes() -> int:
    return int(settings.BATCH_MAX_PDF_MB * 1024 * 1024)


def _parse_file_size_mb(size_str: str) -> float | None:
    """Parse file size string like '5 MB', '<1 MB', 'No scan' into MB."""
    s = size_str.strip().lower()
    if "no scan" in s:
        return None
    s = s.replace("mb", "").replace("<", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _dma_docket_padded(docket: str) -> str:
    digits = "".join(c for c in (docket or "") if c.isdigit())
    if not digits:
        return (docket or "").strip()
    return f"{int(digits):04d}"


def build_usgs_scan_url(docket: str, state_abbr: str, report_series: str) -> str:
    """Build USGS DS-1004 PDF URL. ``report_series``: OME | DMEA | DMA (case-insensitive)."""
    st = state_abbr.strip().lower()
    series = (report_series or "OME").strip().upper()
    if series == "DMA":
        pad = _dma_docket_padded(docket.strip())
        return DMA_URL_TEMPLATE.format(state=st, docket_padded=pad)
    # DMEA list uses the same scans as OME inventory
    return OME_URL_TEMPLATE.format(state=st, docket=docket.strip())


def _normalize_column_name(col: str) -> str:
    """Map CSV column names to internal keys."""
    c = col.strip().lower().replace(" ", "_")
    mapping = {
        "docket": "docket",
        "file_size": "file_size",
        "property_name": "property_name",
        "state_abbreviation": "state_abbr",
        "state_abbr": "state_abbr",
        "state": "state_abbr",
        "county": "county",
        "all_commodities": "minerals",
        "commodities": "minerals",
        "minerals": "minerals",
        "commodity": "minerals",
        "url": "url",
        "link": "url",
        "report_url": "url",
        "pdf_url": "url",
        "report_series": "report_series",
        "series": "report_series",
        "scan_series": "report_series",
        "report_type": "report_series",
    }
    return mapping.get(c, c)


def parse_batch_csv(csv_bytes: bytes | str, report_series: str = "OME") -> list[dict[str, Any]]:
    """Parse a batch report CSV into a list of row dicts.

    ``report_series`` should be ``OME``, ``DMEA``, or ``DMA`` (from UI or CSV filename).
    Per-row override: optional column ``report_series`` / ``series`` / ``report_type``.
    """
    if isinstance(csv_bytes, bytes):
        text = csv_bytes.decode("utf-8-sig", errors="replace")
    else:
        text = csv_bytes

    default_series = (report_series or "OME").strip().upper()
    if default_series not in ("OME", "DMEA", "DMA"):
        default_series = "OME"

    max_mb = settings.BATCH_MAX_PDF_MB
    reader = csv.DictReader(io.StringIO(text))
    rows_out: list[dict[str, Any]] = []
    for raw_row in reader:
        row: dict[str, Any] = {}
        for k, v in raw_row.items():
            norm_key = _normalize_column_name(k or "")
            row[norm_key] = (v or "").strip()

        row_series = (row.get("report_series") or "").strip().upper()
        if row_series not in ("OME", "DMEA", "DMA"):
            row_series = default_series
        # USGS hosts DMEA inventory scans under the same paths as OME (_OME.pdf).
        url_series = "DMA" if row_series == "DMA" else "OME"

        name = row.get("property_name", "").strip()
        if not name or name.lower() == "none identified":
            tag = "DMA" if row_series == "DMA" else ("DMEA" if row_series == "DMEA" else "OME")
            name = f"{tag} Docket {row.get('docket', 'Unknown')}"

        state = row.get("state_abbr", "").strip().upper()[:2]
        file_size_mb = _parse_file_size_mb(row.get("file_size", ""))
        has_scan = file_size_mb is not None
        downloadable = has_scan and file_size_mb is not None and file_size_mb <= max_mb

        url = row.get("url", "").strip()
        if not url and row.get("docket") and state:
            url = build_usgs_scan_url(row["docket"], state, url_series)

        minerals_raw = row.get("minerals", "")
        minerals = [
            m.strip()
            for m in re.split(r"[,;/|]", minerals_raw)
            if m.strip() and m.strip().lower() != "none identified"
        ]

        rows_out.append(
            {
                "docket": row.get("docket", ""),
                "name": name,
                "state_abbr": state,
                "county": row.get("county", "").strip(),
                "minerals": minerals,
                "file_size": row.get("file_size", "").strip(),
                "file_size_mb": file_size_mb,
                "has_scan": has_scan,
                "downloadable": downloadable,
                "url": url if has_scan else "",
                "report_series": row_series,
                "skipped_reason": (
                    "No scan available"
                    if not has_scan
                    else f"File too large ({file_size_mb:.0f} MB > {max_mb:.0f} MB limit; raise BATCH_MAX_PDF_MB in .env)"
                    if not downloadable
                    else None
                ),
            }
        )
    return rows_out


def download_pdf(url: str, timeout: int = 120) -> tuple[bytes | None, str | None]:
    """Download a PDF from URL. Returns (bytes, None) on success, (None, error_message) on failure."""
    import requests

    max_bytes = _max_pdf_bytes()
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            stream=True,
            headers={"User-Agent": "MiningOS/1.0 (research tool)"},
        )
        if resp.status_code == 404:
            return None, f"Download failed: HTTP 404 (file not found at USGS for this docket/state/series)."
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
            log.warning("URL %s returned content-type %s, trying anyway", url, content_type)

        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                return None, f"Download exceeded BATCH_MAX_PDF_MB ({settings.BATCH_MAX_PDF_MB:.0f} MB)."
        data = b"".join(chunks)
        if not data:
            return None, "Download returned empty body."
        if not data[:5].startswith(b"%PDF"):
            return None, "Download did not return a PDF (missing %PDF header — may be HTML error page)."
        return data, None
    except requests.RequestException as e:
        log.exception("Failed to download PDF from %s", url)
        return None, f"Download failed: {e!s}"


def process_parsed_batch_rows(
    rows: list[dict[str, Any]],
    *,
    skip_pdf: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Download + AI-extract for pre-parsed batch rows. Used by HTTP API and ``process_batch``."""
    from mining_os.services.pdf_report_processor import process_pdf_report

    results: list[dict[str, Any]] = []
    processed = 0
    skipped = 0
    pdf_errors = 0
    targets_found = 0

    for row in rows:
        entry: dict[str, Any] = {
            **row,
            "pdf_targets": [],
            "pdf_processed": False,
            "pdf_error": None,
            "pdf_note": None,
        }

        if skip_pdf or not row["downloadable"] or not row["url"]:
            entry["pdf_error"] = row.get("skipped_reason") or "Skipped"
            skipped += 1
            results.append(entry)
            continue

        log.info("Downloading PDF for %s (docket %s) from %s", row["name"], row.get("docket"), row["url"])
        pdf_bytes_dl, dl_err = download_pdf(row["url"])
        if not pdf_bytes_dl:
            entry["pdf_error"] = dl_err or "Download failed."
            pdf_errors += 1
            results.append(entry)
            continue

        log.info("Downloaded %d bytes, processing with AI...", len(pdf_bytes_dl))
        mineral_hint = row["minerals"][0] if row.get("minerals") else None
        try:
            result = process_pdf_report(
                pdf_bytes_dl,
                mineral=mineral_hint,
                state=row["state_abbr"] or None,
            )
        except Exception as e:
            log.exception("PDF processing failed for docket %s", row.get("docket"))
            entry["pdf_error"] = f"Processing crashed: {e!s}"
            pdf_errors += 1
            results.append(entry)
            continue

        entry["pdf_processed"] = True
        entry["pdf_document_opened"] = bool(result.get("pdf_document_opened"))
        entry["had_extractable_text"] = bool(result.get("had_extractable_text"))
        entry["extraction_reached_ai"] = bool(result.get("extraction_reached_ai"))

        if result.get("ok") and result.get("targets"):
            entry["pdf_targets"] = result["targets"]
            targets_found += len(result["targets"])
        elif result.get("ok") and not result.get("targets"):
            entry["pdf_note"] = result.get("pdf_note")
        else:
            entry["pdf_error"] = result.get("error", "Processing failed")
            pdf_errors += 1

        processed += 1
        results.append(entry)
        time.sleep(0.5)

    summary = {
        "total_rows": len(results),
        "processed": processed,
        "skipped": skipped,
        "pdf_errors": pdf_errors,
        "targets_found": targets_found,
    }
    return results, summary


def process_batch(
    csv_bytes: bytes,
    max_rows: int | None = None,
    skip_pdf: bool = False,
    state_filter: str | None = None,
    mineral_filter: str | None = None,
    report_series: str = "OME",
) -> dict[str, Any]:
    """Process a batch CSV: parse rows, optionally download + AI-extract PDFs.

    Returns {"rows": [...], "summary": {...}}.
    """
    rows = parse_batch_csv(csv_bytes, report_series=report_series)

    if state_filter:
        sf = state_filter.strip().upper()
        rows = [r for r in rows if r["state_abbr"] == sf]
    if mineral_filter:
        mf = mineral_filter.strip().lower()
        rows = [r for r in rows if any(mf in m.lower() for m in r["minerals"])]

    if max_rows and max_rows > 0:
        rows = rows[:max_rows]

    results, summary = process_parsed_batch_rows(rows, skip_pdf=skip_pdf)
    return {"rows": results, "summary": summary}
