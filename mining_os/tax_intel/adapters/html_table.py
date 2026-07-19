"""Simple HTML table adapter using stdlib html.parser."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

import requests

from mining_os.tax_intel.adapters.base import RawTaxRecord, SourceArtifact, TaxSourceAdapter
from mining_os.tax_intel.adapters.csv_adapter import USER_AGENT, _float, _parse_date


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cur_table: list[list[str]] = []
        self._cur_row: list[str] = []
        self._cell_buf: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "table":
            self._in_table = True
            self._cur_table = []
        elif self._in_table and t == "tr":
            self._in_row = True
            self._cur_row = []
        elif self._in_row and t in ("td", "th"):
            self._in_cell = True
            self._cell_buf = []
        elif t == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._cur_row.append(re.sub(r"\s+", " ", "".join(self._cell_buf)).strip())
        elif t == "tr" and self._in_row:
            self._in_row = False
            if self._cur_row:
                self._cur_table.append(self._cur_row)
        elif t == "table" and self._in_table:
            self._in_table = False
            if self._cur_table:
                self.tables.append(self._cur_table)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)


class HtmlTableAdapter(TaxSourceAdapter):
    """Fetch a public HTML page and parse the largest table into records."""

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

    def parse(self, artifact: SourceArtifact) -> Iterable[RawTaxRecord]:
        parser = _TableParser()
        parser.feed(artifact.content.decode("utf-8", errors="replace"))
        if not parser.tables:
            return []
        table = max(parser.tables, key=lambda t: len(t))
        if len(table) < 2:
            return []
        headers = [h.lower().strip() for h in table[0]]
        col_map = self.config.get("column_map") or {}
        state = str(self.source.get("state") or "")
        county = str(self.source.get("county_name") or "")

        def cell(row: list[str], *names: str) -> str | None:
            for name in names:
                mapped = col_map.get(name, name).lower()
                if mapped in headers:
                    idx = headers.index(mapped)
                    if idx < len(row) and row[idx].strip():
                        return row[idx].strip()
                for i, h in enumerate(headers):
                    if name in h and i < len(row) and row[i].strip():
                        return row[i].strip()
            return None

        out: list[RawTaxRecord] = []
        for i, row in enumerate(table[1:], start=1):
            apn = cell(row, "apn", "parcel", "parcel id", "account")
            if not apn and len(row) < 2:
                continue
            key = cell(row, "id", "case") or apn or f"row-{i}"
            out.append(
                RawTaxRecord(
                    source_record_key=str(key),
                    state=state,
                    county_name=county,
                    apn_raw=apn,
                    owner_raw=cell(row, "owner", "taxpayer", "name"),
                    legal_description_raw=cell(row, "legal", "description", "legal description"),
                    raw_status=cell(row, "status", "stage"),
                    amount_due=_float(cell(row, "amount", "amount due", "taxes due", "due")),
                    minimum_bid=_float(cell(row, "minimum bid", "min bid", "bid")),
                    sale_date=_parse_date(cell(row, "sale date", "auction date", "date")),
                    property_address=cell(row, "address", "situs", "location"),
                    best_name=cell(row, "property", "claim", "name") or apn,
                    raw_payload={"headers": headers, "row": row},
                )
            )
        # CivicPlus-style: also surface PDF links for manual review metadata
        pdf_links = [
            urljoin(artifact.source_url, href)
            for href in parser.links
            if href.lower().endswith(".pdf")
        ]
        if pdf_links and not out:
            for i, link in enumerate(pdf_links[:20]):
                out.append(
                    RawTaxRecord(
                        source_record_key=f"pdf-{i+1}",
                        state=state,
                        county_name=county,
                        best_name=f"Document listing {i+1}",
                        legal_description_raw=link,
                        raw_status="NOTICE",
                        raw_payload={"pdf_url": link, "needs_manual_parse": True},
                    )
                )
        return out
