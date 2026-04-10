"""Upsert pipeline targets into Postgres (Mining OS `areas_of_focus` or optional `targets` table)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from target_pipeline.config import Settings, get_settings
from target_pipeline.models import TargetGroup
from target_pipeline.processors.normalize import plss_components_for_db

log = logging.getLogger("target_pipeline.db_writer")


def _coords_from_mapping(rec: Any) -> tuple[Optional[float], Optional[float]]:
    """Parse optional latitude/longitude from a StandardRecord-like dict."""
    if not rec or not isinstance(rec, dict):
        return None, None
    lat, lon = rec.get("latitude"), rec.get("longitude")
    try:
        lat_f = float(lat) if lat is not None and str(lat).strip() != "" else None
    except (TypeError, ValueError):
        lat_f = None
    try:
        lon_f = float(lon) if lon is not None and str(lon).strip() != "" else None
    except (TypeError, ValueError):
        lon_f = None
    return lat_f, lon_f


def _first_lat_lon_in_group(g: TargetGroup) -> tuple[Optional[float], Optional[float]]:
    for rec in (g.get("deposits") or []) + (g.get("claims") or []):
        la, lo = _coords_from_mapping(rec)
        if la is not None and lo is not None:
            return la, lo
    return None, None


def _pipeline_run_id() -> str:
    rid = (os.environ.get("TARGET_PIPELINE_RUN_ID") or "").strip()
    if rid:
        return rid
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _pipeline_characteristics_payload(groups: list[TargetGroup]) -> dict[str, Any]:
    return {
        "target_pipeline": {
            "managed": True,
            "import_tag": "target_pipeline",
            "run_id": _pipeline_run_id(),
            "version": 1,
            "groups": [
                {
                    "commodity": g.get("commodity"),
                    "score": g.get("score", 0),
                    "source_count": g.get("source_count", 0),
                    "has_report": g.get("has_report", False),
                    "deposit_names": g.get("deposit_names") or [],
                    "claim_ids": g.get("claim_ids") or [],
                    "report_links": g.get("report_links") or [],
                }
                for g in groups
            ],
        }
    }


def _merge_characteristics(existing: Any, new_payload: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if isinstance(existing, dict):
        base = dict(existing)
    elif isinstance(existing, str) and existing.strip():
        try:
            base = json.loads(existing)
        except json.JSONDecodeError:
            base = {}
    base["target_pipeline"] = new_payload.get("target_pipeline", {})
    return base


def collapse_by_plss_for_app(groups: list[TargetGroup]) -> list[TargetGroup]:
    """Merge (plss, commodity) groups that share the same plss_normalized into one logical target."""
    by_plss: dict[str, list[TargetGroup]] = {}
    no_key: list[TargetGroup] = []
    for g in groups:
        k = g.get("plss_normalized")
        if not k:
            no_key.append(g)
            continue
        by_plss.setdefault(k, []).append(g)

    merged: list[TargetGroup] = []
    for pnorm, gs in sorted(by_plss.items()):
        gs_sorted = sorted(gs, key=lambda x: (-(x.get("score") or 0), x.get("commodity") or ""))
        lead = gs_sorted[0]
        all_minerals = []
        for g in gs_sorted:
            c = g.get("commodity")
            if c and c not in all_minerals:
                all_minerals.append(c)
        all_links: list[str] = []
        for g in gs_sorted:
            for u in g.get("report_links") or []:
                if u not in all_links:
                    all_links.append(u)
        max_score = max((g.get("score") or 0) for g in gs_sorted)
        deposits = []
        claims = []
        for g in gs_sorted:
            deposits.extend(g.get("deposits") or [])
            claims.extend(g.get("claims") or [])
        counties = [g.get("county") for g in gs_sorted if g.get("county")]
        county = counties[0] if counties else lead.get("county")
        plss_disp = (lead.get("plss") or "").strip() or pnorm
        comm_label = ", ".join(all_minerals) if len(all_minerals) > 1 else (all_minerals[0] if all_minerals else "Multi")
        if county:
            name = f"{comm_label} Target {plss_disp} - {county} County"
        else:
            name = f"{comm_label} Target {plss_disp}"
        m: TargetGroup = TargetGroup(
            plss=plss_disp,
            plss_normalized=pnorm,
            commodity=",".join(all_minerals) if all_minerals else None,
            state=lead.get("state"),
            county=county,
            deposits=deposits,
            claims=claims,
            target_name=name[:500],
            deposit_names=list(
                dict.fromkeys(sum((g.get("deposit_names") or [] for g in gs_sorted), []))
            ),
            claim_ids=list(dict.fromkeys(sum((g.get("claim_ids") or [] for g in gs_sorted), []))),
            report_links=all_links,
            source_count=sum(g.get("source_count") or 0 for g in gs_sorted),
            has_report=any(g.get("has_report") for g in gs_sorted),
            score=max_score,
            score_notes=["merged_by_plss"],
        )
        m["_merged_segments"] = gs_sorted  # type: ignore[assignment]
        merged.append(m)
    if no_key:
        log.warning("Skipping %s targets with no plss_normalized (cannot merge/write safely)", len(no_key))
    return merged


def write_targets(
    engine: Engine,
    scored_groups: list[TargetGroup],
    settings: Optional[Settings] = None,
) -> dict[str, int]:
    settings = settings or get_settings()
    table = settings.output_table

    if table == "areas_of_focus":
        to_write = (
            collapse_by_plss_for_app(scored_groups)
            if settings.merge_by_plss_for_app
            else scored_groups
        )
        return _write_areas_of_focus(engine, to_write)
    if table == "targets":
        return _write_targets_table(engine, scored_groups)
    raise ValueError(f"Unsupported OUTPUT_TABLE={table!r} (use areas_of_focus or targets)")


def _write_targets_table(engine: Engine, groups: list[TargetGroup]) -> dict[str, int]:
    written = 0
    with engine.begin() as conn:
        for g in groups:
            pnorm = g.get("plss_normalized") or g.get("plss") or ""
            comm = g.get("commodity") or ""
            if not pnorm:
                log.warning("skip targets row: missing plss_normalized")
                continue
            lat_f, lon_f = _first_lat_lon_in_group(g)
            conn.execute(
                text("""
                INSERT INTO targets (
                  target_name, state, county, plss, plss_normalized, commodity,
                  source_count, has_report, score,
                  deposit_names_json, claim_ids_json, report_links_json,
                  latitude, longitude, updated_at
                ) VALUES (
                  :target_name, :state, :county, :plss, :plss_normalized, :commodity,
                  :source_count, :has_report, :score,
                  CAST(:deposit_names AS jsonb), CAST(:claim_ids AS jsonb), CAST(:report_links AS jsonb),
                  :latitude, :longitude, now()
                )
                ON CONFLICT (plss_normalized, commodity) DO UPDATE SET
                  target_name = EXCLUDED.target_name,
                  state = EXCLUDED.state,
                  county = EXCLUDED.county,
                  plss = EXCLUDED.plss,
                  source_count = EXCLUDED.source_count,
                  has_report = EXCLUDED.has_report,
                  score = EXCLUDED.score,
                  deposit_names_json = EXCLUDED.deposit_names_json,
                  claim_ids_json = EXCLUDED.claim_ids_json,
                  report_links_json = EXCLUDED.report_links_json,
                  latitude = COALESCE(EXCLUDED.latitude, targets.latitude),
                  longitude = COALESCE(EXCLUDED.longitude, targets.longitude),
                  updated_at = now()
                """),
                {
                    "target_name": g.get("target_name") or "Unknown",
                    "state": g.get("state"),
                    "county": g.get("county"),
                    "plss": g.get("plss") or pnorm,
                    "plss_normalized": pnorm,
                    "commodity": comm,
                    "source_count": g.get("source_count") or 0,
                    "has_report": g.get("has_report", False),
                    "score": g.get("score") or 0,
                    "deposit_names": json.dumps(g.get("deposit_names") or []),
                    "claim_ids": json.dumps(g.get("claim_ids") or []),
                    "report_links": json.dumps(g.get("report_links") or []),
                    "latitude": lat_f,
                    "longitude": lon_f,
                },
            )
            written += 1
    log.info("targets table: rows upserted=%s", written)
    return {"written": written}


def _write_areas_of_focus(
    engine: Engine,
    groups: list[TargetGroup],
) -> dict[str, int]:
    inserted = 0
    updated = 0

    with engine.begin() as conn:
        for g in groups:
            pnorm = g.get("plss_normalized")
            if not pnorm:
                continue
            loc_plss = (g.get("plss") or "").strip() or pnorm
            minerals_csv = g.get("commodity") or ""
            minerals = [m.strip() for m in minerals_csv.split(",") if m.strip()] if minerals_csv else []
            if not minerals and g.get("commodity"):
                minerals = [g["commodity"]]  # type: ignore[list-item]

            first_rec = None
            if g.get("deposits"):
                first_rec = g["deposits"][0]
            elif g.get("claims"):
                first_rec = g["claims"][0]
            lat_f, lon_f = _first_lat_lon_in_group(g)
            if lat_f is None and first_rec:
                lat_f, lon_f = _coords_from_mapping(first_rec)
            default_state = (g.get("state") or "UT")[:2]
            comp = plss_components_for_db(first_rec, default_state=default_state) if first_rec else None
            if not comp:
                comp = {
                    "state_abbr": default_state,
                    "township": None,
                    "range": None,
                    "section": None,
                    "meridian": "26",
                }
            st = comp.get("state_abbr") or default_state
            twp = comp.get("township")
            rng = comp.get("range")
            sec = comp.get("section")
            mer = comp.get("meridian")

            existing = conn.execute(
                text(
                    "SELECT id, minerals, report_links, characteristics, roi_score FROM areas_of_focus WHERE plss_normalized = :k"
                ),
                {"k": pnorm},
            ).mappings().first()

            report_links = list(g.get("report_links") or [])
            name = (g.get("target_name") or "Unknown")[:500]
            roi = g.get("score") or 0
            county = g.get("county")
            validity = f"County: {county}" if county else None

            segs: list[TargetGroup] = list(g.get("_merged_segments") or [g])  # type: ignore[arg-type]
            pipe_meta = _pipeline_characteristics_payload(segs)

            if existing:
                ex_m = list(existing.get("minerals") or [])
                merged_m = list(dict.fromkeys(ex_m + minerals))
                ex_l = list(existing.get("report_links") or [])
                merged_l = list(dict.fromkeys(ex_l + report_links))
                new_chars = _merge_characteristics(existing.get("characteristics"), pipe_meta)
                prev_roi = existing.get("roi_score")
                if prev_roi is not None:
                    roi = max(int(prev_roi), int(roi))

                conn.execute(
                    text("""
                    UPDATE areas_of_focus SET
                      name = COALESCE(:name, name),
                      location_plss = COALESCE(:location_plss, location_plss),
                      minerals = :minerals,
                      report_links = :report_links,
                      roi_score = :roi_score,
                      validity_notes = COALESCE(:validity_notes, validity_notes),
                      source = :source,
                      state_abbr = COALESCE(:state_abbr, state_abbr),
                      township = COALESCE(:township, township),
                      "range" = COALESCE(:range_val, "range"),
                      section = COALESCE(:section, section),
                      meridian = COALESCE(:meridian, meridian),
                      latitude = COALESCE(:lat, latitude),
                      longitude = COALESCE(:lon, longitude),
                      characteristics = CAST(:characteristics AS jsonb),
                      is_uploaded = CASE WHEN :mark_uploaded THEN TRUE ELSE is_uploaded END,
                      updated_at = now()
                    WHERE id = :id
                    """),
                    {
                        "id": existing["id"],
                        "name": name,
                        "location_plss": loc_plss,
                        "minerals": merged_m,
                        "report_links": merged_l,
                        "roi_score": roi,
                        "validity_notes": validity,
                        "source": "target_pipeline",
                        "state_abbr": st,
                        "township": twp,
                        "range_val": rng,
                        "section": sec,
                        "meridian": mer,
                        "lat": lat_f,
                        "lon": lon_f,
                        "characteristics": json.dumps(new_chars),
                        "mark_uploaded": True,
                    },
                )
                updated += 1
            else:
                new_chars = _merge_characteristics({}, pipe_meta)
                conn.execute(
                    text("""
                    INSERT INTO areas_of_focus (
                      name, location_plss, plss_normalized, minerals, status, report_links,
                      validity_notes, source, roi_score, priority,
                      state_abbr, township, "range", section, meridian,
                      latitude, longitude,
                      characteristics, is_uploaded
                    ) VALUES (
                      :name, :location_plss, :plss_normalized, :minerals, :status, :report_links,
                      :validity_notes, :source, :roi_score, :priority,
                      :state_abbr, :township, :range_val, :section, :meridian,
                      :lat, :lon,
                      CAST(:characteristics AS jsonb), :is_uploaded
                    )
                    """),
                    {
                        "name": name,
                        "location_plss": loc_plss,
                        "plss_normalized": pnorm,
                        "minerals": minerals,
                        "status": "unknown",
                        "report_links": report_links,
                        "validity_notes": validity,
                        "source": "target_pipeline",
                        "roi_score": roi,
                        "priority": "monitoring_low",
                        "state_abbr": st,
                        "township": twp,
                        "range_val": rng,
                        "section": sec,
                        "meridian": mer,
                        "lat": lat_f,
                        "lon": lon_f,
                        "characteristics": json.dumps(new_chars),
                        "is_uploaded": True,
                    },
                )
                inserted += 1

    log.info("areas_of_focus: inserted=%s updated=%s", inserted, updated)
    return {"inserted": inserted, "updated": updated}
