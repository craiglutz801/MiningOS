"""
MLRS / RAS maintenance-fee detection (aligned with BLM_ClaimAgent ``get_mlrs_links``).

The MLRS case page (https://mlrs.blm.gov/s/blm-case/...) is a Salesforce **Lightning** SPA;
the red overdue banner is rendered client-side, so plain ``requests.get`` returns the
"Loading … CSS Error" shell and never sees the message. The BLM ``reports.blm.gov``
RAS page wraps the Serial Register in an ``<iframe>``. Both are tried with HTTP first
(fast, free, sometimes enough) and a real headless browser is used as a fallback.

Order per claim:

1. **HTTP ``case_page``** — works only when the banner is server-rendered (rare).
2. **HTTP ``payment_report`` (RAS)** + iframe — sometimes enough.
3. **Playwright** (preferred) headless Chromium loads ``case_page`` and waits for the
   banner element. Production-friendly: one Python package + ``playwright install chromium``.
4. **Selenium** as a last resort if Playwright isn't installed.

Env knobs (all optional):

- ``MINING_OS_MLRS_PAYMENT_HEADLESS=1`` — force headless browser even on PaaS.
- ``MINING_OS_MLRS_PAYMENT_HEADLESS=0`` — never use a headless browser.
- Unset — on **localhost** defaults to ON. On **Render/Railway** defaults to OFF unless you set
  ``MINING_OS_MLRS_PAYMENT_HEADLESS=1`` (recommended after build installs Chromium — see
  ``render.yaml`` / ``nixpacks.toml``).

Install browsers (required for MLRS Lightning SPA):

    pip install -r requirements.txt
    python -m playwright install chromium
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any
from urllib.parse import urljoin

import requests

log = logging.getLogger("mining_os.mlrs_case_payment")

# Same phrase ``get_mlrs_links.check_payment_status_and_owner_from_case_page`` searches for.
_UNPAID_LOWER = "maintenance fee payment was not received"
_CLOSING_FRAG = "may result in the closing of the claim"
_STANDARD_MESSAGE = (
    "Maintenance fee payment was not received and may result in the closing of the claim."
)


def _resolve_playwright_max_ms() -> int:
    raw = (os.getenv("MINING_OS_MLRS_PLAYWRIGHT_MAX_MS") or "50000").strip()
    try:
        ms = int(raw)
    except ValueError:
        ms = 50_000
    return max(20_000, min(ms, 120_000))


def _merge_payment_fields(dst: dict[str, Any], src: dict[str, Any]) -> None:
    """Merge enrichment fields; clear stale errors when we resolve unpaid."""
    for key in ("payment_status", "payment_message", "payment_check_error", "payment_check_source"):
        if key in src and src[key] is not None:
            dst[key] = src[key]
    if (src.get("payment_status") or "").strip().lower() == "unpaid":
        dst.pop("payment_check_error", None)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _paas_host() -> bool:
    return bool(
        os.getenv("RENDER")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("K_SERVICE")
        or os.getenv("DYNO")
    )


def _should_try_headless() -> bool:
    """Honor MINING_OS_MLRS_PAYMENT_HEADLESS first, then legacy MINING_OS_MLRS_PAYMENT_SELENIUM."""
    for env_name in ("MINING_OS_MLRS_PAYMENT_HEADLESS", "MINING_OS_MLRS_PAYMENT_SELENIUM"):
        v = (os.getenv(env_name) or "").strip().lower()
        if v in ("0", "false", "no", "off"):
            return False
        if v in ("1", "true", "yes", "on"):
            return True
    return not _paas_host()


def _payment_from_http(case_url: str, timeout: float = 28.0) -> dict[str, Any]:
    try:
        r = requests.get(case_url, timeout=timeout, headers=_BROWSER_HEADERS)
        r.raise_for_status()
        body = (r.text or "").lower()
        if _UNPAID_LOWER in body:
            return {
                "payment_status": "unpaid",
                "payment_message": _STANDARD_MESSAGE,
                "payment_check_source": "mlrs_case_http",
            }
    except Exception as e:
        log.debug("mlrs case http fetch failed %s: %s", case_url, e)
        return {"payment_status": "unknown", "payment_message": None, "payment_check_error": str(e)}
    return {"payment_status": "unknown", "payment_message": None, "payment_check_source": "mlrs_case_http"}


def _body_implies_unpaid(body_lower: str) -> bool:
    if _UNPAID_LOWER in body_lower:
        return True
    # RAS / assessment pages sometimes use the closing sentence next to maintenance wording.
    if _CLOSING_FRAG in body_lower and "maintenance fee" in body_lower:
        return True
    return False


_IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_iframe_srcs(html: str) -> list[str]:
    return [m.group(1).strip() for m in _IFRAME_SRC_RE.finditer(html or "")]


def _payment_from_ras_http(
    report_url: str,
    serial_number: str | None = None,
    timeout: float = 35.0,
) -> dict[str, Any]:
    """
    BLM Serial Register (RAS). The ``report.cfm`` wrapper often embeds the real register in an
    iframe (e.g. ``/iReport/RAS/1/?serial_number=…``). We fetch wrapper + iframe bodies using one
    session (cookies) and scan for the same overdue phrases as the MLRS case banner.
    """
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)

    candidates: list[str] = []
    sn = (serial_number or "").strip()
    if sn:
        candidates.append(f"https://reports.blm.gov/iReport/RAS/1/?serial_number={sn}")
    ru = (report_url or "").strip()
    if ru and ru not in candidates:
        candidates.append(ru)

    combined_lower = ""

    def scan_chunk(chunk: str) -> bool:
        nonlocal combined_lower
        low = (chunk or "").lower()
        combined_lower += "\n" + low
        return _body_implies_unpaid(low)

    try:
        for url in candidates:
            try:
                r = session.get(url, timeout=timeout, allow_redirects=True)
                r.raise_for_status()
                html = r.text or ""
                if scan_chunk(html):
                    return {
                        "payment_status": "unpaid",
                        "payment_message": _STANDARD_MESSAGE,
                        "payment_check_source": "ras_http",
                    }
                base = r.url
                for src in _extract_iframe_srcs(html):
                    inner = urljoin(base, src)
                    try:
                        r2 = session.get(inner, timeout=timeout, allow_redirects=True)
                        r2.raise_for_status()
                        if scan_chunk(r2.text or ""):
                            return {
                                "payment_status": "unpaid",
                                "payment_message": _STANDARD_MESSAGE,
                                "payment_check_source": "ras_http_iframe",
                            }
                    except Exception as e:
                        log.debug("ras iframe fetch failed %s: %s", inner, e)
            except Exception as e:
                log.debug("ras candidate fetch failed %s: %s", url, e)
                continue

        if _body_implies_unpaid(combined_lower):
            return {
                "payment_status": "unpaid",
                "payment_message": _STANDARD_MESSAGE,
                "payment_check_source": "ras_http",
            }
    except Exception as e:
        log.debug("ras report chain failed %s: %s", report_url, e)
        return {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_error": str(e),
            "payment_check_source": "ras_http",
        }

    return {"payment_status": "unknown", "payment_message": None, "payment_check_source": "ras_http"}


def _collect_playwright_page_text(page: Any) -> str:
    parts: list[str] = []
    try:
        parts.append(page.content() or "")
    except Exception:
        pass
    try:
        parts.append(page.inner_text("body") or "")
    except Exception:
        pass
    # Frames: Salesforce sometimes puts the banner in a child frame.
    try:
        for frame in page.frames:
            try:
                parts.append(frame.content() or "")
            except Exception:
                pass
            try:
                parts.append(frame.inner_text("body") or "")
            except Exception:
                pass
    except Exception:
        pass
    return "\n".join(parts).lower()


def _evaluate_playwright_case_page(page: Any, case_url: str, timeout_ms: int | None = None) -> dict[str, Any]:
    """
    Load the MLRS Lightning SPA and poll for the maintenance-fee banner.

    **Critical:** ``wait_until='networkidle'`` never completes on many Salesforce apps (long-lived
    connections), so navigation timed out and every claim stayed UNKNOWN. We use
    ``domcontentloaded`` and poll the DOM + all frames until the phrase appears or time runs out.
    """
    if timeout_ms is None:
        timeout_ms = _resolve_playwright_max_ms()
    nav_cap = min(60_000, max(15_000, timeout_ms))
    try:
        page.set_default_navigation_timeout(nav_cap)
        page.set_default_timeout(nav_cap)
        page.goto(case_url, wait_until="domcontentloaded", timeout=nav_cap)
    except Exception as e:
        log.warning("mlrs playwright goto failed %s: %s", case_url, e)
        return {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_error": f"navigation failed: {e}",
            "payment_check_source": "mlrs_case_playwright",
        }

    deadline = time.monotonic() + (timeout_ms / 1000.0)
    poll_interval_sec = 0.85
    last_combined = ""

    while time.monotonic() < deadline:
        try:
            last_combined = _collect_playwright_page_text(page)
            if _UNPAID_LOWER in last_combined:
                log.info("mlrs payment: unpaid (playwright) %s", case_url[:80])
                return {
                    "payment_status": "unpaid",
                    "payment_message": _STANDARD_MESSAGE,
                    "payment_check_source": "mlrs_case_playwright",
                }
            # Try Playwright text locator (handles shadow-ish timing better than raw string sometimes).
            try:
                loc = page.get_by_text(re.compile(r"maintenance\s+fee\s+payment\s+was\s+not\s+received", re.I))
                loc.first.wait_for(state="visible", timeout=1500)
                log.info("mlrs payment: unpaid (playwright locator) %s", case_url[:80])
                return {
                    "payment_status": "unpaid",
                    "payment_message": _STANDARD_MESSAGE,
                    "payment_check_source": "mlrs_case_playwright",
                }
            except Exception:
                pass
        except Exception as e:
            log.debug("mlrs playwright poll tick: %s", e)

        time.sleep(poll_interval_sec)

    # Final decision: banner absent after full wait => PAID (per product rule).
    combined = last_combined or _collect_playwright_page_text(page)
    if _UNPAID_LOWER in combined:
        return {
            "payment_status": "unpaid",
            "payment_message": _STANDARD_MESSAGE,
            "payment_check_source": "mlrs_case_playwright",
        }

    comb_l = combined.strip().lower()
    shellish = ("sorry to interrupt" in comb_l and "css error" in comb_l) or comb_l == "loading"
    if shellish:
        return {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_error": "mlrs case page did not finish loading (still shell or CSS error)",
            "payment_check_source": "mlrs_case_playwright",
        }

    log.info("mlrs payment: paid (no overdue banner after wait) %s", case_url[:80])
    return {
        "payment_status": "paid",
        "payment_message": None,
        "payment_check_source": "mlrs_case_playwright",
    }


def _payment_from_playwright(case_url: str, timeout_ms: int | None = None) -> dict[str, Any]:
    """Single-URL path (diagnostics): launch browser, one page, close."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_error": "playwright not installed",
            "payment_check_source": "mlrs_case_playwright",
        }

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                return {
                    "payment_status": "unknown",
                    "payment_message": None,
                    "payment_check_error": (
                        f"playwright chromium launch failed: {e}. "
                        "Run: python -m playwright install chromium"
                    ),
                    "payment_check_source": "mlrs_case_playwright",
                }

            context = browser.new_context(
                user_agent=_BROWSER_HEADERS["User-Agent"],
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            try:
                return _evaluate_playwright_case_page(page, case_url, timeout_ms=timeout_ms)
            finally:
                try:
                    context.close()
                finally:
                    browser.close()
    except Exception as e:
        log.warning("mlrs playwright failed for %s: %s", case_url, e)
        return {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_error": str(e),
            "payment_check_source": "mlrs_case_playwright",
        }


def _payment_from_selenium(case_url: str, timeout: int = 35) -> dict[str, Any]:
    """Fallback for environments that have Selenium + ChromeDriver but not Playwright."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        return {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_error": "selenium not installed",
            "payment_check_source": "mlrs_case_selenium",
        }

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(f"user-agent={_BROWSER_HEADERS['User-Agent']}")

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(timeout)
        driver.get(case_url)
        time.sleep(5)
        page_text = (driver.page_source or "").lower()
        if _UNPAID_LOWER in page_text:
            return {
                "payment_status": "unpaid",
                "payment_message": _STANDARD_MESSAGE,
                "payment_check_source": "mlrs_case_selenium",
            }
        return {
            "payment_status": "paid",
            "payment_message": None,
            "payment_check_source": "mlrs_case_selenium",
        }
    except Exception as e:
        log.warning("mlrs case selenium failed for %s: %s", case_url, e)
        return {
            "payment_status": "unknown",
            "payment_message": None,
            "payment_check_error": str(e),
            "payment_check_source": "mlrs_case_selenium",
        }
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def _close_playwright_batch(st: dict[str, Any]) -> None:
    """Tear down shared Playwright objects created by ``enrich_claims_from_mlrs_case_pages``."""
    browser = st.get("browser")
    pw = st.get("playwright")
    try:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
    finally:
        st.clear()


def enrich_claims_from_mlrs_case_pages(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich claims with MLRS payment status.

    Runs the actual scrape inside a fresh subprocess so each Playwright Chromium
    instance lives and dies in its own Python interpreter — avoiding the
    well-known ``sync_playwright`` thread-safety hang where the second run from a
    background thread blocks indefinitely on launch (the Node driver / greenlet
    state leaks across calls in a long-lived API process).

    The subprocess re-enters this module with ``MINING_OS_MLRS_ENRICH_INPROC=1``
    set, which short-circuits the wrapper and runs the in-process implementation.
    """
    if not claims:
        return claims
    if (os.getenv("MINING_OS_MLRS_ENRICH_INPROC") or "").strip() == "1":
        return _enrich_claims_inproc(claims)
    return _enrich_claims_subprocess(claims)


def _enrich_claims_subprocess(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run :func:`_enrich_claims_inproc` in an isolated child Python process.

    Uses :class:`subprocess.Popen` with explicit pipes and a daemon thread that
    drains stderr line-by-line into our logger. Inheriting stderr from a
    long-running uvicorn parent caused the subprocess to block once its pipe
    buffer (~64 KiB) filled with progress logs — the symptom was a job that
    appeared to hang forever even though the scrape worked fine when launched
    by hand.
    """
    import json
    import subprocess
    import sys
    import threading

    log.info(
        "mlrs payment enrich: dispatching %d claim row(s) to subprocess (Playwright isolation)",
        len(claims),
    )

    try:
        payload = json.dumps(claims).encode("utf-8")
    except (TypeError, ValueError) as e:
        log.warning("mlrs enrich: claims not JSON-serialisable, falling back in-process: %s", e)
        return _enrich_claims_inproc(claims)

    env = {**os.environ, "MINING_OS_MLRS_ENRICH_INPROC": "1", "PYTHONUNBUFFERED": "1"}
    cmd = [sys.executable, "-m", "mining_os.services.mlrs_case_payment"]

    try:
        proc = subprocess.Popen(  # noqa: S603 - command list is fully controlled
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except Exception as e:  # pragma: no cover - process spawn errors
        log.warning("mlrs enrich subprocess could not start (%s); falling back in-process", e)
        return _enrich_claims_inproc(claims)

    # Manual stdio orchestration: ``communicate()`` would race with our stderr
    # drainer thread (both would try to read the stderr pipe). Instead we run
    # one drainer per stream so the subprocess's pipe buffers never fill up.
    stdout_chunks: list[bytes] = []
    err_lock = threading.Lock()

    def _drain_stdout() -> None:
        try:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    return
                stdout_chunks.append(chunk)
        except Exception:  # pragma: no cover - drainer is best-effort
            pass

    def _drain_stderr() -> None:
        try:
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                with err_lock:
                    log.info("mlrs[sub] %s", line)
        except Exception:  # pragma: no cover - drainer is best-effort
            pass

    t_out = threading.Thread(target=_drain_stdout, name="mlrs-sub-stdout", daemon=True)
    t_err = threading.Thread(target=_drain_stderr, name="mlrs-sub-stderr", daemon=True)
    t_out.start()
    t_err.start()

    try:
        assert proc.stdin is not None
        proc.stdin.write(payload)
    except BrokenPipeError:
        log.warning("mlrs enrich subprocess closed stdin early")
    finally:
        try:
            assert proc.stdin is not None
            proc.stdin.close()
        except Exception:
            pass

    try:
        proc.wait(timeout=45 * 60)
    except subprocess.TimeoutExpired:
        log.warning("mlrs enrich subprocess exceeded 45-min cap; killing")
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        return claims

    t_out.join(timeout=5)
    t_err.join(timeout=5)

    if proc.returncode != 0:
        log.warning("mlrs enrich subprocess exit code %s; returning claims unchanged", proc.returncode)
        return claims

    out = b"".join(stdout_chunks)
    try:
        enriched = json.loads(out.decode("utf-8") or "[]")
    except Exception as e:
        log.warning("mlrs enrich subprocess produced invalid JSON: %s", e)
        return claims

    if not isinstance(enriched, list) or len(enriched) != len(claims):
        log.warning(
            "mlrs enrich subprocess returned unexpected shape (got %s rows, expected %d); discarding",
            type(enriched).__name__,
            len(claims),
        )
        return claims

    return enriched


def _enrich_claims_inproc(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """In-process implementation (used directly inside the subprocess)."""
    if not claims:
        return claims

    try_headless = _should_try_headless()
    log.info(
        "mlrs payment enrich: %d claim row(s), headless=%s "
        "(prod needs MINING_OS_MLRS_PAYMENT_HEADLESS=1 + playwright install chromium)",
        len(claims),
        try_headless,
    )
    pw_batch: dict[str, Any] = {}
    pw_launch_logged = False

    def _playwright_page_or_none():
        if pw_batch.get("launch_failed"):
            return None
        if pw_batch.get("page") is not None:
            return pw_batch["page"]
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            pw_batch["launch_failed"] = True
            pw_batch["launch_error"] = f"playwright not installed: {e}"
            return None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=_BROWSER_HEADERS["User-Agent"],
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            pw_batch.update(playwright=pw, browser=browser, context=context, page=page)
            log.info("mlrs payment: playwright chromium launched (shared across claims in this fetch)")
            return page
        except Exception as e:
            pw_batch["launch_failed"] = True
            pw_batch["launch_error"] = str(e)
            log.warning("mlrs payment: playwright chromium launch failed: %s", e)
            return None

    try:
        for i, c in enumerate(claims):
            if i > 0:
                time.sleep(0.12)
            if not isinstance(c, dict):
                continue
            url = c.get("case_page")
            if not url or not isinstance(url, str) or not url.strip().startswith("http"):
                continue

            prev = (c.get("payment_status") or "unknown").strip().lower()
            if prev in ("paid", "unpaid"):
                continue

            http_info = _payment_from_http(url.strip())
            _merge_payment_fields(c, http_info)

            if (c.get("payment_status") or "unknown").strip().lower() == "unpaid":
                log.debug("mlrs payment: unpaid via case HTTP %s", c.get("serial_number"))
                continue

            report_u = c.get("payment_report")
            if (
                (c.get("payment_status") or "unknown").strip().lower() == "unknown"
                and report_u
                and isinstance(report_u, str)
                and report_u.strip().startswith("http")
            ):
                ras_info = _payment_from_ras_http(
                    report_u.strip(),
                    serial_number=str(c.get("serial_number") or "") or None,
                )
                _merge_payment_fields(c, ras_info)

            if (c.get("payment_status") or "unknown").strip().lower() == "unpaid":
                log.debug("mlrs payment: unpaid via RAS %s", c.get("serial_number"))
                continue

            if try_headless and (c.get("payment_status") or "unknown").strip().lower() == "unknown":
                prefer_sel = (os.getenv("MINING_OS_MLRS_PAYMENT_PREFER_SELENIUM") or "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
                pw_ms = _resolve_playwright_max_ms()

                if prefer_sel:
                    sel_first = _payment_from_selenium(url.strip())
                    _merge_payment_fields(c, sel_first)

                if (c.get("payment_status") or "unknown").strip().lower() == "unknown":
                    pg = _playwright_page_or_none()
                    if pg is not None:
                        pw_info = _evaluate_playwright_case_page(pg, url.strip(), timeout_ms=pw_ms)
                        _merge_payment_fields(c, pw_info)
                    elif not pw_launch_logged and pw_batch.get("launch_error"):
                        pw_launch_logged = True
                        _merge_payment_fields(
                            c,
                            {
                                "payment_check_error": pw_batch.get("launch_error"),
                                "payment_check_source": "mlrs_case_playwright",
                            },
                        )

                if (
                    not prefer_sel
                    and (c.get("payment_status") or "unknown").strip().lower() == "unknown"
                ):
                    sel_info = _payment_from_selenium(url.strip())
                    _merge_payment_fields(c, sel_info)
                time.sleep(0.35)

        return claims
    finally:
        _close_playwright_batch(pw_batch)


def check_payment_for_url(case_url: str) -> dict[str, Any]:
    """
    Diagnostic: run the full enrichment chain on a single MLRS case URL and report which
    path resolved the status. Used by ``/api/diag/check-payment`` so you can verify a real
    claim without re-running Fetch Claim Records.
    """
    fake = {
        "case_page": case_url,
        "payment_report": None,
        "payment_status": "unknown",
    }
    enrich_claims_from_mlrs_case_pages([fake])
    return {
        "payment_status": fake.get("payment_status"),
        "payment_message": fake.get("payment_message"),
        "payment_check_source": fake.get("payment_check_source"),
        "payment_check_error": fake.get("payment_check_error"),
    }


def _subprocess_main() -> int:
    """Subprocess entry point: read claims JSON from stdin, write enriched JSON to stdout.

    Invoked by :func:`_enrich_claims_subprocess` via ``python -m mining_os.services.mlrs_case_payment``.
    Logs go to stderr so the parent process keeps streaming per-claim progress.
    """
    import json
    import sys

    try:
        from mining_os.logging_setup import setup_logging

        setup_logging("INFO", stream=sys.stderr)
    except Exception:  # pragma: no cover - logging setup is best-effort
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    raw = sys.stdin.buffer.read()
    if not raw:
        sys.stdout.write("[]")
        return 0
    try:
        claims = json.loads(raw)
    except Exception as e:
        log.error("mlrs enrich subprocess: invalid stdin JSON: %s", e)
        return 2

    if not isinstance(claims, list):
        log.error("mlrs enrich subprocess: stdin must be a JSON array")
        return 2

    enriched = _enrich_claims_inproc(claims)
    sys.stdout.write(json.dumps(enriched))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_subprocess_main())
