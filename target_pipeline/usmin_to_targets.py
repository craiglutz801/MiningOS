#!/usr/bin/env python3
"""
Create Targets from the local USGS USMIN mine-feature points shown on the map
(data_files/usmin_points/<ST>.json.gz — the "USMIN Mines — clickable" layer).

Mirrors target_pipeline/mines_to_targets.py: points are reverse-geocoded to a
PLSS section (shared disk cache) and grouped into ONE Target per mine-bearing
section. Unlike mines_to_targets, this importer is strictly INSERT-ONLY:
sections whose ``plss_normalized`` already exists as a Target (any source) are
left completely untouched.

Run from repo root:

    # 1) Dry-run first — geocodes + writes payload artifacts, no DB writes
    python -m target_pipeline.usmin_to_targets --states UT NV ID WY --dry-run

    # 2) Real import (reuses the dry-run artifacts; only inserts new sections)
    DATABASE_URL='postgresql://...' python -m target_pipeline.usmin_to_targets \
        --states UT NV ID WY --upsert-only

New rows get ``source = 'usmin_auto'`` so the batch is revertible::

    DELETE FROM areas_of_focus WHERE source = 'usmin_auto';
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from target_pipeline.mines_to_targets import (  # noqa: E402
    BLM_PLSS_REVERSE_URL,
    CACHE_DIR,
    MinePoint,
    PlssReverseCache,
    SectionGroup,
    _section_centroid,
    group_by_section,
)

log = logging.getLogger("usmin_to_targets")

USMIN_DATA_DIR = _REPO_ROOT / "data_files" / "usmin_points"
OUT_DIR = _REPO_ROOT / "target_pipeline" / "data" / "usmin_to_targets"
KNOWN_STATES = ("UT", "NV", "ID", "WY")
MAX_REPORT_LINKS = 10


def usmin_detail_url(usmin_id: str) -> str:
    return f"https://mrdata.usgs.gov/usmin/show-usmin.php?type=point&id={usmin_id}"


def load_usmin_points(state: str) -> list[MinePoint]:
    """Read data_files/usmin_points/<ST>.json.gz records: [lon, lat, type, name, id]."""
    path = USMIN_DATA_DIR / f"{state}.json.gz"
    if not path.exists():
        raise FileNotFoundError(f"No USMIN point file at {path}")
    raw = json.loads(gzip.open(path).read())
    out: list[MinePoint] = []
    for r in raw:
        lon, lat = float(r[0]), float(r[1])
        ftype = (r[2] or "").strip()
        name = (r[3] or "").strip()
        usmin_id = str(r[4] or "").strip()
        out.append(
            MinePoint(
                dep_id=usmin_id,
                name=name,
                dev_stat=ftype,  # feature type (Adit, Shaft, Prospect Pit, ...)
                commodities=[],  # USMIN topo features carry no commodity info
                url=usmin_detail_url(usmin_id) if usmin_id else "",
                grade="",
                longitude=lon,
                latitude=lat,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Reverse-geocode: one BLM call per unique grid cell, with retry
# ─────────────────────────────────────────────────────────────────────────────
#
# mines_to_targets.reverse_geocode_points issues one call per *point* and
# permanently caches None on timeout, silently dropping mines. With 124k NV
# points that's both slow and lossy, so here we (a) dedupe to unique cache
# cells before calling BLM, (b) retry failures once, (c) only cache a None
# when BLM positively answered "no PLSS here", and (d) re-probe pre-existing
# cached Nones (many are timeout artifacts from earlier MRDS runs).


def _reverse_once(lat: float, lon: float, timeout: float) -> tuple[bool, dict[str, Any] | None]:
    """Returns (answered, plss_dict_or_None). answered=False means request failed."""
    import requests
    from mining_os.services.plss_geocode import _parse_plssid_attrs

    params = {
        "f": "json",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PLSSID,FRSTDIVNO",
        "returnGeometry": "false",
        "resultRecordCount": "5",
    }
    try:
        r = requests.get(BLM_PLSS_REVERSE_URL, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or data.get("error"):
            return False, None
        feats = data.get("features") or []
        if not feats:
            return True, None  # positive "not on PLSS land"
        attrs = (feats[0] or {}).get("attributes") or {}
        return True, _parse_plssid_attrs(attrs.get("PLSSID"), attrs.get("FRSTDIVNO"))
    except Exception:
        return False, None


def geocode_points_via_cells(
    points: list[MinePoint],
    cache: PlssReverseCache,
    workers: int = 8,
    timeout: float = 25.0,
    retry_nulls: bool = True,
) -> dict[int, dict[str, Any] | None]:
    """Map point-index → PLSS dict (or None), calling BLM once per unique grid cell."""
    import concurrent.futures
    import threading
    import time as _time

    cells: dict[str, tuple[float, float]] = {}
    for p in points:
        k = cache.cell_key(p.latitude, p.longitude)
        cells.setdefault(k, (p.latitude, p.longitude))

    todo: list[tuple[str, float, float]] = []
    for k, (lat, lon) in cells.items():
        hit, val = cache.get(lat, lon)
        if not hit or (retry_nulls and val is None):
            todo.append((k, lat, lon))

    log.info("geocode: %d points → %d unique cells (%d need BLM lookups)",
             len(points), len(cells), len(todo))

    failed = 0
    done = 0
    lock = threading.Lock()

    def _work(item: tuple[str, float, float]) -> None:
        nonlocal failed, done
        _, lat, lon = item
        answered, val = _reverse_once(lat, lon, timeout)
        if not answered:
            _time.sleep(1.0)
            answered, val = _reverse_once(lat, lon, timeout)
        with lock:
            done += 1
            if answered:
                cache.set(lat, lon, val)
            else:
                failed += 1
            if done % 500 == 0:
                cache.flush()
                log.info("geocode progress: %d/%d cells (%d failed)", done, len(todo), failed)

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_work, todo))
        cache.flush()
    if failed:
        log.warning("geocode: %d cells failed twice — their mines are skipped this run "
                    "(rerun to retry; they were NOT cached as empty)", failed)

    out: dict[int, dict[str, Any] | None] = {}
    for i, p in enumerate(points):
        _, val = cache.get(p.latitude, p.longitude)
        out[i] = val
    return out


def _build_name(group: SectionGroup) -> str:
    """Named features joined ' / '; unnamed-only sections fall back to type counts."""
    names: list[str] = []
    seen_lower: set[str] = set()
    for m in group.mines:
        nm = m.name.strip()
        if nm and nm.lower() not in seen_lower:
            seen_lower.add(nm.lower())
            names.append(nm)
    if names:
        base = " / ".join(names)
    else:
        counts = Counter((m.dev_stat or "Mine feature").strip() or "Mine feature" for m in group.mines)
        parts = [f"{t} ×{n}" if n > 1 else t for t, n in counts.most_common()]
        base = ", ".join(parts)
    suffix = " (known mine)"
    max_base = 500 - len(suffix)
    if len(base) > max_base:
        base = base[: max_base - 1].rstrip() + "…"
    return base + suffix


def _build_validity_notes(group: SectionGroup) -> str:
    counts = Counter((m.dev_stat or "unknown").strip() or "unknown" for m in group.mines)
    types = ", ".join(f"{t} ×{n}" for t, n in counts.most_common())
    parts = [
        f"Auto-imported from USGS USMIN (Prospect- and Mine-Related Features). "
        f"Features in section: {len(group.mines)}.",
        f"Feature types: {types}.",
    ]
    ids = sorted({m.dep_id for m in group.mines if m.dep_id})
    if ids:
        shown = ", ".join(ids[:25]) + (f" (+{len(ids) - 25} more)" if len(ids) > 25 else "")
        parts.append("USMIN point IDs: " + shown + ".")
    return " ".join(parts)


def build_target_payloads(groups: dict[Any, SectionGroup]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for g in groups.values():
        lat, lon = _section_centroid(g.mines)
        links: list[str] = []
        for m in g.mines:
            if m.url and m.url not in links:
                links.append(m.url)
            if len(links) >= MAX_REPORT_LINKS:
                break
        payloads.append(
            {
                "name": _build_name(g),
                "location_plss": g.location_plss,
                "state_abbr": g.state_abbr,
                "township": g.township,
                "range_val": g.range_,
                "section": g.section,
                "meridian": g.meridian,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "minerals": [],
                "report_links": links,
                "validity_notes": _build_validity_notes(g),
                "source": "usmin_auto",
                "status": "unknown",
                "_mine_count": len(g.mines),
            }
        )
    return payloads


# ─────────────────────────────────────────────────────────────────────────────
# Insert-only upsert
# ─────────────────────────────────────────────────────────────────────────────


def _existing_plss_keys(account_id: int) -> set[str]:
    from sqlalchemy import text
    from mining_os.db import get_engine

    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT plss_normalized FROM areas_of_focus "
                "WHERE account_id = :a AND plss_normalized IS NOT NULL "
                "AND TRIM(plss_normalized) != ''"
            ),
            {"a": account_id},
        ).scalars().all()
    return {str(k) for k in rows}


def insert_new_only(
    payloads: list[dict[str, Any]],
    account_id: int,
) -> dict[str, int]:
    """Insert payloads whose section has no existing Target. Existing => skipped."""
    from mining_os.services.areas_of_focus import _normalize_plss, upsert_area

    existing = _existing_plss_keys(account_id)
    log.info("account %d has %d existing PLSS-keyed targets", account_id, len(existing))

    inserted = 0
    skipped = 0
    errors = 0
    for i, p in enumerate(payloads, 1):
        key = _normalize_plss(p["location_plss"], default_state=p["state_abbr"])
        if key and key in existing:
            skipped += 1
            continue
        try:
            upsert_area(
                name=p["name"],
                location_plss=p["location_plss"],
                latitude=p["latitude"],
                longitude=p["longitude"],
                minerals=None,
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
                account_id=account_id,
            )
            inserted += 1
            if key:
                existing.add(key)  # guard against dupes within this batch
        except Exception as e:
            errors += 1
            log.warning("insert failed (%s): %s", p["location_plss"], e)
        if i % 200 == 0:
            log.info("progress %d/%d — inserted %d, skipped %d, errors %d",
                     i, len(payloads), inserted, skipped, errors)
    return {"inserted": inserted, "skipped": skipped, "errors": errors, "total": len(payloads)}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def summarize(payloads: list[dict[str, Any]]) -> str:
    if not payloads:
        return "0 sections."
    by_count = sorted(payloads, key=lambda p: p["_mine_count"], reverse=True)
    total_mines = sum(p["_mine_count"] for p in payloads)
    lines = [
        f"sections: {len(payloads)}",
        f"total USMIN features covered: {total_mines}",
        f"mean features/section: {total_mines / len(payloads):.2f}",
        "top 3 dense sections:",
    ]
    for p in by_count[:3]:
        lines.append(f"  • {p['_mine_count']} @ {p['location_plss']} → '{p['name'][:100]}'")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", nargs="+", default=list(KNOWN_STATES))
    parser.add_argument("--dry-run", action="store_true",
                        help="Geocode + write payload artifacts; no DB writes.")
    parser.add_argument("--upsert-only", action="store_true",
                        help="Skip geocoding; load payloads_<ST>.json and insert new sections only.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--fast-timeout", type=float, default=25.0)
    parser.add_argument("--cache", default=str(CACHE_DIR / "plss_reverse_cache.json"))
    parser.add_argument("--account-id", type=int, default=1)
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    grand = {"inserted": 0, "skipped": 0, "errors": 0, "sections": 0}

    cache = None if args.upsert_only else PlssReverseCache(Path(args.cache))

    for state in (s.upper() for s in args.states):
        if state not in KNOWN_STATES:
            log.error("Unknown state %s — known: %s", state, KNOWN_STATES)
            continue
        log.info("== %s ==", state)
        artifact = OUT_DIR / f"payloads_{state}.json"

        if args.upsert_only:
            if not artifact.exists():
                log.error("No payload artifact at %s — run a dry-run first", artifact)
                continue
            payloads = json.loads(artifact.read_text(encoding="utf-8"))
        else:
            points = load_usmin_points(state)
            log.info("loaded %d USMIN points for %s", len(points), state)
            geocodes = geocode_points_via_cells(
                points,
                cache,
                workers=max(1, int(args.workers)),
                timeout=float(args.fast_timeout),
            )
            groups = group_by_section(points, geocodes, target_state=state)
            payloads = build_target_payloads(groups)
            artifact.write_text(json.dumps(payloads, indent=1, default=str), encoding="utf-8")
            log.info("wrote %d payloads to %s", len(payloads), artifact)

        log.info("\n--- %s ---\n%s", state, summarize(payloads))
        grand["sections"] += len(payloads)

        if args.dry_run:
            continue

        result = insert_new_only(payloads, account_id=args.account_id)
        log.info("== %s done: inserted %d, skipped(existing) %d, errors %d ==",
                 state, result["inserted"], result["skipped"], result["errors"])
        for k in ("inserted", "skipped", "errors"):
            grand[k] += result[k]

    log.info("FINAL: %s", grand)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
