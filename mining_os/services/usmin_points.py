"""
Serve local USGS USMIN mine-feature points (converted from the state KMZ files)
to the map as a viewport-filtered, clickable vector layer.

Data is produced by ``scripts/convert_usmin_kmz.py`` into
``data_files/usmin_points/<ST>.json.gz`` plus an ``index.json`` describing the
per-state counts and bounding boxes. Each state file is a tight JSON array of
records ``[lon, lat, type, name, usmin_id]``.

The whole dataset is small once converted (~1.4 MB gzipped for ID/NV/UT/WY), so
we lazily load each state into memory on first use and keep it cached. Queries
filter by bounding box and cap the number of returned points so the browser
never receives more than it can cluster smoothly.
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("mining_os.usmin_points")

_DATA_DIR = Path(__file__).resolve().parents[2] / "data_files" / "usmin_points"

# Hard ceiling regardless of caller-supplied limit, to protect the browser.
MAX_LIMIT = 6000
DEFAULT_LIMIT = 3000

_lock = threading.Lock()
_state_cache: dict[str, list[list]] = {}
_index_cache: list[dict[str, Any]] | None = None


def _load_index() -> list[dict[str, Any]]:
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    path = _DATA_DIR / "index.json"
    if not path.exists():
        log.warning("USMIN index.json not found at %s", path)
        _index_cache = []
        return _index_cache
    try:
        _index_cache = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to read USMIN index.json: %s", e)
        _index_cache = []
    return _index_cache


def _load_state(state: str) -> list[list]:
    state = state.upper()
    cached = _state_cache.get(state)
    if cached is not None:
        return cached
    with _lock:
        cached = _state_cache.get(state)
        if cached is not None:
            return cached
        path = _DATA_DIR / f"{state}.json.gz"
        if not path.exists():
            _state_cache[state] = []
            return []
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to load USMIN points for %s: %s", state, e)
            data = []
        _state_cache[state] = data
        log.info("Loaded %d USMIN points for %s", len(data), state)
        return data


def _bbox_intersects(a: list[float], west: float, south: float, east: float, north: float) -> bool:
    """``a`` is [min_lon, min_lat, max_lon, max_lat]."""
    if not a or len(a) < 4:
        return True  # unknown bbox -> don't exclude
    return not (a[2] < west or a[0] > east or a[3] < south or a[1] > north)


def coverage() -> list[dict[str, Any]]:
    """Per-state counts and bounding boxes (no point data)."""
    return _load_index()


def query_points(
    west: float,
    south: float,
    east: float,
    north: float,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Return USMIN points inside the bbox.

    Output: ``{"points": [[lon, lat, type, name, id], ...], "count": n,
    "capped": bool}``. Only states whose bbox intersects the query are scanned.
    """
    try:
        limit = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    index = _load_index()
    states = [
        entry["state"]
        for entry in index
        if _bbox_intersects(entry.get("bbox") or [], west, south, east, north)
    ] or [entry["state"] for entry in index]

    out: list[list] = []
    capped = False
    for state in states:
        for rec in _load_state(state):
            lon, lat = rec[0], rec[1]
            if west <= lon <= east and south <= lat <= north:
                out.append(rec)
                if len(out) >= limit:
                    capped = True
                    break
        if capped:
            break

    return {"points": out, "count": len(out), "capped": capped}
