"""Source run orchestration: discover → fetch → parse → observe → upsert opportunities."""

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
from mining_os.tax_intel.adapters.registry import build_adapter
from mining_os.tax_intel.normalize import (
    canonical_key,
    extract_mineral_surveys,
    extract_patent_number,
    extract_plss,
    map_lifecycle,
    normalize_apn,
    record_hash,
)
from mining_os.tax_intel.scoring import compute_scores

log = logging.getLogger("mining_os.tax_intel.ingest")

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "data_files" / "tax_intel_artifacts"


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str)


def list_sources(enabled_only: bool = True) -> list[dict[str, Any]]:
    eng = get_engine()
    clause = "WHERE enabled = true" if enabled_only else ""
    with eng.connect() as conn:
        has_adapter = conn.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'tax_intel' AND table_name = 'source_registry'
                    AND column_name = 'adapter_class'
                )
                """
            )
        ).scalar()
        adapter_col = ", adapter_class" if has_adapter else ""
        rows = conn.execute(
            text(
                f"""
                SELECT id, source_key, name, state, county_fips, county_name,
                       parser_kind, publication_scope, enabled, manual_only,
                       listing_url, refresh_schedule, freshness_sla_hours,
                       health_status, configuration_json{adapter_col},
                       consecutive_failures, last_success_at, last_failure_at
                FROM tax_intel.source_registry
                {clause}
                ORDER BY state, county_name, name
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
        d.setdefault("adapter_class", (d.get("configuration_json") or {}).get("adapter_class"))
        out.append(d)
    return out


def run_source(
    source_key: str,
    *,
    account_id: int,
    trigger_type: str = "manual",
    enrich: bool = True,
) -> dict[str, Any]:
    """Run one source adapter for an account and upsert opportunities."""
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
                INSERT INTO tax_intel.source_runs (
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
            fname = f"{source_key}_{run_id[:8]}_{sha[:12]}.bin"
            storage = ARTIFACT_DIR / fname
            storage.write_bytes(artifact.content)
            artifact_id = str(uuid4())
            with eng.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.raw_artifacts (
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
            errors = adapter.validate(records)
            if errors:
                metrics.setdefault("validation_warnings", []).extend(errors[:20])
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
                    UPDATE tax_intel.source_runs
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
                    UPDATE tax_intel.source_registry
                    SET health_status = 'HEALTHY',
                        last_success_at = :ts,
                        consecutive_failures = 0,
                        updated_at = :ts
                    WHERE id = CAST(:sid AS uuid)
                    """
                ),
                {"ts": datetime.now(timezone.utc), "sid": str(source["id"])},
            )

        if enrich:
            try:
                from mining_os.tax_intel.enrichment import enrich_account_opportunities

                enrich_account_opportunities(account_id, limit=50)
            except Exception:
                log.exception("post-ingest enrichment failed")

        try:
            from mining_os.tax_intel.alerts import detect_watchlist_changes

            detect_watchlist_changes(account_id)
        except Exception:
            log.exception("watchlist change detection failed")

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
        log.exception("source run failed for %s", source_key)
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE tax_intel.source_runs
                    SET completed_at = :done, status = 'failed', error_message = :err,
                        records_created = :c, records_updated = :u,
                        records_unchanged = :un, records_failed = :f,
                        metrics_json = CAST(:m AS jsonb)
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "done": datetime.now(timezone.utc),
                    "err": str(e)[:2000],
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
                    UPDATE tax_intel.source_registry
                    SET health_status = 'FAILED',
                        last_failure_at = :ts,
                        consecutive_failures = consecutive_failures + 1,
                        updated_at = :ts
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
        cfg = src.get("configuration_json") or {}
        if cfg.get("skip_scheduled") and trigger_type == "scheduled":
            continue
        results.append(run_source(src["source_key"], account_id=account_id, trigger_type=trigger_type))
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "ran": len(results), "succeeded": ok, "results": results}


def ingest_csv_bytes(
    *,
    account_id: int,
    source_key: str,
    content: bytes,
    filename: str = "upload.csv",
) -> dict[str, Any]:
    """Admin CSV upload path — writes a temp fixture-style run via CsvTaxAdapter."""
    from mining_os.tax_intel.adapters.csv_adapter import CsvTaxAdapter
    from mining_os.tax_intel.adapters.base import SourceArtifact

    sources = [s for s in list_sources(enabled_only=False) if s["source_key"] == source_key]
    if not sources:
        return {"ok": False, "error": f"Unknown source_key={source_key}"}
    source = sources[0]
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"upload_{source_key}_{uuid4().hex[:10]}.csv"
    path.write_bytes(content)
    # Temporarily point listing at uploaded file
    source = {**source, "configuration_json": {**(source.get("configuration_json") or {}), "listing_url": str(path)}}
    adapter = CsvTaxAdapter(source)
    eng = get_engine()
    run_id = str(uuid4())
    started = datetime.now(timezone.utc)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tax_intel.source_runs (
                  id, source_id, run_type, started_at, status, trigger_type
                ) VALUES (
                  CAST(:id AS uuid), CAST(:sid AS uuid), 'MANUAL_UPLOAD', :started, 'running', 'manual'
                )
                """
            ),
            {"id": run_id, "sid": str(source["id"]), "started": started},
        )
    artifact = SourceArtifact(
        source_url=f"upload://{filename}",
        retrieved_at=started,
        media_type="text/csv",
        content=content,
        filename=filename,
    )
    artifact_id = str(uuid4())
    sha = hashlib.sha256(content).hexdigest()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tax_intel.raw_artifacts (
                  id, source_id, source_run_id, source_url, retrieved_at,
                  filename, media_type, storage_uri, sha256, byte_size, metadata_json
                ) VALUES (
                  CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:rid AS uuid),
                  :url, :ret, :fn, 'text/csv', :uri, :sha, :sz, '{}'::jsonb
                )
                """
            ),
            {
                "id": artifact_id,
                "sid": str(source["id"]),
                "rid": run_id,
                "url": artifact.source_url,
                "ret": started,
                "fn": filename,
                "uri": str(path),
                "sha": sha,
                "sz": len(content),
            },
        )
    created = updated = unchanged = failed = 0
    records = list(adapter.parse(artifact))
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
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE tax_intel.source_runs
                SET completed_at = :done, status = 'completed',
                    records_discovered = :d, records_created = :c,
                    records_updated = :u, records_unchanged = :un, records_failed = :f
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "done": datetime.now(timezone.utc),
                "d": len(records),
                "c": created,
                "u": updated,
                "un": unchanged,
                "f": failed,
                "id": run_id,
            },
        )
        conn.execute(
            text(
                """
                UPDATE tax_intel.source_registry
                SET health_status = 'HEALTHY', last_success_at = :ts,
                    consecutive_failures = 0, updated_at = :ts, enabled = true
                WHERE id = CAST(:sid AS uuid)
                """
            ),
            {"ts": datetime.now(timezone.utc), "sid": str(source["id"])},
        )
    return {
        "ok": True,
        "run_id": run_id,
        "records_discovered": len(records),
        "records_created": created,
        "records_updated": updated,
        "records_unchanged": unchanged,
        "records_failed": failed,
    }


def _upsert_record(
    eng,
    *,
    account_id: int,
    source: dict[str, Any],
    run_id: str,
    artifact_id: str,
    rec,
) -> str:
    try:
        now = datetime.now(timezone.utc)
        legal = rec.legal_description_raw
        plss = extract_plss(legal)
        ms_nums = list(rec.mineral_survey_numbers or []) or extract_mineral_surveys(legal)
        patent_no = rec.patent_number or extract_patent_number(legal)
        apn_n = normalize_apn(rec.apn_raw)
        ckey = canonical_key(rec.state, rec.county_name, rec.apn_raw, rec.source_record_key)
        lifecycle = map_lifecycle(rec.raw_status, source.get("publication_scope"))
        payload = {
            "apn": rec.apn_raw,
            "owner": rec.owner_raw,
            "legal": legal,
            "status": rec.raw_status,
            "amount_due": rec.amount_due,
            "minimum_bid": rec.minimum_bid,
            "sale_date": rec.sale_date.isoformat() if rec.sale_date else None,
        }
        rhash = record_hash(payload)
        patent_class = "UNKNOWN"
        patent_conf = 0.0
        if patent_no and ms_nums:
            patent_class = "PROBABLE"
            patent_conf = 0.65
        elif ms_nums or patent_no:
            patent_class = "POSSIBLE"
            patent_conf = 0.4

        score_inputs = {
            "clear_sale_stage": lifecycle in {"AUCTION_SCHEDULED", "SALE_ELIGIBLE", "TAX_DEED_ISSUED", "COUNTY_OR_TRUSTEE_HELD"},
            "bid_or_amount_known": rec.amount_due is not None or rec.minimum_bid is not None,
            "patent_probable": patent_class == "PROBABLE",
            "patent_possible": patent_class == "POSSIBLE",
            "commodity_evidence": bool(rec.commodities),
            "geometry_confirmed": rec.latitude is not None and rec.longitude is not None,
            "mineral_survey_coherence": bool(ms_nums),
        }
        scores = compute_scores(score_inputs)
        expl = _json(scores.get("explanation_json") or scores)

        with eng.begin() as conn:
            existing = conn.execute(
                text(
                    """
                    SELECT id, amount_due, sale_lifecycle_status
                    FROM tax_intel.tax_opportunities
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
                "owner": rec.owner_raw,
                "apn": rec.apn_raw,
                "napn": apn_n or rec.apn_raw,
                "legal": legal,
                "addr": rec.property_address,
                "status": rec.raw_status,
                "nstatus": lifecycle,
                "amt": rec.amount_due,
                "bid": rec.minimum_bid,
                "sale": rec.sale_date.date() if rec.sale_date else None,
                "payload": _json(rec.raw_payload),
                "rhash": rhash,
            }

            if existing:
                obs_id = str(uuid4())
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.tax_observations (
                          id, opportunity_id, source_id, source_run_id, raw_artifact_id,
                          source_record_key, observed_at, effective_date,
                          raw_owner_name, normalized_owner_name, raw_apn, normalized_apn,
                          raw_legal_description, property_address, raw_status, normalized_status,
                          amount_due, minimum_bid, sale_date, raw_payload_json, record_hash
                        ) VALUES (
                          CAST(:id AS uuid), CAST(:oid AS uuid), CAST(:sid AS uuid),
                          CAST(:rid AS uuid), CAST(:aid AS uuid),
                          :skey, :obs, CAST(:obs AS date),
                          :owner, :owner, :apn, :napn,
                          :legal, :addr, :status, :nstatus,
                          :amt, :bid, :sale, CAST(:payload AS jsonb), :rhash
                        )
                        """
                    ),
                    {**obs_params, "id": obs_id, "oid": str(existing["id"])},
                )
                prev_amt = existing.get("amount_due")
                prev_life = existing.get("sale_lifecycle_status")
                changed = (
                    (prev_amt is None and rec.amount_due is not None)
                    or (
                        prev_amt is not None
                        and rec.amount_due is not None
                        and float(prev_amt) != float(rec.amount_due)
                    )
                    or (prev_life != lifecycle)
                )
                conn.execute(
                    text(
                        """
                        UPDATE tax_intel.tax_opportunities SET
                          primary_apn = COALESCE(:apn, primary_apn),
                          best_name = COALESCE(:name, best_name),
                          property_address = COALESCE(:addr, property_address),
                          acreage = COALESCE(:acre, acreage),
                          latitude = COALESCE(:lat, latitude),
                          longitude = COALESCE(:lon, longitude),
                          plss_key = COALESCE(:plss, plss_key),
                          township = COALESCE(:twp, township),
                          range = COALESCE(:rng, range),
                          section = COALESCE(:sec, section),
                          sale_lifecycle_status = :life,
                          tax_delinquency_status = CASE
                            WHEN :life IN ('REDEEMED','WITHDRAWN','SOLD') THEN tax_delinquency_status
                            ELSE 'DELINQUENT'
                          END,
                          last_observed_at = :obs,
                          auction_start_at = COALESCE(:auction, auction_start_at),
                          amount_due = COALESCE(:amt, amount_due),
                          minimum_bid = COALESCE(:bid, minimum_bid),
                          patent_classification = CASE
                            WHEN patent_classification = 'CONFIRMED' THEN patent_classification
                            ELSE :pclass
                          END,
                          patent_confidence = GREATEST(patent_confidence, :pconf),
                          commodities = CASE WHEN :has_com THEN CAST(:com AS text[]) ELSE commodities END,
                          mineral_potential_score = :ms,
                          acquisition_readiness_score = :ascore,
                          overall_priority_score = :os,
                          priority_tier = :tier,
                          score_explanation_json = CAST(:expl AS jsonb),
                          enrichment_status = 'pending',
                          updated_at = :obs
                        WHERE id = CAST(:oid AS uuid)
                        """
                    ),
                    {
                        "apn": apn_n or rec.apn_raw,
                        "name": rec.best_name,
                        "addr": rec.property_address,
                        "acre": rec.acreage,
                        "lat": rec.latitude,
                        "lon": rec.longitude,
                        "plss": plss.get("plss_key"),
                        "twp": plss.get("township"),
                        "rng": plss.get("range"),
                        "sec": plss.get("section"),
                        "life": lifecycle,
                        "obs": now,
                        "auction": rec.sale_date,
                        "amt": rec.amount_due,
                        "bid": rec.minimum_bid,
                        "pclass": patent_class,
                        "pconf": patent_conf,
                        "has_com": bool(rec.commodities),
                        "com": rec.commodities or [],
                        "ms": scores["mineral_potential_score"],
                        "ascore": scores["acquisition_readiness_score"],
                        "os": scores["overall_priority_score"],
                        "tier": scores["priority_tier"],
                        "expl": expl,
                        "oid": str(existing["id"]),
                    },
                )
                if ms_nums or patent_no:
                    _ensure_patent_stub(
                        conn, str(existing["id"]), rec.state, rec.county_name, patent_no, ms_nums, legal, plss
                    )
                return "updated" if changed else "unchanged"

            oid = str(uuid4())
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.tax_opportunities (
                      id, account_id, canonical_key, state, county_fips, county_name,
                      primary_apn, best_name, property_address, acreage,
                      latitude, longitude, geometry_accuracy,
                      plss_key, township, range, section,
                      tax_delinquency_status, sale_lifecycle_status,
                      first_observed_at, last_observed_at, auction_start_at,
                      amount_due, minimum_bid, publication_scope,
                      patent_classification, patent_confidence,
                      mineral_signal, mineral_potential_score, acquisition_readiness_score,
                      overall_priority_score, priority_tier, score_explanation_json,
                      review_status, is_demo, is_active, commodities, enrichment_status
                    ) VALUES (
                      CAST(:id AS uuid), :aid, :ck, :state, :fips, :county,
                      :apn, :name, :addr, :acre,
                      :lat, :lon, CASE WHEN :lat IS NOT NULL THEN 'COORDINATE' ELSE 'UNKNOWN' END,
                      :plss, :twp, :rng, :sec,
                      'DELINQUENT', :life,
                      :obs, :obs, :auction,
                      :amt, :bid, :scope,
                      :pclass, :pconf,
                      CASE WHEN :has_com THEN 'MEDIUM' ELSE 'UNKNOWN' END,
                      :ms, :ascore, :os, :tier, CAST(:expl AS jsonb),
                      'OPEN', false, true, CAST(:com AS text[]), 'pending'
                    )
                    """
                ),
                {
                    "id": oid,
                    "aid": account_id,
                    "ck": ckey,
                    "state": rec.state.upper(),
                    "fips": source.get("county_fips"),
                    "county": rec.county_name,
                    "apn": apn_n or rec.apn_raw,
                    "name": rec.best_name,
                    "addr": rec.property_address,
                    "acre": rec.acreage,
                    "lat": rec.latitude,
                    "lon": rec.longitude,
                    "plss": plss.get("plss_key"),
                    "twp": plss.get("township"),
                    "rng": plss.get("range"),
                    "sec": plss.get("section"),
                    "life": lifecycle,
                    "obs": now,
                    "auction": rec.sale_date,
                    "amt": rec.amount_due,
                    "bid": rec.minimum_bid,
                    "scope": source.get("publication_scope") or "UNKNOWN",
                    "pclass": patent_class,
                    "pconf": patent_conf,
                    "has_com": bool(rec.commodities),
                    "ms": scores["mineral_potential_score"],
                    "ascore": scores["acquisition_readiness_score"],
                    "os": scores["overall_priority_score"],
                    "tier": scores["priority_tier"],
                    "expl": expl,
                    "com": rec.commodities or [],
                },
            )
            if apn_n or rec.apn_raw:
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.parcel_identifiers (
                          opportunity_id, source_id, identifier_type, raw_value, normalized_value, is_primary
                        ) VALUES (
                          CAST(:oid AS uuid), CAST(:sid AS uuid), 'APN', :raw, :norm, true
                        )
                        """
                    ),
                    {
                        "oid": oid,
                        "sid": str(source["id"]),
                        "raw": rec.apn_raw or apn_n,
                        "norm": apn_n or rec.apn_raw,
                    },
                )
            obs_id = str(uuid4())
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.tax_observations (
                      id, opportunity_id, source_id, source_run_id, raw_artifact_id,
                      source_record_key, observed_at, effective_date,
                      raw_owner_name, normalized_owner_name, raw_apn, normalized_apn,
                      raw_legal_description, property_address, raw_status, normalized_status,
                      amount_due, minimum_bid, sale_date, raw_payload_json, record_hash
                    ) VALUES (
                      CAST(:id AS uuid), CAST(:oid AS uuid), CAST(:sid AS uuid),
                      CAST(:rid AS uuid), CAST(:aid AS uuid),
                      :skey, :obs, CAST(:obs AS date),
                      :owner, :owner, :apn, :napn,
                      :legal, :addr, :status, :nstatus,
                      :amt, :bid, :sale, CAST(:payload AS jsonb), :rhash
                    )
                    """
                ),
                {**obs_params, "id": obs_id, "oid": oid},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.tax_events (
                      opportunity_id, event_type, event_at, source_observation_id, title, description
                    ) VALUES (
                      CAST(:oid AS uuid), 'DISCOVERED', :obs, CAST(:obsid AS uuid),
                      'Ingested from source', :descr
                    )
                    """
                ),
                {
                    "oid": oid,
                    "obs": now,
                    "obsid": obs_id,
                    "descr": f"{source['source_key']} → {lifecycle}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.evidence_items (
                      opportunity_id, fact_key, fact_value_json, evidence_class,
                      source_id, source_url, source_record_key, extraction_method, confidence
                    ) VALUES (
                      CAST(:oid AS uuid), 'source_listing', CAST(:val AS jsonb), 'TAX',
                      CAST(:sid AS uuid), :url, :skey, 'adapter_ingest', 0.9
                    )
                    """
                ),
                {
                    "oid": oid,
                    "val": _json({"value": rec.best_name or rec.apn_raw, "lifecycle": lifecycle}),
                    "sid": str(source["id"]),
                    "url": source.get("listing_url"),
                    "skey": rec.source_record_key,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.score_snapshots (
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
                    "risk": scores.get("risk_penalty", 0),
                    "os": scores["overall_priority_score"],
                    "tier": scores["priority_tier"],
                    "expl": expl,
                },
            )
            if ms_nums or patent_no:
                _ensure_patent_stub(conn, oid, rec.state, rec.county_name, patent_no, ms_nums, legal, plss)
                existing_task = conn.execute(
                    text(
                        """
                        SELECT id FROM tax_intel.review_tasks
                        WHERE opportunity_id = CAST(:oid AS uuid)
                          AND task_type = 'MANUAL_GLO_LOOKUP' AND status = 'OPEN'
                        LIMIT 1
                        """
                    ),
                    {"oid": oid},
                ).first()
                if not existing_task and patent_class in {"POSSIBLE", "PROBABLE"}:
                    conn.execute(
                        text(
                            """
                            INSERT INTO tax_intel.review_tasks (
                              opportunity_id, task_type, priority, status, title, instructions,
                              input_context_json
                            ) VALUES (
                              CAST(:oid AS uuid), 'MANUAL_GLO_LOOKUP', 40, 'OPEN',
                              'Confirm GLO patent / Mineral Survey',
                              'Verify patent number and Mineral Survey against BLM GLO records. Do not treat as confirmed until document evidence is attached.',
                              CAST(:ctx AS jsonb)
                            )
                            """
                        ),
                        {
                            "oid": oid,
                            "ctx": _json(
                                {
                                    "patent_number": patent_no,
                                    "mineral_survey_numbers": ms_nums,
                                    "legal": legal,
                                    "glo_url": "https://glorecords.blm.gov/",
                                }
                            ),
                        },
                    )
        return "created"
    except Exception:
        log.exception("upsert record failed")
        return "failed"


def _ensure_patent_stub(
    conn,
    opportunity_id: str,
    state: str,
    county: str,
    patent_no: str | None,
    ms_nums: list[str],
    legal: str | None,
    plss: dict[str, Any],
) -> None:
    if not (patent_no or ms_nums):
        return
    patent_id = str(uuid4())
    conn.execute(
        text(
            """
            INSERT INTO tax_intel.patent_records (
              id, patent_number, accession_number, document_type, state, county_name,
              legal_description, mineral_survey_numbers, township, range, section,
              document_url, raw_payload_json
            ) VALUES (
              CAST(:id AS uuid), :pno, :pno, 'MINERAL_PATENT', :state, :county,
              :legal, CAST(:ms AS text[]), :twp, :rng, :sec,
              'https://glorecords.blm.gov/', CAST(:raw AS jsonb)
            )
            """
        ),
        {
            "id": patent_id,
            "pno": patent_no,
            "state": state.upper(),
            "county": county,
            "legal": legal,
            "ms": ms_nums,
            "twp": plss.get("township"),
            "rng": plss.get("range"),
            "sec": plss.get("section"),
            "raw": _json({"inferred_from_legal": True}),
        },
    )
    conf = 0.65 if patent_no and ms_nums else 0.4
    conn.execute(
        text(
            """
            INSERT INTO tax_intel.opportunity_patent_matches (
              opportunity_id, patent_record_id, match_status, match_confidence, match_method,
              mineral_survey_score, plss_score, evidence_summary_json
            ) VALUES (
              CAST(:oid AS uuid), CAST(:pid AS uuid), :status, :conf, 'LEGAL_PARSE',
              :ms_score, :plss_score, CAST(:summary AS jsonb)
            )
            """
        ),
        {
            "oid": opportunity_id,
            "pid": patent_id,
            "status": "PROBABLE" if conf >= 0.65 else "POSSIBLE",
            "conf": conf,
            "ms_score": 40.0 if ms_nums else 0.0,
            "plss_score": 15.0 if plss.get("plss_key") else 0.0,
            "summary": _json(
                {
                    "patent_number": patent_no,
                    "mineral_survey_numbers": ms_nums,
                    "glo_url": "https://glorecords.blm.gov/",
                }
            ),
        },
    )
    for ms in ms_nums:
        conn.execute(
            text(
                """
                INSERT INTO tax_intel.mineral_surveys (
                  state, survey_number, survey_number_normalized, notes, source_url
                ) VALUES (
                  :state, :ms, :msn, 'Inferred from tax-sale legal description',
                  'https://glorecords.blm.gov/'
                )
                ON CONFLICT (state, survey_number_normalized) DO NOTHING
                """
            ),
            {"state": state.upper(), "ms": ms, "msn": ms},
        )
