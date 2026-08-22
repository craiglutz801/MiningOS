"""SITLA opportunity query/service layer."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.sitla_intel.demo_seed import ensure_demo_seed, schema_ready
from mining_os.sitla_intel.enums import ACTIVE_LIFECYCLE

log = logging.getLogger("mining_os.sitla_intel.opportunity_service")


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
    bidding_end_at: Any = None,
    record_detail_url: str | None = None,
    listing_fallback: str | None = None,
    bid_portal_url: str | None = None,
    is_demo: bool = False,
) -> dict[str, Any]:
    """Normalize source trail for opportunity detail (official PDF / hub links)."""
    name = (source_row or {}).get("name")
    listing_url = (source_row or {}).get("listing_url") or listing_fallback
    source_key = (source_row or {}).get("source_key")
    auction = bidding_end_at
    if hasattr(auction, "isoformat"):
        auction = auction.isoformat()
    detail = (record_detail_url or "").strip() or None
    listing = (listing_url or "").strip() or None
    portal = (bid_portal_url or "").strip() or None
    open_url = detail or listing
    if is_demo:
        open_url = detail
    return {
        "name": name,
        "source_key": source_key,
        "listing_url": listing,
        "record_detail_url": detail,
        "bid_portal_url": portal,
        "open_url": open_url,
        "bidding_end_at": auction,
        "is_demo": is_demo,
        "verified_publication": not is_demo and bool(open_url),
    }


def disabled_payload(message: str = "SITLA Intelligence module is disabled.") -> dict[str, Any]:
    return {"ok": False, "error": message, "enabled": False}


def ensure_ready(account_id: int) -> dict[str, Any] | None:
    if not schema_ready():
        return {
            "ok": False,
            "error": "SITLA schema is not installed. Run: python -m mining_os.pipelines.run_all --init-db",
            "enabled": True,
        }
    try:
        ensure_demo_seed(account_id)
    except Exception as e:
        log.exception("sitla demo seed failed")
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
            text("""
                SELECT
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND lifecycle_status = ANY(:active)) AS active_opportunities,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND lifecycle_status = 'BIDDING_OPEN') AS bidding_now,
                  COUNT(*) FILTER (
                    WHERE is_active AND NOT is_demo AND (
                      (bidding_end_at IS NOT NULL AND bidding_end_at <= :soon AND bidding_end_at >= :now)
                      OR (application_deadline IS NOT NULL AND application_deadline <= :soon AND application_deadline >= :now)
                    )
                  ) AS deadlines_within_30_days,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND last_observed_at >= :week) AS new_this_week,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND priority_tier = 'A') AS high_priority,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND opportunity_type = 'COMPETITIVE_MINERAL_LEASE') AS competitive_leases,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND opportunity_type = 'COMPETING_APPLICATION_NOTICE') AS competing_applications,
                  COUNT(*) FILTER (WHERE is_active AND NOT is_demo AND opportunity_type IN ('MINERAL_MATERIAL_PERMIT', 'SAND_GRAVEL_PERMIT')) AS mineral_material_permits,
                  COUNT(*) FILTER (WHERE NOT is_demo AND review_status = 'OPEN') AS needing_review,
                  COUNT(*) FILTER (WHERE is_demo AND is_active) AS demo_fixtures
                FROM sitla_intel.opportunities
                WHERE account_id = :aid
            """),
            {
                "aid": account_id,
                "active": list(ACTIVE_LIFECYCLE),
                "soon": now + timedelta(days=30),
                "now": now,
                "week": now - timedelta(days=7),
            },
        ).mappings().first()
        cov = conn.execute(
            text("""
                SELECT
                  COUNT(*) FILTER (WHERE enabled) AS enabled_sources,
                  COUNT(*) FILTER (WHERE enabled AND health_status = 'HEALTHY') AS healthy_sources,
                  COUNT(*) FILTER (WHERE enabled AND health_status IN ('FAILED', 'STALE', 'DEGRADED')) AS failed_or_stale
                FROM sitla_intel.sources
            """)
        ).mappings().first()

    summary = _row(rows or {})
    coverage = _row(cov or {})
    cards = [
        {"key": "active", "label": "Active opportunities", "value": summary.get("active_opportunities") or 0, "filter": {"active_only": True}},
        {"key": "bidding", "label": "Bidding now", "value": summary.get("bidding_now") or 0, "filter": {"status": "BIDDING_OPEN"}},
        {"key": "deadlines", "label": "Deadlines within 30 days", "value": summary.get("deadlines_within_30_days") or 0, "filter": {"deadline_within_days": 30}},
        {"key": "new_week", "label": "New this week", "value": summary.get("new_this_week") or 0, "filter": {}},
        {"key": "priority_a", "label": "High priority (A)", "value": summary.get("high_priority") or 0, "filter": {"priority_tier": "A"}},
        {"key": "competitive", "label": "Competitive mineral leases", "value": summary.get("competitive_leases") or 0, "filter": {"opportunity_type": "COMPETITIVE_MINERAL_LEASE"}},
        {"key": "competing", "label": "Competing applications", "value": summary.get("competing_applications") or 0, "filter": {"opportunity_type": "COMPETING_APPLICATION_NOTICE"}},
        {"key": "materials", "label": "Mineral-material permits", "value": summary.get("mineral_material_permits") or 0, "filter": {"opportunity_type": "SAND_GRAVEL_PERMIT"}},
        {"key": "review", "label": "Needs review", "value": summary.get("needing_review") or 0, "filter": {"review_status": "OPEN"}},
        {"key": "healthy", "label": "Healthy / enabled sources", "value": f"{coverage.get('healthy_sources') or 0}/{coverage.get('enabled_sources') or 0}", "filter": None},
    ]
    return {
        "ok": True,
        "error": None,
        "enabled": True,
        "coverage_banner": {
            "message": "Live Trust Lands publications only (demo fixtures hidden by default).",
            "detail": (
                "Utah SITLA competitive mineral auctions, Idaho IDL endowment mineral/oil-gas leasing, "
                "and Nevada NDSL school-trust inventory (~3k acres; not a SITLA-scale auction program)."
            ),
            "enabled_sources": coverage.get("enabled_sources") or 0,
            "healthy_sources": coverage.get("healthy_sources") or 0,
            "failed_or_stale": coverage.get("failed_or_stale") or 0,
            "demo_fixtures": summary.get("demo_fixtures") or 0,
        },
        "cards": cards,
        "disclaimer": (
            "SITLA postings describe state-administered mineral opportunities. "
            "Lease eligibility, rights offered, royalties, bonding, and award decisions require official documents and are not determined by Mining OS scoring."
        ),
    }


def list_opportunities(
    account_id: int,
    *,
    state: str | None = None,
    county: str | None = None,
    status: str | None = None,
    opportunity_type: str | None = None,
    priority_tier: str | None = None,
    review_status: str | None = None,
    search: str | None = None,
    min_score: float | None = None,
    deadline_within_days: int | None = None,
    deadline_timing: str | None = "upcoming",
    active_only: bool = True,
    include_demo: bool = False,
    historical: bool | None = None,
    watchlisted: bool | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "overall_priority_score",
    order: str = "desc",
) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    timing = (deadline_timing or "upcoming").strip().lower()
    if timing not in {"upcoming", "past", "all"}:
        timing = "upcoming"
    allowed_sort = {
        "overall_priority_score", "bidding_end_at", "application_deadline", "minimum_bid",
        "county_name", "acreage", "last_observed_at", "best_title", "priority_tier",
    }
    if sort not in allowed_sort:
        sort = "overall_priority_score"
    if sort == "overall_priority_score":
        if timing == "upcoming":
            sort = "bidding_end_at"
            order = "asc"
        elif timing == "past":
            sort = "bidding_end_at"
            order = "desc"
    order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 50)))
    offset = (page - 1) * page_size
    clauses = ["o.account_id = :aid"]
    params: dict[str, Any] = {"aid": account_id, "limit": page_size, "offset": offset}
    # Upcoming: active pipeline only. Past/all: include closed sales that still have real dates.
    if timing == "upcoming" and active_only:
        clauses.append("o.is_active = true")
        clauses.append("o.lifecycle_status = ANY(:active)")
        params["active"] = list(ACTIVE_LIFECYCLE)
    elif timing == "all" and active_only:
        clauses.append("(o.is_active = true OR o.is_historical = true)")
    if not include_demo:
        clauses.append("o.is_demo = false")
    if timing == "upcoming":
        clauses.append("""(
            (o.bidding_end_at IS NOT NULL AND (o.bidding_end_at AT TIME ZONE 'UTC')::date >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date)
            OR (o.application_deadline IS NOT NULL AND (o.application_deadline AT TIME ZONE 'UTC')::date >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date)
        )""")
    elif timing == "past":
        clauses.append("""(
            (o.bidding_end_at IS NOT NULL AND (o.bidding_end_at AT TIME ZONE 'UTC')::date < (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date)
            OR (
              o.bidding_end_at IS NULL
              AND o.application_deadline IS NOT NULL
              AND (o.application_deadline AT TIME ZONE 'UTC')::date < (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date
            )
        )""")
    if historical is True:
        clauses.append("o.is_historical = true")
    if state:
        clauses.append("o.state = :state")
        params["state"] = state.strip().upper()[:2]
    if county:
        clauses.append("o.county_name ILIKE :county")
        params["county"] = f"%{county.strip()}%"
    if status:
        clauses.append("o.lifecycle_status = :status")
        params["status"] = status.strip().upper()
    if opportunity_type:
        clauses.append("o.opportunity_type = :otype")
        params["otype"] = opportunity_type.strip().upper()
    if priority_tier:
        clauses.append("o.priority_tier = :tier")
        params["tier"] = priority_tier.strip().upper()[:1]
    if review_status:
        clauses.append("o.review_status = :rev")
        params["rev"] = review_status.strip().upper()
    if min_score is not None:
        clauses.append("o.overall_priority_score >= :min_score")
        params["min_score"] = float(min_score)
    if deadline_within_days is not None:
        clauses.append("""(
            (o.bidding_end_at IS NOT NULL AND o.bidding_end_at >= now() AND o.bidding_end_at <= now() + make_interval(days => :ddays))
            OR (o.application_deadline IS NOT NULL AND o.application_deadline >= now() AND o.application_deadline <= now() + make_interval(days => :ddays))
        )""")
        params["ddays"] = int(deadline_within_days)
    if search and search.strip():
        clauses.append("""(
          o.best_title ILIKE :q OR o.reference_number ILIKE :q OR o.plss_key ILIKE :q
          OR o.county_name ILIKE :q OR o.legal_description_raw ILIKE :q
          OR EXISTS (SELECT 1 FROM unnest(o.commodities) c WHERE c ILIKE :q)
        )""")
        params["q"] = f"%{search.strip()}%"
    if watchlisted is True:
        clauses.append("EXISTS (SELECT 1 FROM sitla_intel.watchlists w WHERE w.opportunity_id = o.id AND w.account_id = :aid)")
    elif watchlisted is False:
        clauses.append("NOT EXISTS (SELECT 1 FROM sitla_intel.watchlists w WHERE w.opportunity_id = o.id AND w.account_id = :aid)")
    where = " AND ".join(clauses)
    eng = get_engine()
    with eng.begin() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM sitla_intel.opportunities o WHERE {where}"), params).scalar()
        rows = conn.execute(
            text(f"""
                SELECT o.id::text, o.reference_number, o.best_title, o.opportunity_type, o.lifecycle_status,
                       o.state, o.agency_code, o.county_name, o.published_commodity, o.commodities,
                       o.acreage, o.plss_key,
                       o.latitude, o.longitude, o.offering_cycle,
                       o.bidding_start_at, o.bidding_end_at, o.application_deadline,
                       o.minimum_bid, o.winning_bid, o.overall_priority_score, o.priority_tier,
                       o.mineral_potential_score, o.acquisition_readiness_score, o.review_status,
                       o.official_detail_url, o.external_bid_url, o.is_active, o.is_demo,
                       o.is_historical, o.last_observed_at,
                       EXISTS (
                         SELECT 1 FROM sitla_intel.watchlists w
                         WHERE w.opportunity_id = o.id AND w.account_id = :aid
                       ) AS watchlisted
                FROM sitla_intel.opportunities o
                WHERE {where}
                ORDER BY o.{sort} {order_sql} NULLS LAST, o.id
                LIMIT :limit OFFSET :offset
            """),
            params,
        ).mappings().all()
    return {"ok": True, "error": None, "items": [_row(r) for r in rows], "total": int(total or 0), "page": page, "page_size": page_size}


def get_opportunity(account_id: int, opportunity_id: str) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        opp = conn.execute(
            text("""
                SELECT o.*, EXISTS (
                  SELECT 1 FROM sitla_intel.watchlists w
                  WHERE w.opportunity_id = o.id AND w.account_id = :aid
                ) AS watchlisted
                FROM sitla_intel.opportunities o
                WHERE o.id = CAST(:id AS uuid) AND o.account_id = :aid
            """),
            {"id": opportunity_id, "aid": account_id},
        ).mappings().first()
        if not opp:
            return {"ok": False, "error": "Opportunity not found"}
        timeline = conn.execute(
            text("""
                SELECT id::text, event_type, event_at, title, description, amount
                FROM sitla_intel.opportunity_events
                WHERE opportunity_id = CAST(:oid AS uuid)
                ORDER BY event_at NULLS LAST, created_at
            """),
            {"oid": opportunity_id},
        ).mappings().all()
        mineral = conn.execute(
            text("""
                SELECT id::text, evidence_type, mine_name, prospect_name, commodity_normalized,
                       production_status, distance_meters, inside_parcel, confidence
                FROM sitla_intel.mineral_evidence WHERE opportunity_id = CAST(:oid AS uuid)
            """),
            {"oid": opportunity_id},
        ).mappings().all()
        claims = conn.execute(
            text("""
                SELECT id::text, mlrs_serial_number, claim_name, claim_status, claimant_name, distance_meters
                FROM sitla_intel.claim_context WHERE opportunity_id = CAST(:oid AS uuid)
            """),
            {"oid": opportunity_id},
        ).mappings().all()
        evidence = conn.execute(
            text("""
                SELECT e.id::text, e.fact_key, e.fact_value_json, e.evidence_class, e.confidence,
                       e.extraction_method, e.source_url, s.name AS source_name
                FROM sitla_intel.evidence_items e
                LEFT JOIN sitla_intel.sources s ON s.id = e.source_id
                WHERE e.opportunity_id = CAST(:oid AS uuid)
                ORDER BY e.created_at DESC
            """),
            {"oid": opportunity_id},
        ).mappings().all()
        bids = conn.execute(
            text("""
                SELECT id::text, winning_bidder, winning_bid, bid_per_acre, outcome, result_date, source_url
                FROM sitla_intel.bid_results WHERE opportunity_id = CAST(:oid AS uuid)
            """),
            {"oid": opportunity_id},
        ).mappings().all()
        score = conn.execute(
            text("""
                SELECT mineral_potential_score, acquisition_readiness_score, risk_penalty,
                       overall_priority_score, priority_tier, explanation_json, score_version
                FROM sitla_intel.score_snapshots
                WHERE opportunity_id = CAST(:oid AS uuid)
                ORDER BY calculated_at DESC LIMIT 1
            """),
            {"oid": opportunity_id},
        ).mappings().first()
        links = conn.execute(
            text("""
                SELECT id::text, area_of_focus_id, link_type, confidence
                FROM sitla_intel.opportunity_target_links
                WHERE opportunity_id = CAST(:oid AS uuid)
            """),
            {"oid": opportunity_id},
        ).mappings().all()
        matches = conn.execute(
            text("""
                SELECT m.id::text, m.match_type, m.match_confidence, m.summary,
                       m.related_opportunity_id::text,
                       r.reference_number AS related_reference,
                       r.best_title AS related_title,
                       r.winning_bid AS related_winning_bid,
                       r.lifecycle_status AS related_lifecycle
                FROM sitla_intel.historical_matches m
                LEFT JOIN sitla_intel.opportunities r ON r.id = m.related_opportunity_id
                WHERE m.opportunity_id = CAST(:oid AS uuid)
                ORDER BY m.match_confidence DESC NULLS LAST
            """),
            {"oid": opportunity_id},
        ).mappings().all()
        source_listing_row = conn.execute(
            text("""
                SELECT s.name, s.listing_url, s.source_key
                FROM sitla_intel.opportunity_observations obs
                JOIN sitla_intel.sources s ON s.id = obs.source_id
                WHERE obs.opportunity_id = CAST(:oid AS uuid)
                  AND obs.source_id IS NOT NULL
                ORDER BY obs.observed_at DESC
                LIMIT 1
            """),
            {"oid": opportunity_id},
        ).mappings().first()

    detail = _row(opp)
    detail["id"] = opportunity_id
    record_detail = detail.get("official_detail_url")
    listing_fallback = None
    for e in evidence:
        url = e.get("source_url")
        if url and str(url).startswith("http"):
            listing_fallback = str(url)
            break
    source_row = dict(source_listing_row) if source_listing_row else None
    source_listing = build_source_listing(
        source_row=source_row,
        bidding_end_at=detail.get("bidding_end_at") or detail.get("application_deadline"),
        record_detail_url=record_detail,
        listing_fallback=listing_fallback or "https://trustlands.utah.gov/work-with-us/energy-minerals/",
        bid_portal_url=detail.get("external_bid_url"),
        is_demo=bool(detail.get("is_demo")),
    )
    detail["listing_url"] = source_listing.get("open_url")
    detail["source_name"] = source_listing.get("name")

    return {
        "ok": True,
        "error": None,
        "opportunity": detail,
        "source_listing": source_listing,
        "timeline": [_row(r) for r in timeline],
        "mineral_evidence": [_row(r) for r in mineral],
        "claim_context": [_row(r) for r in claims],
        "evidence_ledger": [_row(r) for r in evidence],
        "bid_results": [_row(r) for r in bids],
        "historical_matches": [_row(r) for r in matches],
        "score": _row(score) if score else None,
        "target_links": [_row(r) for r in links],
        "disclaimer": (
            "Scores are deterministic decision-support only (sitla-v1.0). "
            "Official SITLA documents govern rights, fees, royalties, and award outcomes."
        ),
    }


def get_coverage(account_id: int) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT id::text, source_key, name, listing_url, parser_kind, enabled, manual_only,
                       state, agency_code,
                       health_status, last_success_at, last_failure_at, consecutive_failures, notes
                FROM sitla_intel.sources
                ORDER BY COALESCE(state, 'ZZ'), name
            """)
        ).mappings().all()
    items = [_row(r) for r in rows]
    return {
        "ok": True,
        "error": None,
        "sources": items,
        "jurisdictions": items,
        "coverage_language": (
            "Trust Lands agencies: Utah SITLA, Idaho IDL endowment leasing, "
            "Nevada NDSL school-trust inventory."
        ),
    }


def get_map_features(account_id: int, *, limit: int = 500) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT id::text, best_title, opportunity_type, lifecycle_status, county_name,
                       latitude, longitude, overall_priority_score, priority_tier, acreage
                FROM sitla_intel.opportunities
                WHERE account_id = :aid AND is_active = true AND is_demo = false
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY overall_priority_score DESC NULLS LAST
                LIMIT :lim
            """),
            {"aid": account_id, "lim": limit},
        ).mappings().all()
    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["longitude"]), float(r["latitude"])]},
            "properties": _row(r),
        })
    return {"ok": True, "error": None, "type": "FeatureCollection", "features": features}


def list_review_tasks(account_id: int) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT t.id::text, t.opportunity_id::text, t.task_type, t.priority, t.status,
                       t.title, t.instructions, o.best_title, o.county_name, o.priority_tier
                FROM sitla_intel.review_tasks t
                JOIN sitla_intel.opportunities o ON o.id = t.opportunity_id
                WHERE o.account_id = :aid AND t.status = 'OPEN' AND o.is_demo = false
                ORDER BY t.priority ASC, t.created_at DESC
            """),
            {"aid": account_id},
        ).mappings().all()
    return {"ok": True, "error": None, "items": [_row(r) for r in rows]}


def set_watch(account_id: int, opportunity_id: str, *, watch: bool, user_id: int | None = None) -> dict[str, Any]:
    err = ensure_ready(account_id)
    if err:
        return err
    eng = get_engine()
    with eng.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sitla_intel.opportunities WHERE id = CAST(:id AS uuid) AND account_id = :aid"),
            {"id": opportunity_id, "aid": account_id},
        ).first()
        if not exists:
            return {"ok": False, "error": "Opportunity not found"}
        if watch:
            conn.execute(
                text("""
                    INSERT INTO sitla_intel.watchlists (account_id, user_id, opportunity_id)
                    VALUES (:aid, :uid, CAST(:oid AS uuid))
                    ON CONFLICT (account_id, opportunity_id) DO NOTHING
                """),
                {"aid": account_id, "uid": user_id, "oid": opportunity_id},
            )
        else:
            conn.execute(
                text("DELETE FROM sitla_intel.watchlists WHERE account_id = :aid AND opportunity_id = CAST(:oid AS uuid)"),
                {"aid": account_id, "oid": opportunity_id},
            )
        count = conn.execute(
            text("SELECT COUNT(*) FROM sitla_intel.watchlists WHERE opportunity_id = CAST(:oid AS uuid)"),
            {"oid": opportunity_id},
        ).scalar()
        conn.execute(
            text("UPDATE sitla_intel.opportunities SET watch_count = :c, updated_at = now() WHERE id = CAST(:oid AS uuid)"),
            {"c": int(count or 0), "oid": opportunity_id},
        )
    return {"ok": True, "error": None, "watchlisted": watch}
