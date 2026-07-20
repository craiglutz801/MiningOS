"""SITLA source run orchestration: discover → fetch → parse → upsert opportunities."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.sitla_intel.adapters.registry import build_adapter
from mining_os.sitla_intel.normalize import (
    canonical_key,
    extract_plss,
    map_lifecycle,
    map_opportunity_type,
    record_hash,
)
from mining_os.sitla_intel.scoring import compute_scores

log = logging.getLogger("mining_os.sitla_intel.ingest")
ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "data_files" / "sitla_intel_artifacts"


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str)


def list_sources(enabled_only: bool = True) -> list[dict[str, Any]]:
    eng = get_engine()
    clause = "WHERE enabled = true" if enabled_only else ""
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id, source_key, name, listing_url, parser_kind, enabled, manual_only,
                       health_status, configuration_json, adapter_class, consecutive_failures
                FROM sitla_intel.sources
                {clause}
                ORDER BY name
                """
            )
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        cfg = d.get("configuration_json")
        if isinstance(cfg, str):
            d["configuration_json"] = json.loads(cfg)
        elif cfg is None:
            d["configuration_json"] = {}
        out.append(d)
    return out


def run_source(
    source_key: str,
    *,
    account_id: int,
    trigger_type: str = "manual",
    enrich: bool = True,
) -> dict[str, Any]:
    sources = [s for s in list_sources(enabled_only=False) if s["source_key"] == source_key]
    if not sources:
        return {"ok": False, "error": f"Unknown source_key={source_key}"}
    source = sources[0]
    eng = get_engine()
    run_id = str(uuid4())
    started = datetime.now(timezone.utc)
    metrics: dict[str, Any] = {"adapter": None, "urls": []}
    created = updated = unchanged = failed = 0

    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO sitla_intel.source_runs (
                  id, source_id, run_type, started_at, status, trigger_type
                ) VALUES (
                  CAST(:id AS uuid), CAST(:sid AS uuid), 'LISTING_REFRESH', :started, 'running', :trig
                )
                """
            ),
            {"id": run_id, "sid": str(source["id"]), "started": started, "trig": trigger_type},
        )

    try:
        adapter = build_adapter(source)
        metrics["adapter"] = adapter.__class__.__name__
        urls = adapter.discover()
        metrics["urls"] = urls
        if not urls:
            raise RuntimeError("Adapter discovered no listing URLs/fixtures")

        all_records = []
        for url in urls:
            artifact = adapter.fetch(url)
            sha = hashlib.sha256(artifact.content).hexdigest()
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            storage = ARTIFACT_DIR / f"{source_key}_{run_id[:8]}_{sha[:12]}.bin"
            storage.write_bytes(artifact.content)
            artifact_id = str(uuid4())
            with eng.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO sitla_intel.raw_artifacts (
                          id, source_id, source_run_id, source_url, retrieved_at,
                          filename, media_type, storage_uri, sha256, byte_size, metadata_json
                        ) VALUES (
                          CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:rid AS uuid),
                          :url, :ret, :fn, :mt, :uri, :sha, :sz, CAST(:meta AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": artifact_id,
                        "sid": str(source["id"]),
                        "rid": run_id,
                        "url": artifact.source_url,
                        "ret": artifact.retrieved_at,
                        "fn": artifact.filename,
                        "mt": artifact.media_type,
                        "uri": str(storage),
                        "sha": sha,
                        "sz": len(artifact.content),
                        "meta": _json(artifact.metadata or {}),
                    },
                )
            records = list(adapter.parse(artifact))
            metrics.setdefault("validation_warnings", []).extend(adapter.validate(records)[:20])
            for rec in records:
                result = _upsert_record(
                    eng,
                    account_id=account_id,
                    source=source,
                    run_id=run_id,
                    artifact_id=artifact_id,
                    rec=rec,
                )
                if result == "created":
                    created += 1
                elif result == "updated":
                    updated += 1
                elif result == "unchanged":
                    unchanged += 1
                else:
                    failed += 1
                all_records.append(rec)

        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE sitla_intel.source_runs
                    SET completed_at = :done, status = 'completed',
                        records_discovered = :disc, records_created = :c,
                        records_updated = :u, records_unchanged = :un,
                        records_failed = :f, metrics_json = CAST(:m AS jsonb)
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "done": datetime.now(timezone.utc),
                    "disc": len(all_records),
                    "c": created,
                    "u": updated,
                    "un": unchanged,
                    "f": failed,
                    "m": _json(metrics),
                    "id": run_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE sitla_intel.sources
                    SET health_status = 'HEALTHY', last_success_at = :ts,
                        consecutive_failures = 0, updated_at = :ts
                    WHERE id = CAST(:sid AS uuid)
                    """
                ),
                {"ts": datetime.now(timezone.utc), "sid": str(source["id"])},
            )

        if enrich:
            try:
                from mining_os.sitla_intel.enrichment import enrich_account_opportunities
                from mining_os.sitla_intel.history import match_historical_offerings

                enrich_account_opportunities(account_id, limit=40)
                match_historical_offerings(account_id)
            except Exception:
                log.exception("post-ingest enrichment failed")
        try:
            from mining_os.sitla_intel.alerts import detect_watchlist_changes

            detect_watchlist_changes(account_id)
        except Exception:
            log.exception("sitla watchlist detection failed")

        return {
            "ok": True,
            "source_key": source_key,
            "run_id": run_id,
            "records_discovered": len(all_records),
            "records_created": created,
            "records_updated": updated,
            "records_unchanged": unchanged,
            "records_failed": failed,
            "metrics": metrics,
        }
    except Exception as e:
        log.exception("sitla source run failed for %s", source_key)
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE sitla_intel.source_runs
                    SET completed_at = :done, status = 'failed', error_message = :err,
                        metrics_json = CAST(:m AS jsonb)
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "done": datetime.now(timezone.utc),
                    "err": str(e)[:2000],
                    "m": _json(metrics),
                    "id": run_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE sitla_intel.sources
                    SET health_status = 'FAILED', last_failure_at = :ts,
                        consecutive_failures = consecutive_failures + 1, updated_at = :ts
                    WHERE id = CAST(:sid AS uuid)
                    """
                ),
                {"ts": datetime.now(timezone.utc), "sid": str(source["id"])},
            )
        return {"ok": False, "error": str(e), "source_key": source_key, "run_id": run_id}


def run_all_enabled_sources(account_id: int, trigger_type: str = "scheduled") -> dict[str, Any]:
    results = []
    for src in list_sources(enabled_only=True):
        if src.get("manual_only") and trigger_type == "scheduled":
            continue
        results.append(run_source(src["source_key"], account_id=account_id, trigger_type=trigger_type))
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "ran": len(results), "succeeded": ok, "results": results}


def _upsert_record(eng, *, account_id: int, source: dict[str, Any], run_id: str, artifact_id: str, rec) -> str:
    try:
        now = datetime.now(timezone.utc)
        legal = rec.legal_description_raw
        plss = extract_plss(legal)
        otype = map_opportunity_type(rec.opportunity_type_raw)
        lifecycle = map_lifecycle(rec.status_raw)
        ckey = canonical_key(
            rec.reference_number,
            rec.county_name,
            legal,
            rec.offering_cycle,
            rec.source_record_key,
        )
        payload = {
            "title": rec.title,
            "ref": rec.reference_number,
            "status": rec.status_raw,
            "min_bid": rec.minimum_bid,
            "deadline": rec.bidding_end_at.isoformat() if rec.bidding_end_at else None,
        }
        rhash = record_hash(payload)
        score_inputs = {
            "official_active": lifecycle in {"BIDDING_OPEN", "SCHEDULED", "COMPETING_APPLICATION_OPEN", "PUBLIC_NOTICE_OPEN", "ANNOUNCED"},
            "deadline_clear": bool(rec.bidding_end_at or rec.application_deadline),
            "geometry_resolved": rec.latitude is not None and rec.longitude is not None,
            "commercial_terms": rec.minimum_bid is not None,
            "commodity_evidence": bool(rec.commodities or rec.commodity_raw),
            "data_fresh": True,
            "documents_complete": bool(rec.detail_url),
            "strategic_mineral": any(
                x.lower() in {"lithium", "uranium", "tungsten", "helium"}
                for x in (rec.commodities or []) + ([rec.commodity_raw] if rec.commodity_raw else [])
            ),
            "rights_unclear": True,
        }
        scores = compute_scores(score_inputs)
        expl = _json(scores["explanation_json"])
        coms = list(rec.commodities or ([] if not rec.commodity_raw else [rec.commodity_raw]))

        with eng.begin() as conn:
            existing = conn.execute(
                text(
                    """
                    SELECT id, lifecycle_status, minimum_bid
                    FROM sitla_intel.opportunities
                    WHERE account_id = :aid AND canonical_key = :ck
                    """
                ),
                {"aid": account_id, "ck": ckey},
            ).mappings().first()

            obs_params = {
                "sid": str(source["id"]),
                "rid": run_id,
                "aid": artifact_id,
                "skey": rec.source_record_key,
                "obs": now,
                "title": rec.title,
                "ref": rec.reference_number,
                "status": rec.status_raw,
                "nstatus": lifecycle,
                "otype": rec.opportunity_type_raw,
                "commodity": rec.commodity_raw,
                "legal": legal,
                "acre": rec.acreage,
                "minbid": rec.minimum_bid,
                "winbid": rec.winning_bid,
                "adead": rec.application_deadline,
                "bstart": rec.bidding_start_at,
                "bend": rec.bidding_end_at,
                "url": rec.detail_url,
                "ebid": rec.external_bid_url,
                "payload": _json(rec.raw_payload),
                "rhash": rhash,
            }

            if existing:
                conn.execute(
                    text(
                        """
                        INSERT INTO sitla_intel.opportunity_observations (
                          opportunity_id, source_id, source_run_id, raw_artifact_id, source_record_key,
                          observed_at, raw_title, raw_reference_number, raw_status, normalized_status,
                          raw_opportunity_type, raw_commodity, raw_legal_description, acreage,
                          minimum_bid, winning_bid, application_deadline, bidding_start_at, bidding_end_at,
                          official_detail_url, external_bid_url, raw_payload_json, record_hash
                        ) VALUES (
                          CAST(:oid AS uuid), CAST(:sid AS uuid), CAST(:rid AS uuid), CAST(:aid AS uuid), :skey,
                          :obs, :title, :ref, :status, :nstatus,
                          :otype, :commodity, :legal, :acre,
                          :minbid, :winbid, :adead, :bstart, :bend,
                          :url, :ebid, CAST(:payload AS jsonb), :rhash
                        )
                        """
                    ),
                    {**obs_params, "oid": str(existing["id"])},
                )
                changed = (
                    existing.get("lifecycle_status") != lifecycle
                    or (
                        existing.get("minimum_bid") is not None
                        and rec.minimum_bid is not None
                        and float(existing["minimum_bid"]) != float(rec.minimum_bid)
                    )
                )
                conn.execute(
                    text(
                        """
                        UPDATE sitla_intel.opportunities SET
                          best_title = COALESCE(:title, best_title),
                          reference_number = COALESCE(:ref, reference_number),
                          opportunity_type = :otype,
                          lifecycle_status = :life,
                          county_name = COALESCE(:county, county_name),
                          published_commodity = COALESCE(:commodity, published_commodity),
                          commodities = CASE WHEN :has_com THEN CAST(:coms AS text[]) ELSE commodities END,
                          acreage = COALESCE(:acre, acreage),
                          legal_description_raw = COALESCE(:legal, legal_description_raw),
                          plss_key = COALESCE(:plss, plss_key),
                          township = COALESCE(:twp, township),
                          range = COALESCE(:rng, range),
                          section_summary = COALESCE(:sec, section_summary),
                          latitude = COALESCE(:lat, latitude),
                          longitude = COALESCE(:lon, longitude),
                          offering_cycle = COALESCE(:cycle, offering_cycle),
                          bidding_start_at = COALESCE(:bstart, bidding_start_at),
                          bidding_end_at = COALESCE(:bend, bidding_end_at),
                          application_deadline = COALESCE(:adead, application_deadline),
                          minimum_bid = COALESCE(:minbid, minimum_bid),
                          winning_bid = COALESCE(:winbid, winning_bid),
                          official_detail_url = COALESCE(:url, official_detail_url),
                          external_bid_url = COALESCE(:ebid, external_bid_url),
                          mineral_potential_score = :ms,
                          acquisition_readiness_score = :ascore,
                          overall_priority_score = :os,
                          priority_tier = :tier,
                          score_explanation_json = CAST(:expl AS jsonb),
                          last_observed_at = :obs,
                          enrichment_status = 'pending',
                          updated_at = :obs
                        WHERE id = CAST(:oid AS uuid)
                        """
                    ),
                    {
                        "title": rec.title,
                        "ref": rec.reference_number,
                        "otype": otype,
                        "life": lifecycle,
                        "county": rec.county_name,
                        "commodity": rec.commodity_raw,
                        "has_com": bool(coms),
                        "coms": coms,
                        "acre": rec.acreage,
                        "legal": legal,
                        "plss": plss.get("plss_key"),
                        "twp": plss.get("township"),
                        "rng": plss.get("range"),
                        "sec": plss.get("section"),
                        "lat": rec.latitude,
                        "lon": rec.longitude,
                        "cycle": rec.offering_cycle,
                        "bstart": rec.bidding_start_at,
                        "bend": rec.bidding_end_at,
                        "adead": rec.application_deadline,
                        "minbid": rec.minimum_bid,
                        "winbid": rec.winning_bid,
                        "url": rec.detail_url,
                        "ebid": rec.external_bid_url,
                        "ms": scores["mineral_potential_score"],
                        "ascore": scores["acquisition_readiness_score"],
                        "os": scores["overall_priority_score"],
                        "tier": scores["priority_tier"],
                        "expl": expl,
                        "obs": now,
                        "oid": str(existing["id"]),
                    },
                )
                return "updated" if changed else "unchanged"

            oid = str(uuid4())
            is_hist = lifecycle in {"AWARDED", "NO_BID", "ARCHIVED", "EXPIRED", "CANCELLED"}
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.opportunities (
                      id, account_id, canonical_key, reference_number, best_title,
                      opportunity_type, raw_opportunity_type, lifecycle_status, raw_status,
                      is_active, is_historical, is_demo,
                      county_name, published_commodity, commodities, acreage,
                      legal_description_raw, township, range, section_summary, plss_key,
                      latitude, longitude, geometry_accuracy, offering_cycle,
                      bidding_start_at, bidding_end_at, application_deadline,
                      minimum_bid, winning_bid, official_detail_url, external_bid_url,
                      mineral_potential_score, acquisition_readiness_score, overall_priority_score,
                      priority_tier, score_explanation_json, review_status,
                      first_observed_at, last_observed_at, enrichment_status
                    ) VALUES (
                      CAST(:id AS uuid), :aid, :ck, :ref, :title,
                      :otype, :rawtype, :life, :rawlife,
                      :active, :hist, false,
                      :county, :commodity, CAST(:coms AS text[]), :acre,
                      :legal, :twp, :rng, :sec, :plss,
                      :lat, :lon, CASE WHEN :lat IS NOT NULL THEN 'COORDINATE' ELSE 'UNKNOWN' END, :cycle,
                      :bstart, :bend, :adead,
                      :minbid, :winbid, :url, :ebid,
                      :ms, :ascore, :os, :tier, CAST(:expl AS jsonb), 'OPEN',
                      :obs, :obs, 'pending'
                    )
                    """
                ),
                {
                    "id": oid,
                    "aid": account_id,
                    "ck": ckey,
                    "ref": rec.reference_number,
                    "title": rec.title,
                    "otype": otype,
                    "rawtype": rec.opportunity_type_raw,
                    "life": lifecycle,
                    "rawlife": rec.status_raw,
                    "active": not is_hist,
                    "hist": is_hist,
                    "county": rec.county_name,
                    "commodity": rec.commodity_raw,
                    "coms": coms,
                    "acre": rec.acreage,
                    "legal": legal,
                    "twp": plss.get("township"),
                    "rng": plss.get("range"),
                    "sec": plss.get("section"),
                    "plss": plss.get("plss_key"),
                    "lat": rec.latitude,
                    "lon": rec.longitude,
                    "cycle": rec.offering_cycle,
                    "bstart": rec.bidding_start_at,
                    "bend": rec.bidding_end_at,
                    "adead": rec.application_deadline,
                    "minbid": rec.minimum_bid,
                    "winbid": rec.winning_bid,
                    "url": rec.detail_url,
                    "ebid": rec.external_bid_url,
                    "ms": scores["mineral_potential_score"],
                    "ascore": scores["acquisition_readiness_score"],
                    "os": scores["overall_priority_score"],
                    "tier": scores["priority_tier"],
                    "expl": expl,
                    "obs": now,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.opportunity_observations (
                      opportunity_id, source_id, source_run_id, raw_artifact_id, source_record_key,
                      observed_at, raw_title, raw_reference_number, raw_status, normalized_status,
                      raw_opportunity_type, raw_commodity, raw_legal_description, acreage,
                      minimum_bid, winning_bid, application_deadline, bidding_start_at, bidding_end_at,
                      official_detail_url, external_bid_url, raw_payload_json, record_hash
                    ) VALUES (
                      CAST(:oid AS uuid), CAST(:sid AS uuid), CAST(:rid AS uuid), CAST(:aid AS uuid), :skey,
                      :obs, :title, :ref, :status, :nstatus,
                      :otype, :commodity, :legal, :acre,
                      :minbid, :winbid, :adead, :bstart, :bend,
                      :url, :ebid, CAST(:payload AS jsonb), :rhash
                    )
                    """
                ),
                {**obs_params, "oid": oid},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.opportunity_events (
                      opportunity_id, event_type, event_at, title, description
                    ) VALUES (
                      CAST(:oid AS uuid), 'FIRST_DISCOVERED', :obs, 'Ingested from SITLA source', :descr
                    )
                    """
                ),
                {"oid": oid, "obs": now, "descr": f"{source['source_key']} → {lifecycle}"},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.evidence_items (
                      opportunity_id, fact_key, fact_value_json, evidence_class,
                      source_id, source_url, source_record_key, extraction_method, confidence
                    ) VALUES (
                      CAST(:oid AS uuid), 'source_listing', CAST(:val AS jsonb), 'SITLA',
                      CAST(:sid AS uuid), :url, :skey, 'adapter_ingest', 0.9
                    )
                    """
                ),
                {
                    "oid": oid,
                    "val": _json({"value": rec.title, "lifecycle": lifecycle}),
                    "sid": str(source["id"]),
                    "url": rec.detail_url or source.get("listing_url"),
                    "skey": rec.source_record_key,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.score_snapshots (
                      opportunity_id, mineral_potential_score, acquisition_readiness_score,
                      risk_penalty, overall_priority_score, priority_tier, explanation_json
                    ) VALUES (
                      CAST(:oid AS uuid), :ms, :ascore, :risk, :os, :tier, CAST(:expl AS jsonb)
                    )
                    """
                ),
                {
                    "oid": oid,
                    "ms": scores["mineral_potential_score"],
                    "ascore": scores["acquisition_readiness_score"],
                    "risk": scores["risk_penalty"],
                    "os": scores["overall_priority_score"],
                    "tier": scores["priority_tier"],
                    "expl": expl,
                },
            )
            if rec.winning_bid is not None:
                conn.execute(
                    text(
                        """
                        INSERT INTO sitla_intel.bid_results (
                          opportunity_id, winning_bidder, winning_bid, bid_per_acre, outcome, source_url
                        ) VALUES (
                          CAST(:oid AS uuid), :bidder, :bid, :ppa, 'AWARDED', :url
                        )
                        """
                    ),
                    {
                        "oid": oid,
                        "bidder": rec.winning_bidder or "Unknown",
                        "bid": rec.winning_bid,
                        "ppa": float(rec.winning_bid) / float(rec.acreage or 1),
                        "url": rec.detail_url,
                    },
                )
        return "created"
    except Exception:
        log.exception("sitla upsert failed")
        return "failed"
