"""Tax Opportunity query/service layer."""

from __future__ import annotations

import json
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


def build_source_listing(
    *,
    source_row: dict[str, Any] | None,
    auction_start_at: Any = None,
    record_detail_url: str | None = None,
    is_demo: bool = False,
) -> dict[str, Any]:
    """Normalize source listing payload for opportunity detail."""
    name = (source_row or {}).get("name")
    listing_url = (source_row or {}).get("listing_url")
    source_key = (source_row or {}).get("source_key")
    auction = auction_start_at
    if hasattr(auction, "isoformat"):
        auction = auction.isoformat()
    detail = (record_detail_url or "").strip() or None
    listing = (listing_url or "").strip() or None
    open_url = detail or listing
    # Demo/fixture rows must not present a county homepage as proof of this parcel/date.
    if is_demo:
        open_url = detail  # only a per-record URL if somehow present
    return {
        "name": name,
        "source_key": source_key,
        "listing_url": listing,
        "record_detail_url": detail,
        "open_url": open_url,
        "auction_start_at": auction,
        "is_demo": is_demo,
        "verified_publication": not is_demo and bool(open_url),
    }

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
        _mark_fixture_opportunities_as_demo(account_id)
    except Exception as e:
        log.exception("demo seed failed")
        return {"ok": False, "error": f"Demo seed failed: {e}", "enabled": True}
    return None


def _mark_fixture_opportunities_as_demo(account_id: int) -> None:
    """Packaged fixture / demo rows must not look like live county publications."""
    eng = get_engine()
    with eng.begin() as conn:
        # Never demote rows that already have a live HTTP observation from a validated feed.
        conn.execute(
            text(
                """
                UPDATE tax_intel.tax_opportunities o
                SET is_demo = false, updated_at = now()
                WHERE o.account_id = :aid
                  AND o.is_demo = true
                  AND EXISTS (
                    SELECT 1 FROM tax_intel.tax_observations obs
                    JOIN tax_intel.raw_artifacts a ON a.id = obs.raw_artifact_id
                    JOIN tax_intel.source_registry s ON s.id = obs.source_id
                    WHERE obs.opportunity_id = o.id
                      AND COALESCE(a.source_url, '') ~* '^https?://'
                      AND COALESCE(a.source_url, '') NOT LIKE 'fixture://%'
                      AND (
                        COALESCE((s.configuration_json->>'allow_live_pdf')::boolean, false)
                        OR COALESCE((s.configuration_json->>'allow_live_html')::boolean, false)
                        OR COALESCE(s.configuration_json->>'live_status', '') = 'validated'
                      )
                  )
                """
            ),
            {"aid": account_id},
        )
        conn.execute(
            text(
                """
                UPDATE tax_intel.tax_opportunities o
                SET is_demo = true, updated_at = now()
                WHERE o.account_id = :aid
                  AND o.is_demo = false
                  AND NOT EXISTS (
                    SELECT 1 FROM tax_intel.tax_observations obs
                    JOIN tax_intel.raw_artifacts a ON a.id = obs.raw_artifact_id
                    WHERE obs.opportunity_id = o.id
                      AND COALESCE(a.source_url, '') ~* '^https?://'
                      AND COALESCE(a.source_url, '') NOT LIKE 'fixture://%'
                  )
                  AND (
                    EXISTS (
                      SELECT 1 FROM tax_intel.tax_observations obs
                      JOIN tax_intel.raw_artifacts a ON a.id = obs.raw_artifact_id
                      WHERE obs.opportunity_id = o.id
                        AND (
                          COALESCE(a.source_url, '') LIKE 'fixture://%'
                          OR COALESCE(a.metadata_json->>'fixture', '') = 'true'
                        )
                    )
                    OR EXISTS (
                      SELECT 1 FROM tax_intel.source_registry s
                      JOIN tax_intel.tax_observations obs ON obs.source_id = s.id
                      WHERE obs.opportunity_id = o.id
                        AND COALESCE(s.configuration_json->>'use_fixture', 'false') = 'true'
                        AND COALESCE((s.configuration_json->>'allow_live_pdf')::boolean, false) = false
                        AND COALESCE((s.configuration_json->>'allow_live_html')::boolean, false) = false
                    )
                  )
                """
            ),
            {"aid": account_id},
        )


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
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND sale_lifecycle_status = ANY(:active)) AS active_published,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND sale_lifecycle_status = 'AUCTION_SCHEDULED') AS auction_scheduled,
                  COUNT(*) FILTER (
                    WHERE is_active AND NOT is_demo
                      AND auction_start_at IS NOT NULL
                      AND auction_start_at <= :soon
                      AND auction_start_at >= :now
                  ) AS auction_within_30_days,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND patent_classification = 'CONFIRMED') AS confirmed_patents,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND patent_classification = 'PROBABLE') AS probable_patents,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND mineral_signal = 'HIGH') AS high_mineral,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND acquisition_readiness_score >= 70) AS high_acquisition,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND priority_tier = 'A') AS priority_a,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND last_observed_at >= :week) AS new_since_week,
                  COUNT(*) FILTER (
                    WHERE NOT is_demo
                      AND sale_lifecycle_status IN ('REDEEMED', 'WITHDRAWN')
                      AND last_observed_at >= :week
                  ) AS redeemed_withdrawn_week,
                  COUNT(*) FILTER (WHERE NOT is_demo AND review_status = 'OPEN') AS needing_review,
                  COUNT(*) FILTER (WHERE is_demo AND is_active) AS demo_fixtures
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
        {"key": "demo_fixtures", "label": "Demo fixtures (hidden by default)", "value": summary.get("demo_fixtures") or 0, "filter": {"include_demo": True}},
    ]
    return {
        "ok": True,
        "error": None,
        "enabled": True,
        "coverage_banner": {
            "message": "Live-verified county publications only (demo/fixture rows hidden by default).",
            "detail": (
                "Auction dates must come from a county tax-sale / tax-deed list, CSV upload, or validated live feed. "
                "Packaged demo fixtures are for UI testing and are not live publications."
            ),
            "enabled_counties": coverage.get("enabled_counties") or 0,
            "healthy_counties": coverage.get("healthy_counties") or 0,
            "failed_or_stale": coverage.get("failed_or_stale") or 0,
            "demo_fixtures": summary.get("demo_fixtures") or 0,
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
    auction_timing: str | None = "upcoming",
    active_only: bool = True,
    include_demo: bool = False,
    watchlisted: bool | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "overall_priority_score",
    order: str = "desc",
) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err

    timing = (auction_timing or "upcoming").strip().lower()
    if timing not in {"upcoming", "past", "all"}:
        timing = "upcoming"

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
    # Default list sort follows auction timing when the client uses the score default.
    if sort == "overall_priority_score":
        if timing == "upcoming":
            sort = "auction_start_at"
            order = "asc"
        elif timing == "past":
            sort = "auction_start_at"
            order = "desc"
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
    if not include_demo:
        clauses.append("o.is_demo = false")
    if timing == "upcoming":
        clauses.append("o.auction_start_at IS NOT NULL")
        clauses.append("(o.auction_start_at AT TIME ZONE 'UTC')::date >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date")
    elif timing == "past":
        clauses.append("o.auction_start_at IS NOT NULL")
        clauses.append("(o.auction_start_at AT TIME ZONE 'UTC')::date < (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date")
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
                       claim_type, distance_meters, inside_parcel, raw_payload_json,
                       COALESCE(
                         raw_payload_json->>'case_page',
                         raw_payload_json->>'case_url'
                       ) AS case_page
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
                       minimum_bid, years_delinquent, sale_date, source_id::text AS source_id,
                       raw_payload_json
                FROM tax_intel.tax_observations
                WHERE opportunity_id = CAST(:id AS uuid)
                ORDER BY observed_at DESC
                """
            ),
            {"id": opportunity_id},
        ).mappings().all()
        source_listing_row = conn.execute(
            text(
                """
                SELECT s.name, s.listing_url, s.source_key
                FROM tax_intel.tax_observations obs
                JOIN tax_intel.source_registry s ON s.id = obs.source_id
                WHERE obs.opportunity_id = CAST(:id AS uuid)
                  AND obs.source_id IS NOT NULL
                ORDER BY obs.observed_at DESC
                LIMIT 1
                """
            ),
            {"id": opportunity_id},
        ).mappings().first()
        if not source_listing_row:
            source_listing_row = conn.execute(
                text(
                    """
                    SELECT s.name, s.listing_url, s.source_key
                    FROM tax_intel.source_registry s
                    WHERE s.state = :st AND lower(s.county_name) = lower(:co)
                    ORDER BY s.enabled DESC, s.updated_at DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"st": opp.get("state"), "co": opp.get("county_name")},
            ).mappings().first()
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

    record_detail_url = None
    if observations:
        payload = observations[0].get("raw_payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = None
        if isinstance(payload, dict):
            record_detail_url = (
                payload.get("detail_url")
                or payload.get("record_url")
                or payload.get("listing_url")
                or payload.get("source_url")
            )

    # Fall back to evidence ledger / parcel identifier source when observation join misses.
    source_row = dict(source_listing_row) if source_listing_row else None
    if not (source_row or {}).get("listing_url"):
        for e in evidence:
            url = e.get("source_url")
            if url and str(url).startswith("http"):
                source_row = {
                    "name": e.get("source_name") or (source_row or {}).get("name"),
                    "listing_url": str(url),
                    "source_key": (source_row or {}).get("source_key"),
                }
                break
    if not (source_row or {}).get("listing_url"):
        with eng.connect() as conn:
            via_ident = conn.execute(
                text(
                    """
                    SELECT s.name, s.listing_url, s.source_key
                    FROM tax_intel.parcel_identifiers pi
                    JOIN tax_intel.source_registry s ON s.id = pi.source_id
                    WHERE pi.opportunity_id = CAST(:id AS uuid)
                      AND pi.source_id IS NOT NULL
                      AND s.listing_url IS NOT NULL
                    ORDER BY pi.is_primary DESC, pi.created_at DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"id": opportunity_id},
            ).mappings().first()
            if via_ident:
                source_row = dict(via_ident)

    source_listing = build_source_listing(
        source_row=source_row,
        auction_start_at=detail.get("auction_start_at"),
        record_detail_url=record_detail_url,
        is_demo=bool(detail.get("is_demo")),
    )
    # Surface on opportunity for clients that only read that object.
    detail["listing_url"] = source_listing.get("open_url")
    detail["source_name"] = source_listing.get("name")

    return {
        "ok": True,
        "error": None,
        "opportunity": detail,
        "source_listing": source_listing,
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
                      AND o.is_active = true
                      AND o.is_demo = false
                  ) AS record_count,
                  COALESCE(s.configuration_json->>'live_status', '') AS live_status,
                  COALESCE((s.configuration_json->>'allow_live_pdf')::boolean, false)
                    OR COALESCE((s.configuration_json->>'allow_live_html')::boolean, false) AS is_live_feed
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
                WHERE o.account_id = :aid
                  AND t.status = 'OPEN'
                  AND o.is_demo = false
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
            "live_jurisdictions": sum(1 for i in items if i.get("is_live_feed")),
            "open_review_tasks": int(open_reviews or 0),
        },
        "coverage_language": (
            "All Utah, Idaho, and Nevada counties are registered. "
            "Record counts are active non-demo opportunities. "
            "Only validated live feeds (allow_live_pdf / allow_live_html) are enabled for auto-pull; "
            "other counties stay pending until a treasurer publication is wired."
        ),
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
        "is_demo = false",
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
                WHERE o.account_id = :aid
                  AND t.status = 'OPEN'
                  AND o.is_demo = false
                ORDER BY t.priority DESC, t.created_at ASC
                LIMIT 200
                """
            ),
            {"aid": account_id},
        ).mappings().all()
    return {"ok": True, "error": None, "items": [_row(r) for r in rows]}
