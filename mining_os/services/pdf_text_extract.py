"""Robust PDF text extraction for historic USGS scans (OME/DMA/DMEA-style).

PyPDF2 alone often fails on older scans (image-only pages, odd xrefs). We try
PyMuPDF first, then pypdf/PyPDF2, then optional OCR (Tesseract) on rendered pages.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

log = logging.getLogger("mining_os.pdf_text_extract")

_tesseract_cmd_configured: str | None = None


def _resolve_tesseract_cmd() -> str | None:
    """Prefer explicit env/settings, then common Homebrew paths, then PATH."""
    env_cmd = os.environ.get("TESSERACT_CMD", "").strip()
    if env_cmd and Path(env_cmd).is_file():
        return env_cmd
    try:
        from mining_os.config import settings

        cfg_cmd = settings.TESSERACT_CMD.strip()
        if cfg_cmd and Path(cfg_cmd).is_file():
            return cfg_cmd
    except Exception:
        pass
    for candidate in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if Path(candidate).is_file():
            return candidate
    return shutil.which("tesseract")


def _ensure_pytesseract_cmd() -> str | None:
    """Point pytesseract at a real binary once per process (avoids wrong PATH / hangs)."""
    global _tesseract_cmd_configured
    import pytesseract.pytesseract as pt_mod

    if _tesseract_cmd_configured is not None:
        return _tesseract_cmd_configured or None
    resolved = _resolve_tesseract_cmd()
    if resolved:
        pt_mod.tesseract_cmd = resolved
        _tesseract_cmd_configured = resolved
        log.debug("pytesseract.tesseract_cmd = %s", resolved)
    else:
        _tesseract_cmd_configured = ""
        log.warning("No tesseract binary found; OCR will fail until TESSERACT_CMD or PATH is set")
    return resolved if resolved else None

# Minimum non-whitespace characters to consider "has text" before OCR
MIN_TEXT_CHARS_BEFORE_OCR = 80
MAX_CHARS_FOR_AI = 80000


@dataclass
class PdfExtractResult:
    text: str
    pdf_opened: bool
    had_meaningful_text: bool
    methods_tried: list[str]
    ocr_used: bool
    open_error: str | None


def _text_meaningful(s: str) -> bool:
    t = "".join(s.split())
    return len(t) >= MIN_TEXT_CHARS_BEFORE_OCR


def _extract_pymupdf(pdf_bytes: bytes) -> tuple[str, bool, str | None]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "", False, None
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts: list[str] = []
        for i in range(doc.page_count):
            page = doc.load_page(i)
            parts.append(page.get_text("text") or "")
        return "\n\n".join(parts), True, None
    except Exception as e:
        return "", False, str(e)
    finally:
        if doc is not None:
            doc.close()


def _extract_pypdf_family(pdf_bytes: bytes) -> tuple[str, bool, str | None]:
    reader_cls: Any = None
    try:
        from pypdf import PdfReader as PR

        reader_cls = PR
    except ImportError:
        try:
            from PyPDF2 import PdfReader as PR2

            reader_cls = PR2
        except ImportError:
            return "", False, "Neither pypdf nor PyPDF2 is installed"
    try:
        reader = reader_cls(BytesIO(pdf_bytes), strict=False)
        parts: list[str] = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n\n".join(parts), True, None
    except Exception as e:
        return "", False, str(e)


def _ocr_pages_pymupdf(
    pdf_bytes: bytes,
    max_pages: int,
    *,
    page_timeout_sec: int = 120,
) -> tuple[str, bool]:
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError:
        return "", False
    if not _ensure_pytesseract_cmd():
        log.warning("Skipping OCR: tesseract binary not found")
        return "", False
    doc = None
    timeout_kw: dict[str, int] = {}
    if page_timeout_sec and page_timeout_sec > 0:
        timeout_kw["timeout"] = page_timeout_sec
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts: list[str] = []
        n = min(doc.page_count, max_pages)
        mat = fitz.Matrix(2.0, 2.0)
        for i in range(n):
            pix = doc.load_page(i).get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            parts.append(pytesseract.image_to_string(img, **timeout_kw) or "")
        return "\n\n".join(parts), True
    except Exception as e:
        log.warning("OCR pass failed: %s", e)
        return "", False
    finally:
        if doc is not None:
            doc.close()


def extract_pdf_text(
    pdf_bytes: bytes,
    *,
    ocr_max_pages: int = 8,
    ocr_page_timeout_sec: int = 120,
) -> PdfExtractResult:
    """Extract text using multiple strategies. Sets ``had_meaningful_text`` when
    enough text exists for downstream AI (or OCR produced enough).
    """
    methods: list[str] = []
    open_error: str | None = None
    ocr_used = False

    if not pdf_bytes or len(pdf_bytes) < 8:
        return PdfExtractResult(
            text="",
            pdf_opened=False,
            had_meaningful_text=False,
            methods_tried=[],
            ocr_used=False,
            open_error="Empty or too-small file (not a valid PDF).",
        )

    if not pdf_bytes[:5].startswith(b"%PDF"):
        return PdfExtractResult(
            text="",
            pdf_opened=False,
            had_meaningful_text=False,
            methods_tried=[],
            ocr_used=False,
            open_error="File does not start with %PDF — response may be HTML or corrupt.",
        )

    text, opened, err = _extract_pymupdf(pdf_bytes)
    methods.append("pymupdf")
    if not opened and err:
        open_error = err
    elif opened and _text_meaningful(text):
        t = text[:MAX_CHARS_FOR_AI] if len(text) > MAX_CHARS_FOR_AI else text
        return PdfExtractResult(
            text=t,
            pdf_opened=True,
            had_meaningful_text=True,
            methods_tried=methods,
            ocr_used=False,
            open_error=None,
        )

    # Fallback: pypdf / PyPDF2
    text2, opened2, err2 = _extract_pypdf_family(pdf_bytes)
    methods.append("pypdf_or_pypdf2")
    if opened2 and _text_meaningful(text2):
        merged = text2
        t = merged[:MAX_CHARS_FOR_AI] if len(merged) > MAX_CHARS_FOR_AI else merged
        return PdfExtractResult(
            text=t,
            pdf_opened=True,
            had_meaningful_text=True,
            methods_tried=methods,
            ocr_used=False,
            open_error=None,
        )

    if opened2:
        text = text2
        open_error = None
        pdf_opened = True
    elif opened:
        pdf_opened = True
        open_error = None
    else:
        pdf_opened = False
        open_error = open_error or err2 or "Could not open PDF with any reader."

    if pdf_opened and not _text_meaningful(text) and ocr_max_pages > 0:
        ocr_text, ocr_ok = _ocr_pages_pymupdf(
            pdf_bytes,
            ocr_max_pages,
            page_timeout_sec=ocr_page_timeout_sec,
        )
        methods.append(f"ocr(max_pages={ocr_max_pages})")
        if ocr_ok and ocr_text.strip():
            ocr_used = True
            if _text_meaningful(ocr_text):
                t = ocr_text[:MAX_CHARS_FOR_AI] if len(ocr_text) > MAX_CHARS_FOR_AI else ocr_text
                return PdfExtractResult(
                    text=t,
                    pdf_opened=True,
                    had_meaningful_text=True,
                    methods_tried=methods,
                    ocr_used=True,
                    open_error=None,
                )
            text = ocr_text

    if not pdf_opened:
        return PdfExtractResult(
            text="",
            pdf_opened=False,
            had_meaningful_text=False,
            methods_tried=methods,
            ocr_used=ocr_used,
            open_error=open_error or "Unknown PDF open failure.",
        )

    if not _text_meaningful(text):
        return PdfExtractResult(
            text=text[:MAX_CHARS_FOR_AI] if text else "",
            pdf_opened=True,
            had_meaningful_text=False,
            methods_tried=methods,
            ocr_used=ocr_used,
            open_error=None,
        )

    t = text[:MAX_CHARS_FOR_AI] if len(text) > MAX_CHARS_FOR_AI else text
    return PdfExtractResult(
        text=t,
        pdf_opened=True,
        had_meaningful_text=True,
        methods_tried=methods,
        ocr_used=ocr_used,
        open_error=None,
    )
