"""HTML discovery adapters for Trust Lands hub / indexes (stdlib parser)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

import requests

from mining_os.sitla_intel.adapters.base import RawSitlaRecord, SourceArtifact, SitlaSourceAdapter

USER_AGENT = "MiningOS-SitlaIntel/1.0 (+https://github.com/local/Mining_OS)"


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self.links.append((self._href, text))
            self._href = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._buf.append(data)


class HtmlHubAdapter(SitlaSourceAdapter):
    """
    Fetch a Trust Lands listing page and emit link records for downstream review.
    Live fetch is opt-in via configuration_json.allow_live_html=true.
    """

    def discover(self) -> list[str]:
        url = self.config.get("listing_url") or self.source.get("listing_url")
        return [str(url)] if url else []

    def fetch(self, url: str) -> SourceArtifact:
        now = datetime.now(timezone.utc)
        r = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return SourceArtifact(
            source_url=str(r.url),
            retrieved_at=now,
            media_type=r.headers.get("Content-Type", "text/html"),
            content=r.content,
            filename="listing.html",
            metadata={"final_url": str(r.url)},
        )

    def parse(self, artifact: SourceArtifact) -> Iterable[RawSitlaRecord]:
        parser = _LinkParser()
        parser.feed(artifact.content.decode("utf-8", errors="replace"))
        keywords = [k.lower() for k in (self.config.get("link_keywords") or [
            "auction", "mineral", "lease", "notice", "permit", "nomination", "bid", "offering"
        ])]
        seen: set[str] = set()
        out: list[RawSitlaRecord] = []
        for href, text in parser.links:
            abs_url = urljoin(artifact.source_url, href)
            blob = f"{text} {abs_url}".lower()
            if not any(k in blob for k in keywords):
                continue
            if abs_url in seen:
                continue
            seen.add(abs_url)
            key = re.sub(r"[^a-zA-Z0-9]+", "-", abs_url)[-80:] or f"link-{len(out)+1}"
            status = "ANNOUNCED"
            otype = "UNKNOWN"
            if "past-auction" in abs_url or "result" in blob:
                status = "ARCHIVED"
                otype = "COMPETITIVE_MINERAL_LEASE"
            elif "public-notice" in abs_url or "competing" in blob:
                status = "PUBLIC NOTICE"
                otype = "COMPETING_APPLICATION_NOTICE"
            elif "auction" in blob or "lease" in blob:
                status = "SCHEDULED"
                otype = "COMPETITIVE_MINERAL_LEASE"
            elif "gravel" in blob or "material" in blob:
                otype = "MINERAL_MATERIAL_PERMIT"
                status = "PUBLIC NOTICE"
            out.append(
                RawSitlaRecord(
                    source_record_key=key,
                    title=text or abs_url,
                    opportunity_type_raw=otype,
                    status_raw=status,
                    county_name=None,
                    detail_url=abs_url,
                    raw_payload={"href": abs_url, "anchor_text": text, "discovery": True},
                )
            )
            if len(out) >= int(self.config.get("max_records") or 40):
                break
        return out
