"""Tax Opportunity query/service layer."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.tax_intel.demo_seed import ensure_demo_seed, schema_ready
from mining_os.tax_intel.enums import ACTIVE_LIFECYCLE

log = logging.getLogger("mining_os.tax_intel.opportunity_service")


def _row(d: Any) -> dict[str, Any]:
    out = dict(d)
    for k, v in list(out.items()):
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, list):
            out[k] = list(v)
    return out


def disabled_payload(message: str = "Tax Sales module is disabled.") -> dict[str, Any]:
    return {"ok": False, "error": message, "enabled": False}


def ensure_ready(account_id: int) -> dict[str, Any] | None:
    if not schema_ready():
        return {
            "ok": False,
            "error": "Tax Sales schema is not installed. Run: python -m mining_os.pipelines.run_all --init-db",
            "enabled": True,
        }
    try:
        ensure_demo_seed(account_id)
    except Exception as e:
        log.exception("demo seed failed")
        return {"ok": False, "error": f"Demo seed failed: {e}", "enabled": True}
    return None


def get_summary(account_id: int) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE is_active AND sale_lifecycle_status = ANY(:active)) AS active_published,
                  COUNT(*) FILTER (WHERE is_active AND sale_lifecycle_status = 'AUCTION_SCHEDULED') AS auction_scheduled,
                  COUNT(*) FILTER (
                    WHERE is_active
                      AND auction_start_at IS NOT NULL
                      AND auction_start_at <= :soon
                      AND auction_start_at >= :now
                  ) AS auction_within_30_days,
                  COUNT(*) FILTER (WHERE is_active AND patent_classification = 'CONFIRMED') AS confirmed_patents,
                  COUNT(*) FILTER (WHERE is_active AND patent_classification = 'PROBABLE') AS probable_patents,
                  COUNT(*) FILTER (WHERE is_active AND mineral_signal = 'HIGH') AS high_mineral,
                  COUNT(*) FILTER (WHERE is_active AND acquisition_readiness_score >= 70) AS high_acquisition,
                  COUNT(*) FILTER (WHERE is_active AND priority_tier = 'A') AS priority_a,
                  COUNT(*) FILTER (WHERE is_active AND last_observed_at >= :week) AS new_since_week,
                  COUNT(*) FILTER (
                    WHERE sale_lifecycle_status IN ('REDEEMED', 'WITHDRAWN')
                      AND last_observed_at >= :week
                  ) AS redeemed_withdrawn_week,
                  COUNT(*) FILTER (WHERE review_status = 'OPEN') AS needing_review
                FROM tax_intel.tax_opportunities
                WHERE account_id = :aid
                """
            ),
            {
                "aid": account_id,
                "active": list(ACTIVE_LIFECYCLE),
                "soon": now + timedelta(days=30),
                "now": now,
                "week": now - timedelta(days=7),
            },
        ).mappings().first()

        cov = conn.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE enabled) AS enabled_counties,
                  COUNT(*) FILTER (WHERE enabled AND health_status = 'HEALTHY') AS healthy_counties,
                  COUNT(*) FILTER (WHERE enabled AND health_status IN ('FAILED', 'STALE', 'DEGRADED')) AS failed_or_stale
                FROM tax_intel.source_registry
                WHERE source_category = 'TAX'
                """
            )
        ).mappings().first()

    summary = _row(rows or {})
    coverage = _row(cov or {})
    cards = [
        {"key": "active_published", "label": "Active published", "value": summary.get("active_published") or 0, "filter": {"active_only": True}},
        {"key": "auction_scheduled", "label": "Auction scheduled", "value": summary.get("auction_scheduled") or 0, "filter": {"status": "AUCTION_SCHEDULED"}},
        {"key": "auction_within_30_days", "label": "Auction within 30 days", "value": summary.get("auction_within_30_days") or 0, "filter": {"auction_within_days": 30}},
        {"key": "confirmed_patents", "label": "Confirmed patented", "value": summary.get("confirmed_patents") or 0, "filter": {"patent_classification": "CONFIRMED"}},
        {"key": "probable_patents", "label": "Probable patented", "value": summary.get("probable_patents") or 0, "filter": {"patent_classification": "PROBABLE"}},
        {"key": "high_mineral", "label": "High mineral signal", "value": summary.get("high_mineral") or 0, "filter": {"mineral_signal": "HIGH"}},
        {"key": "high_acquisition", "label": "High acquisition readiness", "value": summary.get("high_acquisition") or 0, "filter": {"min_score": 70}},
        {"key": "priority_a", "label": "Priority tier A", "value": summary.get("priority_a") or 0, "filter": {"priority_tier": "A"}},
        {"key": "new_since_week", "label": "Updated this week", "value": summary.get("new_since_week") or 0, "filter": {"updated_within_days": 7}},
        {"key": "redeemed_withdrawn_week", "label": "Redeemed/withdrawn this week", "value": summary.get("redeemed_withdrawn_week") or 0, "filter": {"status": "REDEEMED"}},
        {"key": "healthy_counties", "label": "Healthy / enabled counties", "value": f"{coverage.get('healthy_counties') or 0}/{coverage.get('enabled_counties') or 0}", "filter": None},
        {"key": "failed_or_stale", "label": "Failed or stale sources", "value": coverage.get("failed_or_stale") or 0, "filter": None},
        {"key": "needing_review", "label": "Needs review", "value": summary.get("needing_review") or 0, "filter": {"review_status": "OPEN"}},
    ]
    return {
        "ok": True,
        "error": None,
        "enabled": True,
        "coverage_banner": {
            "message": "All publicly available records from enabled and healthy sources.",
            "detail": "Publication scope varies by county — sale-stage lists are not complete unpaid-tax coverage.",
            "enabled_counties": coverage.get("enabled_counties") or 0,
            "healthy_counties": coverage.get("healthy_counties") or 0,
            "failed_or_stale": coverage.get("failed_or_stale") or 0,
        },
        "cards": cards,
        "disclaimer": (
            "Patent status and tax-sale status do not establish current mineral ownership. "
            "Current deeds, reservations, severances, liens, and title history require separate review."
        ),
    }


def list_opportunities(
    account_id: int,
    *,
    state: str | None = None,
    county: str | None = None,
    status: str | None = None,
    patent_classification: str | None = None,
    mineral_signal: str | None = None,
    priority_tier: str | None = None,
    review_status: str | None = None,
    search: str | None = None,
    min_score: float | None = None,
    auction_within_days: int | None = None,
    active_only: bool = True,
    watchlisted: bool | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "overall_priority_score",
    order: str = "desc",
) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err

    allowed_sort = {
        "overall_priority_score",
        "auction_start_at",
        "amount_due",
        "minimum_bid",
        "county_name",
        "state",
        "patent_confidence",
        "last_observed_at",
        "best_name",
        "priority_tier",
    }
    if sort not in allowed_sort:
        sort = "overall_priority_score"
    order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 50)))
    offset = (page - 1) * page_size

    clauses = ["o.account_id = :aid"]
    params: dict[str, Any] = {"aid": account_id, "limit": page_size, "offset": offset}
    if active_only:
        clauses.append("o.is_active = true")
        clauses.append("o.sale_lifecycle_status = ANY(:active)")
        params["active"] = list(ACTIVE_LIFECYCLE)
    if state:
        clauses.append("o.state = :state")
        params["state"] = state.strip().upper()[:2]
    if county:
        clauses.append("o.county_name ILIKE :county")
        params["county"] = f"%{county.strip()}%"
    if status:
        clauses.append("o.sale_lifecycle_status = :status")
        params["status"] = status.strip().upper()
    if patent_classification:
        clauses.append("o.patent_classification = :patent")
        params["patent"] = patent_classification.strip().upper()
    if mineral_signal:
        clauses.append("o.mineral_signal = :msig")
        params["msig"] = mineral_signal.strip().upper()
    if priority_tier:
        clauses.append("o.priority_tier = :tier")
        params["tier"] = priority_tier.strip().upper()[:1]
    if review_status:
        clauses.append("o.review_status = :rev")
        params["rev"] = review_status.strip().upper()
    if min_score is not None:
        clauses.append("o.overall_priority_score >= :min_score")
        params["min_score"] = float(min_score)
    if auction_within_days is not None:
        clauses.append("o.auction_start_at IS NOT NULL")
        clauses.append("o.auction_start_at >= now()")
        clauses.append("o.auction_start_at <= now() + make_interval(days => :adays)")
        params["adays"] = int(auction_within_days)
    if search and search.strip():
        clauses.append(
            """(
              o.best_name ILIKE :q OR o.primary_apn ILIKE :q OR o.plss_key ILIKE :q
              OR o.property_address ILIKE :q OR o.county_name ILIKE :q
              OR EXISTS (
                SELECT 1 FROM unnest(o.commodities) c WHERE c ILIKE :q
              )
            )"""
        )
        params["q"] = f"%{search.strip()}%"
    if watchlisted is True:
        clauses.append(
            "EXISTS (SELECT 1 FROM tax_intel.watchlists w WHERE w.opportunity_id = o.id AND w.account_id = :aid)"
        )
    elif watchlisted is False:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM tax_intel.watchlists w WHERE w.opportunity_id = o.id AND w.account_id = :aid)"
        )

    where = " AND ".join(clauses)
    eng = get_engine()
    with eng.begin() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM tax_intel.tax_opportunities o WHERE {where}"),
            params,
        ).scalar()
        rows = conn.execute(
            text(
                f"""
                SELECT
                  o.id::text AS id,
                  o.state, o.county_name, o.county_fips, o.primary_apn, o.best_name,
                  o.sale_lifecycle_status, o.tax_delinquency_status,
                  o.auction_start_at, o.amount_due, o.minimum_bid, o.years_delinquent,
                  o.acreage, o.patent_classification, o.patent_confidence,
                  o.mineral_signal, o.commodities, o.access_status,
                  o.surface_mineral_unity_status, o.data_completeness_score,
                  o.source_freshness_score, o.mineral_potential_score,
                  o.acquisition_readiness_score, o.overall_priority_score, o.priority_tier,
                  o.review_status, o.last_observed_at, o.latitude, o.longitude,
                  o.publication_scope, o.plss_key, o.is_demo, o.geometry_accuracy,
                  EXISTS (
                    SELECT 1 FROM tax_intel.watchlists w
                    WHERE w.opportunity_id = o.id AND w.account_id = :aid
                  ) AS watchlisted,
                  (
                    SELECT COUNT(*) FROM tax_intel.claim_context c
                    WHERE c.opportunity_id = o.id AND c.claim_status = 'ACTIVE'
                  ) AS nearby_active_claims,
                  (
                    SELECT COUNT(*) FROM tax_intel.mineral_evidence m
                    WHERE m.opportunity_id = o.id AND m.inside_parcel
                  ) AS mines_on_parcel
                FROM tax_intel.tax_opportunities o
                WHERE {where}
                ORDER BY o.{sort} {order_sql} NULLS LAST, o.best_name ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()

    return {
        "ok": True,
        "error": None,
        "page": page,
        "page_size": page_size,
        "total": int(total or 0),
        "items": [_row(r) for r in rows],
    }


def get_opportunity(account_id: int, opportunity_id: str) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        opp = conn.execute(
            text(
                """
                SELECT o.*, o.id::text AS id,
                  EXISTS (
                    SELECT 1 FROM tax_intel.watchlists w
                    WHERE w.opportunity_id = o.id AND w.account_id = :aid
                  ) AS watchlisted
                FROM tax_intel.tax_opportunities o
                WHERE o.id = CAST(:id AS uuid) AND o.account_id = :aid
                """
            ),
            {"id": opportunity_id, "aid": account_id},
        ).mappings().first()
        if not opp:
            return {"ok": False, "error": "Opportunity not found"}

        events = conn.execute(
            text(
                """
                SELECT id::text AS id, event_type, event_at, title, description, amount
                FROM tax_intel.tax_events
                WHERE opportunity_id = CAST(:id AS uuid)
                ORDER BY event_at NULLS LAST, created_at
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        patents = conn.execute(
            text(
                """
                SELECT m.id::text AS id, m.match_status, m.match_confidence, m.match_method,
                       m.evidence_summary_json, p.patent_number, p.accession_number,
                       p.patentee_name, p.mineral_survey_numbers, p.claim_names,
                       p.document_url, p.legal_description
                FROM tax_intel.opportunity_patent_matches m
                LEFT JOIN tax_intel.patent_records p ON p.id = m.patent_record_id
                WHERE m.opportunity_id = CAST(:id AS uuid)
                ORDER BY m.match_confidence DESC
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        minerals = conn.execute(
            text(
                """
                SELECT id::text AS id, evidence_type, mine_name, prospect_name,
                       commodity_normalized, production_status, distance_meters,
                       inside_parcel, confidence, source_url
                FROM tax_intel.mineral_evidence
                WHERE opportunity_id = CAST(:id AS uuid)
                ORDER BY inside_parcel DESC, confidence DESC
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        claims = conn.execute(
            text(
                """
                SELECT id::text AS id, mlrs_serial_number, claim_name, claim_status,
                       claim_type, distance_meters, inside_parcel
                FROM tax_intel.claim_context
                WHERE opportunity_id = CAST(:id AS uuid)
                ORDER BY distance_meters NULLS LAST
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        evidence = conn.execute(
            text(
                """
                SELECT e.id::text AS id, e.fact_key, e.fact_value_json, e.evidence_class,
                       e.source_url, e.extraction_method, e.confidence, e.is_primary,
                       e.is_contradictory, e.analyst_verified, e.created_at,
                       s.name AS source_name
                FROM tax_intel.evidence_items e
                LEFT JOIN tax_intel.source_registry s ON s.id = e.source_id
                WHERE e.opportunity_id = CAST(:id AS uuid)
                ORDER BY e.is_primary DESC, e.confidence DESC, e.created_at DESC
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        observations = conn.execute(
            text(
                """
                SELECT id::text AS id, observed_at, effective_date, raw_owner_name, raw_apn,
                       raw_legal_description, raw_status, normalized_status, amount_due,
                       minimum_bid, years_delinquent, sale_date
                FROM tax_intel.tax_observations
                WHERE opportunity_id = CAST(:id AS uuid)
                ORDER BY observed_at DESC
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        reviews = conn.execute(
            text(
                """
                SELECT id::text AS id, task_type, priority, status, title, instructions,
                       decision, decision_notes, created_at, due_at
                FROM tax_intel.review_tasks
                WHERE opportunity_id = CAST(:id AS uuid)
                ORDER BY created_at DESC
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        score = conn.execute(
            text(
                """
                SELECT score_version, calculated_at, mineral_potential_score,
                       acquisition_readiness_score, risk_penalty, overall_priority_score,
                       priority_tier, explanation_json
                FROM tax_intel.score_snapshots
                WHERE opportunity_id = CAST(:id AS uuid)
                ORDER BY calculated_at DESC
                LIMIT 1
                """
            ),
            {"id": opportunity_id},
        ).mappings().first()
        links = conn.execute(
            text(
                """
                SELECT id::text AS id, area_of_focus_id, link_type, confidence, created_at
                FROM tax_intel.opportunity_target_links
                WHERE opportunity_id = CAST(:id AS uuid)
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()

    detail = _row(opp)
    detail["id"] = opportunity_id

    return {
        "ok": True,
        "error": None,
        "opportunity": detail,
        "timeline": [_row(r) for r in events],
        "patent_matches": [_row(r) for r in patents],
        "mineral_evidence": [_row(r) for r in minerals],
        "claim_context": [_row(r) for r in claims],
        "evidence_ledger": [_row(r) for r in evidence],
        "observations": [_row(r) for r in observations],
        "review_tasks": [_row(r) for r in reviews],
        "score": _row(score) if score else detail.get("score_explanation_json"),
        "target_links": [_row(r) for r in links],
        "disclaimer": (
            "Patent status and tax-sale status do not establish current mineral ownership. "
            "Current deeds, reservations, severances, liens, and title history require separate review."
        ),
    }


def get_coverage(account_id: int) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        sources = conn.execute(
            text(
                """
                SELECT
                  s.id::text AS id, s.source_key, s.name, s.state, s.county_fips, s.county_name,
                  s.publication_scope, s.enabled, s.manual_only, s.health_status,
                  s.last_success_at, s.last_failure_at, s.freshness_sla_hours,
                  s.listing_url, s.parser_kind, s.notes,
                  (
                    SELECT COUNT(*) FROM tax_intel.tax_opportunities o
                    WHERE o.account_id = :aid
                      AND o.state = s.state
                      AND o.county_name = s.county_name
                  ) AS record_count
                FROM tax_intel.source_registry s
                WHERE s.source_category = 'TAX'
                ORDER BY s.state, s.county_name
                """
            ),
            {"aid": account_id},
        ).mappings().all()
        open_reviews = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM tax_intel.review_tasks t
                JOIN tax_intel.tax_opportunities o ON o.id = t.opportunity_id
                WHERE o.account_id = :aid AND t.status = 'OPEN'
                """
            ),
            {"aid": account_id},
        ).scalar()

    items = [_row(r) for r in sources]
    return {
        "ok": True,
        "error": None,
        "jurisdictions": items,
        "metrics": {
            "enabled_jurisdictions": sum(1 for i in items if i.get("enabled")),
            "healthy_jurisdictions": sum(1 for i in items if i.get("health_status") == "HEALTHY"),
            "stale_jurisdictions": sum(1 for i in items if i.get("health_status") == "STALE"),
            "failed_jurisdictions": sum(1 for i in items if i.get("health_status") == "FAILED"),
            "manual_jurisdictions": sum(1 for i in items if i.get("manual_only") or i.get("health_status") == "MANUAL"),
            "open_review_tasks": int(open_reviews or 0),
        },
        "coverage_language": "All publicly available records from enabled and healthy sources.",
    }


def get_map_features(
    account_id: int,
    *,
    state: str | None = None,
    patent_classification: str | None = None,
    min_score: float | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    clauses = [
        "account_id = :aid",
        "is_active = true",
        "latitude IS NOT NULL",
        "longitude IS NOT NULL",
    ]
    params: dict[str, Any] = {"aid": account_id, "limit": max(1, min(2000, int(limit)))}
    if state:
        clauses.append("state = :state")
        params["state"] = state.strip().upper()[:2]
    if patent_classification:
        clauses.append("patent_classification = :patent")
        params["patent"] = patent_classification.strip().upper()
    if min_score is not None:
        clauses.append("overall_priority_score >= :min_score")
        params["min_score"] = float(min_score)
    where = " AND ".join(clauses)
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id::text AS id, best_name, state, county_name, primary_apn,
                       latitude, longitude, patent_classification, priority_tier,
                       overall_priority_score, sale_lifecycle_status, mineral_signal
                FROM tax_intel.tax_opportunities
                WHERE {where}
                ORDER BY overall_priority_score DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

    features = []
    for r in rows:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r["longitude"]), float(r["latitude"])],
                },
                "properties": {
                    "id": r["id"],
                    "name": r["best_name"],
                    "state": r["state"],
                    "county": r["county_name"],
                    "apn": r["primary_apn"],
                    "patent_classification": r["patent_classification"],
                    "priority_tier": r["priority_tier"],
                    "score": float(r["overall_priority_score"] or 0),
                    "status": r["sale_lifecycle_status"],
                    "mineral_signal": r["mineral_signal"],
                },
            }
        )
    return {
        "ok": True,
        "error": None,
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
    }


def set_watch(account_id: int, opportunity_id: str, *, watch: bool, user_id: int | None = None) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM tax_intel.tax_opportunities
                WHERE id = CAST(:id AS uuid) AND account_id = :aid
                """
            ),
            {"id": opportunity_id, "aid": account_id},
        ).scalar()
        if not exists:
            return {"ok": False, "error": "Opportunity not found"}
        if watch:
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.watchlists (account_id, user_id, opportunity_id, watch_reason)
                    VALUES (:aid, :uid, CAST(:oid AS uuid), 'user')
                    ON CONFLICT (account_id, opportunity_id) DO NOTHING
                    """
                ),
                {"aid": account_id, "uid": user_id, "oid": opportunity_id},
            )
        else:
            conn.execute(
                text(
                    """
                    DELETE FROM tax_intel.watchlists
                    WHERE account_id = :aid AND opportunity_id = CAST(:oid AS uuid)
                    """
                ),
                {"aid": account_id, "oid": opportunity_id},
            )
        count = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM tax_intel.watchlists
                WHERE opportunity_id = CAST(:oid AS uuid)
                """
            ),
            {"oid": opportunity_id},
        ).scalar()
        conn.execute(
            text(
                """
                UPDATE tax_intel.tax_opportunities
                SET watch_count = :c, updated_at = now()
                WHERE id = CAST(:oid AS uuid)
                """
            ),
            {"c": int(count or 0), "oid": opportunity_id},
        )
    return {"ok": True, "error": None, "watchlisted": watch}


def list_review_tasks(account_id: int) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.id::text AS id, t.task_type, t.priority, t.status, t.title,
                       t.instructions, t.created_at, t.due_at,
                       o.id::text AS opportunity_id, o.best_name, o.state, o.county_name,
                       o.priority_tier, o.patent_classification
                FROM tax_intel.review_tasks t
                JOIN tax_intel.tax_opportunities o ON o.id = t.opportunity_id
                WHERE o.account_id = :aid AND t.status = 'OPEN'
                ORDER BY t.priority DESC, t.created_at ASC
                LIMIT 200
                """
            ),
            {"aid": account_id},
        ).mappings().all()
    return {"ok": True, "error": None, "items": [_row(r) for r in rows]}
