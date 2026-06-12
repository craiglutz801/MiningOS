#!/usr/bin/env python3
"""
Convert USGS USMIN state KMZ exports (data_files/usmin-<ST>.kmz) into compact,
gzipped point datasets that the API can serve to the map as a viewport-filtered,
clickable vector overlay.

Why this exists
---------------
The raw KML is huge (Nevada alone is ~70 MB / ~124k placemarks). We cannot ship
that to the browser. Instead we extract only what the map needs per point and
store it as a tight JSON array, gzipped, one file per state:

    data_files/usmin_points/<ST>.json.gz

Record shape (arrays, not objects, to save space):
    [lon, lat, type, name, usmin_id]
      lon, lat : float (WGS84)
      type     : feature type from <Snippet> (e.g. "Adit", "Mine Shaft")
      name     : <name> text (often "Mine" or a proper name)
      usmin_id : USGS USMIN point id parsed from the description link ("" if none)

A small companion index (usmin_points/index.json) records per-state counts and
bounding boxes so the API/UI can describe coverage without opening each file.

Both Point and Polygon placemarks are converted. Polygons (open-pit mines,
tailings ponds, quarries, gravel pits, etc.) are reduced to the centroid of
their outer ring so they render as a single clickable point alongside the
point features. Polygons whose USGS id already appears as a point are skipped
so a feature mapped as both is not duplicated. Run from the repo root:

    .venv/bin/python scripts/convert_usmin_kmz.py
"""
from __future__ import annotations

import gzip
import json
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data_files"
OUT_DIR = DATA_DIR / "usmin_points"

STATES = ["ID", "NV", "UT", "WY"]

# Each <Placemark>…</Placemark> block (only those with a <Point> are kept).
_PLACEMARK_RE = re.compile(r"<Placemark\b.*?</Placemark>", re.DOTALL)
_NAME_RE = re.compile(r"<name>(.*?)</name>", re.DOTALL)
_SNIPPET_RE = re.compile(r"<Snippet[^>]*>(.*?)</Snippet>", re.DOTALL)
_DESC_RE = re.compile(r"<description>(.*?)</description>", re.DOTALL)
_COORD_RE = re.compile(r"<coordinates>([^<]+)</coordinates>")
# First <coordinates> inside a <Polygon> = the outer boundary ring.
_POLY_COORD_RE = re.compile(r"<Polygon>.*?<coordinates>([^<]+)</coordinates>", re.DOTALL)
# USMIN detail link carries the point id: ...show-usmin.php?type=point&amp;id=459900
# (the ampersand is HTML-encoded in the raw KML, so match "id=" directly).
_ID_RE = re.compile(r"\bid=(\d+)")
# Description text reads like "Mine: Adit<table>…" or "(unnamed): Adit<table>…"
_DESC_TYPE_RE = re.compile(r":\s*([^<:]+?)\s*<table", re.IGNORECASE)


def _read_kml_text(kmz_path: Path) -> str:
    with zipfile.ZipFile(kmz_path) as z:
        kml_names = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError(f"No .kml inside {kmz_path.name}")
        return z.read(kml_names[0]).decode("utf-8", "replace")


def _clean(s: str | None) -> str:
    return (s or "").strip()


def _extract_fields(block: str) -> tuple[str, str, str] | None:
    """Return (feature_type, name, usmin_id) for a Point placemark, or None."""
    m_name = _NAME_RE.search(block)
    name_raw = _clean(m_name.group(1)) if m_name else ""

    m_snip = _SNIPPET_RE.search(block)
    snippet = _clean(m_snip.group(1)) if m_snip else ""

    desc = _DESC_RE.search(block)
    desc_text = desc.group(1) if desc else ""
    m_id = _ID_RE.search(desc_text)
    usmin_id = m_id.group(1) if m_id else ""

    # Feature type: prefer <Snippet>; else the "(Adit)" parenthetical name used
    # for unnamed features; else parse it out of the description text.
    ftype = snippet
    if not ftype and name_raw.startswith("(") and name_raw.endswith(")"):
        ftype = name_raw[1:-1].strip()
    if not ftype:
        m_dt = _DESC_TYPE_RE.search(desc_text)
        if m_dt:
            ftype = _clean(m_dt.group(1))

    # Display name: drop the parenthetical placeholder used for unnamed features.
    name = "" if (name_raw.startswith("(") and name_raw.endswith(")")) else name_raw
    return ftype, name, usmin_id


def _ring_centroid(coord_text: str) -> tuple[float, float] | None:
    """Average of an outer-ring's vertices (drops the duplicated closing point)."""
    verts: list[tuple[float, float]] = []
    for tok in coord_text.split():
        parts = tok.split(",")
        if len(parts) >= 2:
            try:
                verts.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    if not verts:
        return None
    if len(verts) > 1 and verts[0] == verts[-1]:
        verts = verts[:-1]
    n = len(verts)
    lon = round(sum(v[0] for v in verts) / n, 6)
    lat = round(sum(v[1] for v in verts) / n, 6)
    return lon, lat


def convert_state(state: str) -> dict | None:
    kmz_path = DATA_DIR / f"usmin-{state}.kmz"
    if not kmz_path.exists():
        print(f"  ! {kmz_path.name} not found — skipping")
        return None

    text = _read_kml_text(kmz_path)
    point_records: list[list] = []
    poly_records: list[list] = []
    point_ids: set[str] = set()

    for block in _PLACEMARK_RE.finditer(text):
        b = block.group(0)
        ftype, name, usmin_id = _extract_fields(b)

        if "<Point>" in b:
            m_coord = _COORD_RE.search(b)
            if not m_coord:
                continue
            raw = m_coord.group(1).strip().split(",")
            if len(raw) < 2:
                continue
            try:
                lon = round(float(raw[0]), 6)
                lat = round(float(raw[1]), 6)
            except ValueError:
                continue
            point_records.append([lon, lat, ftype, name, usmin_id])
            if usmin_id:
                point_ids.add(usmin_id)
        elif "<Polygon>" in b:
            m_poly = _POLY_COORD_RE.search(b)
            if not m_poly:
                continue
            centroid = _ring_centroid(m_poly.group(1))
            if centroid is None:
                continue
            poly_records.append([centroid[0], centroid[1], ftype, name, usmin_id])

    # Merge: keep all points; add polygon centroids unless that id is already a
    # point (avoids duplicating a feature mapped as both point and polygon).
    records = list(point_records)
    added_poly = 0
    for rec in poly_records:
        usmin_id = rec[4]
        if usmin_id and usmin_id in point_ids:
            continue
        records.append(rec)
        added_poly += 1

    min_lon = min_lat = 1e9
    max_lon = max_lat = -1e9
    for lon, lat, *_ in records:
        min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
        min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{state}.json.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(records, f, separators=(",", ":"))

    size_mb = out_path.stat().st_size / 1e6
    bbox = [min_lon, min_lat, max_lon, max_lat] if records else None
    print(
        f"  {state}: {len(point_records):>7d} points + {added_poly:>5d} polygon "
        f"centroids = {len(records):>7d} -> {out_path.name} ({size_mb:.1f} MB gz)"
    )
    return {"state": state, "count": len(records), "bbox": bbox, "file": f"{state}.json.gz"}


def main() -> int:
    print(f"Converting USMIN KMZ -> {OUT_DIR.relative_to(REPO_ROOT)}")
    index = []
    for st in STATES:
        info = convert_state(st)
        if info:
            index.append(info)
    if index:
        (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        total = sum(i["count"] for i in index)
        print(f"Done. {total} points across {len(index)} states. Wrote index.json")
    else:
        print("No states converted.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
