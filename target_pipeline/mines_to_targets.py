#!/usr/bin/env python3
"""
Pull USGS MRDS mine sites for one or more US states, group them by PLSS section,
and (optionally) upsert one Target per section into ``areas_of_focus``.

Run from repo root:

    # 1) Always start with a dry-run to preview counts + sample rows
    python -m target_pipeline.mines_to_targets --states UT --dry-run

    # 2) Real run against whatever DATABASE_URL is in the environment
    DATABASE_URL='postgresql://...' python -m target_pipeline.mines_to_targets --states UT

Concept:
  * MRDS service (USGS, ArcGIS) returns mine points by bbox (no STATE field).
  * We page the bbox, drop "Plant" sites (and other non-mineral DEV_STATs).
  * Each point is reverse-geocoded to a PLSS section via BLM Cadastral
    (results cached on disk so reruns are essentially free).
  * Points are grouped by (state, township, range, section).
  * For each section with >= 1 mine, we build a Target row whose
    ``name`` is "<MineName1> / <MineName2> / ... (known mine)" (truncated at 500 chars),
    ``minerals`` is the union of MRDS commodity tokens (may be empty),
    and ``source`` is ``mrds_auto`` so the entire batch can be reverted with::
        DELETE FROM areas_of_focus WHERE source = 'mrds_auto';

Safe to re-run: ``upsert_area`` enforces uniqueness on ``plss_normalized`` and
merges minerals / report_links on conflict.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

log = logging.getLogger("mines_to_targets")

MRDS_QUERY_URL = (
    "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
    "Mineral_Resources_Data_System_MRDS_Compact_Version/FeatureServer/0/query"
)
MRDS_PAGE_SIZE = 2000  # service maxRecordCount

# State bounding boxes (W, S, E, N) — slightly padded; we still need the
# reverse-geocode to land inside the actual state PLSS to be kept.
STATE_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "UT": (-114.10, 36.95, -109.00, 42.05),
    "NV": (-120.05, 34.95, -114.00, 42.05),
    "ID": (-117.30, 41.95, -111.00, 49.05),
}

# DEV_STAT values to exclude. MRDS is mostly mineral occurrences/prospects/producers;
# "Plant" denotes processing facilities (smelters, mills) without a mineral pick,
# so they're not real "mines on the ground".
EXCLUDE_DEV_STAT = {"plant", "processing plant", "processing facility"}

# Cache reverse-geocode results to a half-km grid so multiple MRDS points falling
# inside the same PLSS section share one BLM Cadastral call.
GEOCODE_GRID_DEGREES = 0.005

CACHE_DIR = _REPO_ROOT / "target_pipeline" / ".cache"
DRY_RUN_OUT_DIR = _REPO_ROOT / "target_pipeline" / "data" / "mines_to_targets"
RAW_MRDS_DIR = DRY_RUN_OUT_DIR / "raw"


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MinePoint:
    dep_id: str
    name: str
    dev_stat: str
    commodities: list[str]
    url: str
    grade: str
    longitude: float
    latitude: float


@dataclass
class SectionGroup:
    state_abbr: str
    meridian: str
    township: str  # encoded form, e.g. "0120S"
    range_: str  # encoded form, e.g. "0160E"
    section: str  # human form, e.g. "5"
    location_plss: str  # canonical "UT T12S R16E Sec 5"
    mines: list[MinePoint] = field(default_factory=list)

    def key(self) -> tuple[str, str, str, str, str]:
        return (self.state_abbr, self.meridian, self.township, self.range_, self.section)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: pull MRDS
# ─────────────────────────────────────────────────────────────────────────────


def _split_commodities(code_list: str) -> list[str]:
    """Split MRDS CODE_LIST into individual commodity tokens.

    MRDS uses whitespace, commas, semicolons, slashes, and pipes as separators
    (e.g. "Au Ag Cu Pb Zn"). We split on all of them; downstream
    `_normalize_minerals` (in mining_os.services.areas_of_focus) maps each
    code to its full canonical name (Au -> Gold, etc.).
    """
    if not code_list:
        return []
    parts = re.split(r"[\s,;|/]+", code_list)
    out: list[str] = []
    for p in parts:
        s = p.strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def fetch_mrds_for_bbox(bbox: tuple[float, float, float, float]) -> list[MinePoint]:
    """Return all MRDS mine points inside ``bbox`` (W, S, E, N), paged."""
    west, south, east, north = bbox
    out: list[MinePoint] = []
    offset = 0
    while True:
        params = {
            "f": "geojson",
            "where": "1=1",
            "geometry": f"{west},{south},{east},{north}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "DEP_ID,SITE_NAME,DEV_STAT,CODE_LIST,Grade,URL",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultRecordCount": str(MRDS_PAGE_SIZE),
            "resultOffset": str(offset),
        }
        try:
            resp = requests.get(MRDS_QUERY_URL, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # network / parse / 500
            log.warning("MRDS page offset=%d failed: %s — backing off and retrying once", offset, e)
            time.sleep(2.0)
            try:
                resp = requests.get(MRDS_QUERY_URL, params=params, timeout=60)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e2:
                log.error("MRDS page offset=%d failed twice: %s — giving up on this page", offset, e2)
                break
        feats = data.get("features") or []
        if not feats:
            break
        for f in feats:
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            props = f.get("properties") or {}
            out.append(
                MinePoint(
                    dep_id=str(props.get("DEP_ID") or "").strip(),
                    name=(props.get("SITE_NAME") or "").strip() or "Unnamed site",
                    dev_stat=(props.get("DEV_STAT") or "").strip(),
                    commodities=_split_commodities(props.get("CODE_LIST") or ""),
                    url=(props.get("URL") or "").strip(),
                    grade=(props.get("Grade") or "").strip(),
                    longitude=lon,
                    latitude=lat,
                )
            )
        log.info("MRDS pulled offset=%d → %d cumulative", offset, len(out))
        if len(feats) < MRDS_PAGE_SIZE:
            break
        offset += MRDS_PAGE_SIZE
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: PLSS reverse-geocode (cached)
# ─────────────────────────────────────────────────────────────────────────────


class PlssReverseCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any] | None] = {}
        self._dirty = 0
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("cache read failed (%s) — starting empty", e)
                self._data = {}
        log.info("PLSS cache: %d entries loaded from %s", len(self._data), path)

    @staticmethod
    def cell_key(lat: float, lon: float) -> str:
        glat = round(lat / GEOCODE_GRID_DEGREES) * GEOCODE_GRID_DEGREES
        glon = round(lon / GEOCODE_GRID_DEGREES) * GEOCODE_GRID_DEGREES
        return f"{glat:.4f},{glon:.4f}"

    def get(self, lat: float, lon: float) -> tuple[bool, dict[str, Any] | None]:
        k = self.cell_key(lat, lon)
        if k in self._data:
            return True, self._data[k]
        return False, None

    def set(self, lat: float, lon: float, value: dict[str, Any] | None) -> None:
        k = self.cell_key(lat, lon)
        self._data[k] = value
        self._dirty += 1
        if self._dirty >= 50:
            self.flush()

    def flush(self) -> None:
        if self._dirty == 0:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = 0


def reverse_geocode_points(
    points: list[MinePoint],
    cache: PlssReverseCache,
    pause_seconds: float = 0.25,
) -> dict[int, dict[str, Any] | None]:
    """Map idx→PLSS dict (or None when BLM can't resolve / is outside PLSS)."""
    from mining_os.services.plss_geocode import reverse_geocode_plss

    out: dict[int, dict[str, Any] | None] = {}
    api_calls = 0
    for i, p in enumerate(points):
        hit, val = cache.get(p.latitude, p.longitude)
        if hit:
            out[i] = val
            continue
        try:
            val = reverse_geocode_plss(p.latitude, p.longitude)
        except Exception as e:
            log.debug("reverse-geocode failed @ %.5f,%.5f: %s", p.latitude, p.longitude, e)
            val = None
        cache.set(p.latitude, p.longitude, val)
        out[i] = val
        api_calls += 1
        time.sleep(pause_seconds)
        if api_calls % 100 == 0:
            cache.flush()
            log.info("reverse-geocoded %d points (%d api calls so far)", i + 1, api_calls)
    cache.flush()
    log.info("reverse-geocode done: %d points / %d api calls", len(points), api_calls)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: group + build targets
# ─────────────────────────────────────────────────────────────────────────────


def _human_tr(encoded: str) -> str:
    """'0120S' -> '12S' for display in location_plss."""
    m = re.match(r"^(\d+)([NSEW])$", (encoded or "").strip().upper())
    if not m:
        return encoded
    return f"{int(m.group(1)) // 10}{m.group(2)}"


def group_by_section(
    points: list[MinePoint],
    geocodes: dict[int, dict[str, Any] | None],
    target_state: str,
) -> dict[tuple[str, str, str, str, str], SectionGroup]:
    groups: dict[tuple[str, str, str, str, str], SectionGroup] = {}
    skipped_no_plss = 0
    skipped_wrong_state = 0
    for i, p in enumerate(points):
        info = geocodes.get(i)
        if not info or not info.get("township") or not info.get("range") or not info.get("section"):
            skipped_no_plss += 1
            continue
        state_abbr = (info.get("state_abbr") or "").upper()
        if target_state and state_abbr != target_state:
            skipped_wrong_state += 1
            continue
        twp = info["township"]
        rng = info["range"]
        sec = str(info["section"])
        meridian = info.get("meridian") or ""
        location_plss = info.get("location_plss") or (
            f"{state_abbr} T{_human_tr(twp)} R{_human_tr(rng)} Sec {sec}"
        )
        key = (state_abbr, meridian, twp, rng, sec)
        g = groups.get(key)
        if g is None:
            g = SectionGroup(
                state_abbr=state_abbr,
                meridian=meridian,
                township=twp,
                range_=rng,
                section=sec,
                location_plss=location_plss,
            )
            groups[key] = g
        g.mines.append(p)
    log.info(
        "grouped: %d mines → %d sections (skipped: no PLSS=%d, wrong state=%d)",
        len(points), len(groups), skipped_no_plss, skipped_wrong_state,
    )
    return groups


def _section_centroid(mines: Iterable[MinePoint]) -> tuple[float, float]:
    lats = [m.latitude for m in mines]
    lons = [m.longitude for m in mines]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def _build_name(mines: list[MinePoint]) -> str:
    seen: list[str] = []
    for m in mines:
        nm = m.name.strip()
        if nm and nm.lower() not in {s.lower() for s in seen}:
            seen.append(nm)
    if not seen:
        seen = ["Unnamed mine site"]
    base = " / ".join(seen)
    suffix = " (known mine)"
    max_base = 500 - len(suffix)
    if len(base) > max_base:
        base = base[: max_base - 1].rstrip() + "…"
    return base + suffix


def _build_minerals(mines: list[MinePoint]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in mines:
        for c in m.commodities:
            cl = c.lower()
            if cl not in seen:
                seen.add(cl)
                out.append(c)
    return out


def _build_report_links(mines: list[MinePoint]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in mines:
        if m.url and m.url not in seen:
            seen.add(m.url)
            out.append(m.url)
    return out


def _build_validity_notes(mines: list[MinePoint]) -> str:
    dep_ids = sorted({m.dep_id for m in mines if m.dep_id})
    dev_stats = sorted({m.dev_stat for m in mines if m.dev_stat})
    parts = [f"Auto-imported from USGS MRDS. Mines in section: {len(mines)}."]
    if dev_stats:
        parts.append("Dev status: " + ", ".join(dev_stats) + ".")
    if dep_ids:
        ids = ", ".join(dep_ids[:25]) + (f" (+{len(dep_ids) - 25} more)" if len(dep_ids) > 25 else "")
        parts.append("MRDS DEP_IDs: " + ids + ".")
    return " ".join(parts)


def build_target_payloads(groups: dict[Any, SectionGroup]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for g in groups.values():
        lat, lon = _section_centroid(g.mines)
        payloads.append(
            {
                "name": _build_name(g.mines),
                "location_plss": g.location_plss,
                "state_abbr": g.state_abbr,
                "township": g.township,
                "range_val": g.range_,
                "section": g.section,
                "meridian": g.meridian,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "minerals": _build_minerals(g.mines),
                "report_links": _build_report_links(g.mines),
                "validity_notes": _build_validity_notes(g.mines),
                "source": "mrds_auto",
                "status": "unknown",
                "is_uploaded": True,
                "skip_plss_geocode": True,
                "_mine_count": len(g.mines),
            }
        )
    return payloads


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: upsert via existing service
# ─────────────────────────────────────────────────────────────────────────────


def upsert_payloads(payloads: list[dict[str, Any]], commit_every: int = 100) -> dict[str, int]:
    """Insert/update each payload via mining_os.services.areas_of_focus.upsert_area."""
    from mining_os.services.areas_of_focus import upsert_area  # uses DATABASE_URL

    inserted = 0
    errors = 0
    for i, p in enumerate(payloads, 1):
        try:
            upsert_area(
                name=p["name"],
                location_plss=p["location_plss"],
                latitude=p["latitude"],
                longitude=p["longitude"],
                minerals=p["minerals"] or None,
                status=p["status"],
                report_links=p["report_links"] or None,
                validity_notes=p["validity_notes"],
                source=p["source"],
                retrieval_type="Known Mine",
                state_abbr=p["state_abbr"],
                township=p["township"],
                range_val=p["range_val"],
                section=p["section"],
                meridian=p["meridian"],
                is_uploaded=True,
                skip_plss_geocode=True,
            )
            inserted += 1
        except Exception as e:
            errors += 1
            log.warning("upsert failed (%s): %s", p["location_plss"], e)
        if i % commit_every == 0:
            log.info("upserted %d / %d (%d errors)", i, len(payloads), errors)
    return {"upserted": inserted, "errors": errors, "total": len(payloads)}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def write_dry_run_artifacts(state: str, payloads: list[dict[str, Any]]) -> Path:
    DRY_RUN_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = DRY_RUN_OUT_DIR / f"dry_run_{state}.json"
    out.write_text(json.dumps(payloads, indent=2, default=str), encoding="utf-8")
    return out


def write_raw_mrds(state: str, points: list[MinePoint]) -> Path:
    RAW_MRDS_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_MRDS_DIR / f"mrds_{state}.json"
    out.write_text(
        json.dumps([p.__dict__ for p in points], default=str), encoding="utf-8"
    )
    return out


def summarize(payloads: list[dict[str, Any]]) -> str:
    if not payloads:
        return "0 sections."
    payloads_sorted = sorted(payloads, key=lambda p: p["_mine_count"], reverse=True)
    top = payloads_sorted[:5]
    total_mines = sum(p["_mine_count"] for p in payloads)
    sample = payloads_sorted[len(payloads_sorted) // 2] if payloads_sorted else None
    lines = [
        f"sections: {len(payloads)}",
        f"total mines covered: {total_mines}",
        f"mean mines/section: {total_mines / len(payloads):.2f}",
        f"max mines/section: {max(p['_mine_count'] for p in payloads)}",
        "top 5 dense sections:",
    ]
    for p in top:
        lines.append(
            f"  • {p['_mine_count']} mines @ {p['location_plss']} → name='{p['name'][:120]}'"
        )
    if sample:
        lines.append("median sample:")
        lines.append(
            f"  • {sample['_mine_count']} mines @ {sample['location_plss']} → name='{sample['name'][:120]}'"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--states",
        nargs="+",
        default=["UT"],
        help="State abbreviations to process (default: UT)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="Cap MRDS rows per state (debug). Omit for no cap.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't touch the DB. Write JSON artifact and print summary.",
    )
    parser.add_argument(
        "--use-cached-mrds",
        action="store_true",
        help="Reuse the latest mrds_<STATE>.json in target_pipeline/data/mines_to_targets/raw/ "
             "instead of re-pulling MRDS.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.25,
        help="Seconds between BLM Cadastral reverse-geocode calls (default: 0.25)",
    )
    parser.add_argument(
        "--cache",
        default=str(CACHE_DIR / "plss_reverse_cache.json"),
        help="Reverse-geocode cache file (JSON dict)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cache = PlssReverseCache(Path(args.cache))
    grand_total = {"upserted": 0, "errors": 0, "sections": 0, "mines": 0}

    for state in (s.upper() for s in args.states):
        if state not in STATE_BBOXES:
            log.error("Unknown state %s — known: %s", state, sorted(STATE_BBOXES))
            continue
        log.info("== %s ==", state)

        if args.use_cached_mrds and (RAW_MRDS_DIR / f"mrds_{state}.json").exists():
            raw = json.loads((RAW_MRDS_DIR / f"mrds_{state}.json").read_text(encoding="utf-8"))
            points = [MinePoint(**r) for r in raw]
            log.info("loaded %d cached MRDS points for %s", len(points), state)
        else:
            log.info("pulling MRDS for %s bbox %s...", state, STATE_BBOXES[state])
            points = fetch_mrds_for_bbox(STATE_BBOXES[state])
            write_raw_mrds(state, points)
            log.info("pulled %d MRDS points for %s", len(points), state)

        before = len(points)
        points = [p for p in points if (p.dev_stat or "").lower() not in EXCLUDE_DEV_STAT]
        log.info("after DEV_STAT filter: %d (dropped %d Plant/processing rows)", len(points), before - len(points))

        if args.max is not None and len(points) > args.max:
            log.info("--max=%d → trimming from %d", args.max, len(points))
            points = points[: args.max]

        if not points:
            log.warning("no points left for %s — skipping", state)
            continue

        geocodes = reverse_geocode_points(points, cache, pause_seconds=args.pause)
        groups = group_by_section(points, geocodes, target_state=state)
        payloads = build_target_payloads(groups)

        log.info("\n--- DRY-RUN PREVIEW (%s) ---\n%s", state, summarize(payloads))
        out_path = write_dry_run_artifacts(state, payloads)
        log.info("wrote %d payloads to %s", len(payloads), out_path)

        grand_total["sections"] += len(payloads)
        grand_total["mines"] += sum(p["_mine_count"] for p in payloads)

        if args.dry_run:
            continue

        log.info("upserting %d sections for %s into DATABASE_URL=%s ...",
                 len(payloads), state,
                 (os.environ.get("DATABASE_URL") or "(unset)").split("@")[-1])
        result = upsert_payloads(payloads)
        grand_total["upserted"] += result["upserted"]
        grand_total["errors"] += result["errors"]
        log.info("== %s done: upserted %d, errors %d ==", state, result["upserted"], result["errors"])

    log.info("FINAL: %s", grand_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
