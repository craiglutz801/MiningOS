"""File downloads, immutable raw-cache management, and MSHA portal discovery."""

from __future__ import annotations

import io
import random
import time
import zipfile
from pathlib import Path
from typing import Callable

import requests
from bs4 import BeautifulSoup

from mining_os.active_mine_intel.matcher.logging_setup import get_logger
from mining_os.active_mine_intel.matcher.models import SourceUnavailableError
from mining_os.active_mine_intel.matcher.utilities import file_age_hours, timestamp_token

log = get_logger("mcm.download")

USER_AGENT = "MineClaimMatcher/0.1 (research tool; local use)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
DATA_EXTENSIONS = (".zip", ".csv", ".txt", ".tsv")


def download_bytes(
    url: str,
    session: requests.Session | None = None,
    max_retries: int = 3,
    timeout: tuple[float, float] = (10.0, 300.0),
) -> bytes:
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(min(2**attempt + random.uniform(0, 1), 30.0))
            continue
        if response.status_code in RETRYABLE_STATUS:
            last_error = SourceUnavailableError(f"HTTP {response.status_code} from {url}")
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.replace(".", "", 1).isdigit():
                time.sleep(min(float(retry_after), 60.0))
            else:
                time.sleep(min(2**attempt + random.uniform(0, 1), 30.0))
            continue
        if response.status_code != 200:
            raise SourceUnavailableError(f"HTTP {response.status_code} from {url}")
        return response.content
    raise SourceUnavailableError(f"Failed to download {url}: {last_error}")


def extract_tabular_bytes(raw: bytes, name_hint: str = "") -> bytes:
    """If the payload is a ZIP, return the largest tabular member; else return as-is."""
    if raw[:2] != b"PK":
        return raw
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [
                m
                for m in archive.infolist()
                if not m.is_dir()
                and m.filename.lower().endswith((".csv", ".txt", ".tsv", ".dat"))
            ]
            if not members:
                members = [m for m in archive.infolist() if not m.is_dir()]
            if not members:
                raise SourceUnavailableError(f"ZIP archive {name_hint} contains no files")
            best = max(members, key=lambda m: m.file_size)
            log.info("Extracted %s from ZIP %s", best.filename, name_hint)
            return archive.read(best)
    except zipfile.BadZipFile as exc:
        raise SourceUnavailableError(f"Corrupt ZIP payload from {name_hint}") from exc


def discover_msha_url(
    portal_urls: tuple[str, ...],
    keywords: dict[str, list[str]] | list[str],
    session: requests.Session | None = None,
) -> str | None:
    """Parse the MSHA open-data portal pages and locate a likely dataset link.

    ``keywords`` may be a plain prefer-list or a dict with "prefer" and "avoid"
    lists. Avoid keywords steer away from similarly named datasets (e.g.
    MinesProdYearly.zip when the plain Mines.zip identity dataset is wanted).
    """
    if isinstance(keywords, dict):
        prefer = keywords.get("prefer", [])
        avoid = keywords.get("avoid", [])
    else:
        prefer, avoid = keywords, []
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    for portal in portal_urls:
        try:
            html = download_bytes(portal, session=session, max_retries=1)
        except SourceUnavailableError as exc:
            log.warning("MSHA portal %s unavailable: %s", portal, exc)
            continue
        soup = BeautifulSoup(html, "html.parser")
        best_url: str | None = None
        best_score = 0.0
        for anchor in soup.find_all("a", href=True):
            href: str = anchor["href"]
            text = (anchor.get_text() or "").lower()
            haystack = (href + " " + text).lower()
            if "definition" in haystack:
                continue
            score = sum(2.0 for kw in prefer if kw.lower() in haystack)
            if score == 0:
                continue
            score -= sum(2.5 for kw in avoid if kw.lower() in haystack)
            if href.lower().endswith(DATA_EXTENSIONS):
                score += 3
            # Prefer shorter, exact-looking filenames over compound datasets.
            filename = href.rsplit("/", 1)[-1].lower()
            if any(filename.startswith(kw.lower()) for kw in prefer):
                score += 2
            if score > best_score:
                best_score = score
                best_url = requests.compat.urljoin(portal, href)
        if best_url:
            log.info(
                "MSHA discovery selected %s (score %.1f) for prefer=%s",
                best_url,
                best_score,
                prefer,
            )
            return best_url
    return None


def find_manual_file(manual_dir: Path, keywords: list[str]) -> Path | None:
    """Locate a manually supplied fallback file whose name contains any keyword."""
    if not manual_dir.exists():
        return None
    for path in sorted(manual_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name.lower()
        if any(kw.lower() in name for kw in keywords):
            return path
    return None


class CacheManager:
    """Immutable timestamped raw artifacts plus a `latest` copy per source key.

    Never overwrites a good raw file with an error payload: new content is only
    written after a successful fetch.
    """

    def __init__(self, raw_dir: Path, default_ttl_hours: float = 24.0) -> None:
        self.raw_dir = raw_dir
        self.default_ttl_hours = default_ttl_hours

    def latest_path(self, key: str, suffix: str) -> Path:
        return self.raw_dir / f"{key}_latest{suffix}"

    def cache_age_hours(self, key: str, suffix: str) -> float:
        return file_age_hours(self.latest_path(key, suffix))

    def save(self, key: str, suffix: str, content: bytes) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        stamped = self.raw_dir / f"{key}_{timestamp_token()}{suffix}"
        stamped.write_bytes(content)
        latest = self.latest_path(key, suffix)
        latest.write_bytes(content)
        return latest

    def get(
        self,
        key: str,
        suffix: str,
        fetch: Callable[[], bytes],
        ttl_hours: float | None = None,
        use_cache_only: bool = False,
        refresh: bool = True,
    ) -> tuple[bytes, dict]:
        """Return (content, info). info: {cache_used, cache_age_hours, refreshed, stale}."""
        ttl = self.default_ttl_hours if ttl_hours is None else ttl_hours
        latest = self.latest_path(key, suffix)
        age = file_age_hours(latest)
        info = {"cache_used": False, "cache_age_hours": None, "refreshed": False, "stale": False}

        if latest.exists() and (use_cache_only or (not refresh) or age <= ttl):
            info.update({"cache_used": True, "cache_age_hours": round(age, 2)})
            log.info("Using cached %s%s (age %.1f h)", key, suffix, age)
            return latest.read_bytes(), info

        if use_cache_only:
            raise SourceUnavailableError(
                f"--use-cache requested but no cached file exists for {key}{suffix}"
            )

        try:
            content = fetch()
        except Exception as exc:
            if latest.exists():
                log.warning(
                    "Refresh of %s failed (%s); falling back to stale cache (age %.1f h)",
                    key,
                    exc,
                    age,
                )
                info.update(
                    {"cache_used": True, "cache_age_hours": round(age, 2), "stale": True}
                )
                return latest.read_bytes(), info
            raise
        self.save(key, suffix, content)
        info["refreshed"] = True
        return content, info
