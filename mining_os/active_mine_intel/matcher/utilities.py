"""Small shared helpers: time, JSON, caching filenames, delimiter sniffing."""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_token() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def new_run_id() -> str:
    return f"{timestamp_token()}-{uuid.uuid4().hex[:8]}"


def file_age_hours(path: Path) -> float:
    if not path.exists():
        return float("inf")
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (utc_now() - mtime).total_seconds() / 3600.0


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_json(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), default=str)


def sniff_delimiter(sample_text: str, default: str = ",") -> str:
    """Detect the delimiter of a text table with a safe fallback."""
    try:
        dialect = csv.Sniffer().sniff(sample_text[:8192], delimiters=",|\t;")
        return dialect.delimiter
    except csv.Error:
        counts = {d: sample_text[:8192].count(d) for d in ("|", "\t", ",", ";")}
        best = max(counts, key=counts.get)  # type: ignore[arg-type]
        return best if counts[best] > 0 else default


def parse_years(value: Any) -> list[int]:
    """Extract plausible calendar years from lists, strings, or numbers."""
    years: set[int] = set()
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            years.update(parse_years(item))
        return sorted(years)
    if isinstance(value, (int, float)):
        year = int(value)
        if 1800 <= year <= 2100:
            years.add(year)
        return sorted(years)
    text = str(value)
    for token in re.findall(r"(?<!\d)(18\d{2}|19\d{2}|20\d{2}|21\d{2})(?!\d)", text):
        years.add(int(token))
    return sorted(years)


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def first_non_null(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, float) and value != value:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def read_text_guessing_encoding(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def dataframe_from_delimited(raw: bytes):
    """Parse delimited text bytes into a pandas DataFrame with delimiter sniffing."""
    import pandas as pd

    text = read_text_guessing_encoding(raw)
    delimiter = sniff_delimiter(text)
    return pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, low_memory=False)
