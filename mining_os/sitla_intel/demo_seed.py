"""Seed SITLA sources + demo opportunities for UI validation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.sitla_intel.scoring import compute_scores

log = logging.getLogger("mining_os.sitla_intel.demo_seed")

PILOT_SOURCES = [
    {
        "source_key": "sitla_energy_minerals_hub",
        "name": "SITLA Energy & Minerals Hub",
        "listing_url": "https://trustlands.utah.gov/work-with-us/energy-minerals/",
        "parser_kind": "HTML_HUB",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Primary discovery hub. Default: fixture. Set allow_live_html=true after validation.",
        "configuration": {
            "use_fixture": True,
            "fixture_file": "sitla_offerings.json",
            "allow_live_html": False,
            "link_keywords": ["auction", "mineral", "lease", "bid", "offering"],
        },
    },
    {
        "source_key": "sitla_past_auctions",
        "name": "SITLA Past Mineral Auctions",
        "listing_url": "https://trustlands.utah.gov/work-with-us/energy-minerals/past-auctions/",
        "parser_kind": "HTML_INDEX",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Historical offering lists and final results. Fixture-backed by default.",
        "configuration": {
            "use_fixture": True,
            "fixture_file": "sitla_past_auctions.json",
            "allow_live_html": False,
        },
    },
    {
        "source_key": "sitla_public_notices",
        "name": "SITLA Energy & Minerals Public Notices",
        "listing_url": "https://trustlands.utah.gov/work-with-us/public-notice/",
        "parser_kind": "HTML_INDEX",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Competing applications, OBAs, mineral-material notices. Fixture-backed by default.",
        "configuration": {
            "use_fixture": True,
            "fixture_file": "sitla_public_notices.json",
            "allow_live_html": False,
        },
    },
    {
        "source_key": "sitla_fixture_offerings",
        "name": "SITLA Pilot Offerings (fixture)",
        "listing_url": "fixture://sitla_offerings.json",
        "parser_kind": "FIXTURE_JSON",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Packaged competitive offering fixture for UI + ingest validation.",
        "configuration": {
            "use_fixture": True,
            "fixture_file": "sitla_offerings.json",
        },
    },
]


def schema_ready() -> bool:
    eng = get_engine()
    with eng.begin() as conn:
        return bool(
            conn.execute(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1 FROM information_schema.tables
                      WHERE table_schema = 'sitla_intel' AND table_name = 'opportunities'
                    )
                    """
                )
            ).scalar()
        )


def _demo_opportunities(now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "canonical_key": "demo-sitla-juab-w-tungsten",
            "reference_number": "ML-2026-014",
            "best_title": "Juab County — metalliferous mineral lease (tungsten district)",
            "opportunity_type": "METALLIFEROUS_MINERAL_LEASE",
            "lifecycle_status": "BIDDING_OPEN",
            "county_name": "Juab",
            "county_fips": "49023",
            "published_commodity": "Metalliferous minerals",
            "commodities": ["Tungsten", "Silver"],
            "acreage": 640.0,
            "legal": "T12S R2W Sec 16 SLM — SITLA school section",
            "township": "12S",
            "range": "2W",
            "section_summary": "16",
            "meridian": "SLM",
            "plss_key": "T12S R2W Sec 16",
            "latitude": 39.812,
            "longitude": -112.205,
            "offering_cycle": "2026-JUN",
            "bidding_end_at": now + timedelta(days=18),
            "bidding_start_at": now - timedelta(days=5),
            "minimum_bid": 1280.0,
            "annual_rental": 640.0,
            "royalty_rate": "As published in lease terms",
            "application_fee": 50.0,
            "primary_term_years": 10,
            "official_detail_url": "https://trustlands.utah.gov/work-with-us/energy-minerals/",
            "score_inputs": {
                "mine_inside_acreage": True,
                "commodity_evidence": True,
                "historic_production": True,
                "district_quality": True,
                "active_claims": True,
                "strategic_mineral": True,
                "official_active": True,
                "deadline_clear": True,
                "geometry_resolved": True,
                "commercial_terms": True,
                "documents_complete": True,
                "data_fresh": True,
                "rights_unclear": True,
            },
            "mine_name": "Spor Mountain / West Desert tungsten prospect",
            "source_key": "sitla_fixture_offerings",
            "review_task": "RIGHTS_CLARITY",
        },
        {
            "canonical_key": "demo-sitla-beaver-competitive",
            "reference_number": "ML-2026-021",
            "best_title": "Beaver County — competitive mineral lease offering",
            "opportunity_type": "COMPETITIVE_MINERAL_LEASE",
            "lifecycle_status": "SCHEDULED",
            "county_name": "Beaver",
            "county_fips": "49001",
            "published_commodity": "Metalliferous / industrial minerals",
            "commodities": ["Gold", "Copper"],
            "acreage": 320.0,
            "legal": "T27S R11W Sec 2 SLM",
            "township": "27S",
            "range": "11W",
            "section_summary": "02",
            "meridian": "SLM",
            "plss_key": "T27S R11W Sec 02",
            "latitude": 38.421,
            "longitude": -113.198,
            "offering_cycle": "2026-OCT",
            "bidding_start_at": now + timedelta(days=40),
            "bidding_end_at": now + timedelta(days=55),
            "minimum_bid": 640.0,
            "annual_rental": 320.0,
            "official_detail_url": "https://trustlands.utah.gov/work-with-us/energy-minerals/",
            "score_inputs": {
                "commodity_evidence": True,
                "district_quality": True,
                "active_claims": True,
                "official_active": True,
                "deadline_clear": True,
                "geometry_resolved": True,
                "commercial_terms": True,
                "data_fresh": True,
                "historical_comparisons": True,
            },
            "mine_name": "Southern Wah Wah Range occurrence",
            "source_key": "sitla_energy_minerals_hub",
        },
        {
            "canonical_key": "demo-sitla-tooele-competing",
            "reference_number": "PN-EM-2026-088",
            "best_title": "Tooele County — competing application notice (industrial minerals)",
            "opportunity_type": "COMPETING_APPLICATION_NOTICE",
            "lifecycle_status": "COMPETING_APPLICATION_OPEN",
            "county_name": "Tooele",
            "county_fips": "49045",
            "published_commodity": "Industrial minerals",
            "commodities": ["Limestone", "Clay"],
            "acreage": 80.0,
            "legal": "T3S R5W Sec 36 SLM",
            "township": "3S",
            "range": "5W",
            "section_summary": "36",
            "meridian": "SLM",
            "plss_key": "T3S R5W Sec 36",
            "latitude": 40.512,
            "longitude": -112.451,
            "application_deadline": now + timedelta(days=12),
            "minimum_bid": None,
            "official_detail_url": "https://trustlands.utah.gov/work-with-us/public-notice/",
            "score_inputs": {
                "official_active": True,
                "deadline_clear": True,
                "geometry_resolved": True,
                "commodity_evidence": True,
                "data_fresh": True,
                "rights_unclear": True,
            },
            "source_key": "sitla_public_notices",
            "review_task": "DEADLINE_DILIGENCE",
        },
        {
            "canonical_key": "demo-sitla-emery-sand-gravel",
            "reference_number": "MMP-2026-003",
            "best_title": "Emery County — sand & gravel mineral-material permit",
            "opportunity_type": "SAND_GRAVEL_PERMIT",
            "lifecycle_status": "PUBLIC_NOTICE_OPEN",
            "county_name": "Emery",
            "county_fips": "49015",
            "published_commodity": "Sand and gravel",
            "commodities": ["Sand and Gravel"],
            "acreage": 40.0,
            "legal": "T21S R8E Sec 16 SLM",
            "township": "21S",
            "range": "8E",
            "section_summary": "16",
            "meridian": "SLM",
            "plss_key": "T21S R8E Sec 16",
            "latitude": 38.951,
            "longitude": -110.812,
            "application_deadline": now + timedelta(days=28),
            "application_fee": 100.0,
            "official_detail_url": "https://trustlands.utah.gov/work-with-us/public-notice/",
            "score_inputs": {
                "official_active": True,
                "deadline_clear": True,
                "geometry_resolved": True,
                "commercial_terms": True,
                "data_fresh": True,
                "commodity_regional_only": True,
            },
            "source_key": "sitla_public_notices",
        },
        {
            "canonical_key": "demo-sitla-grand-historical",
            "reference_number": "ML-2024-009",
            "best_title": "Grand County — prior auction result (uranium district reoffer context)",
            "opportunity_type": "COMPETITIVE_MINERAL_LEASE",
            "lifecycle_status": "AWARDED",
            "is_active": False,
            "is_historical": True,
            "county_name": "Grand",
            "county_fips": "49019",
            "published_commodity": "Metalliferous minerals",
            "commodities": ["Uranium", "Vanadium"],
            "acreage": 1280.0,
            "legal": "T24S R20E Sec 16 & 36 SLM",
            "township": "24S",
            "range": "20E",
            "section_summary": "16,36",
            "meridian": "SLM",
            "plss_key": "T24S R20E Sec 16/36",
            "latitude": 38.712,
            "longitude": -109.551,
            "offering_cycle": "2024-OCT",
            "award_date": (now - timedelta(days=400)).date(),
            "minimum_bid": 2560.0,
            "winning_bid": 18400.0,
            "official_detail_url": "https://trustlands.utah.gov/work-with-us/energy-minerals/past-auctions/",
            "score_inputs": {
                "mine_inside_acreage": True,
                "commodity_evidence": True,
                "historic_production": True,
                "strategic_mineral": True,
                "historical_comparisons": True,
                "geometry_resolved": True,
            },
            "mine_name": "Lisbon Valley district vicinity",
            "source_key": "sitla_past_auctions",
            "winning_bidder": "Demo Bidder LLC",
        },
        {
            "canonical_key": "demo-sitla-millard-lithium",
            "reference_number": "ML-2026-031",
            "best_title": "Millard County — lithium / brine related mineral offering",
            "opportunity_type": "LITHIUM_LEASE",
            "lifecycle_status": "ANNOUNCED",
            "county_name": "Millard",
            "county_fips": "49027",
            "published_commodity": "Lithium / brine minerals",
            "commodities": ["Lithium"],
            "acreage": 2560.0,
            "legal": "T18S R8W Sec 2,11,14 SLM",
            "township": "18S",
            "range": "8W",
            "section_summary": "2,11,14",
            "meridian": "SLM",
            "plss_key": "T18S R8W Sec 2/11/14",
            "latitude": 39.201,
            "longitude": -112.812,
            "offering_cycle": "2026-OCT",
            "bidding_start_at": now + timedelta(days=70),
            "bidding_end_at": now + timedelta(days=90),
            "minimum_bid": 5120.0,
            "official_detail_url": "https://trustlands.utah.gov/work-with-us/energy-minerals/",
            "score_inputs": {
                "strategic_mineral": True,
                "favorable_geology": True,
                "official_active": True,
                "deadline_clear": True,
                "geometry_resolved": True,
                "data_fresh": True,
                "rights_unclear": True,
                "geometry_unresolved": False,
            },
            "source_key": "sitla_fixture_offerings",
            "review_task": "COMMODITY_RIGHTS",
        },
    ]


def ensure_demo_seed(account_id: int) -> dict[str, Any]:
    if not schema_ready():
        return {"ok": False, "error": "sitla_intel schema not migrated", "seeded": False}

    eng = get_engine()
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM sitla_intel.opportunities
                WHERE account_id = :aid AND is_demo = true
                """
            ),
            {"aid": account_id},
        ).scalar()
        if existing and int(existing) > 0:
            return {"ok": True, "seeded": False, "demo_count": int(existing)}

        source_ids: dict[str, str] = {}
        for src in PILOT_SOURCES:
            row = conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.sources (
                      source_key, name, listing_url, parser_kind, enabled, manual_only,
                      health_status, notes, adapter_class, configuration_json, last_success_at
                    ) VALUES (
                      :source_key, :name, :listing_url, :parser_kind, :enabled, :manual_only,
                      :health_status, :notes, 'FixtureJsonAdapter',
                      CAST(:cfg AS jsonb),
                      CASE WHEN :health_status = 'HEALTHY' THEN now() - interval '1 day' ELSE NULL END
                    )
                    ON CONFLICT (source_key) DO UPDATE SET
                      name = EXCLUDED.name,
                      listing_url = EXCLUDED.listing_url,
                      parser_kind = EXCLUDED.parser_kind,
                      health_status = EXCLUDED.health_status,
                      notes = EXCLUDED.notes,
                      configuration_json = EXCLUDED.configuration_json,
                      updated_at = now()
                    RETURNING id::text, source_key
                    """
                ),
                {
                    **{k: v for k, v in src.items() if k != "configuration"},
                    "cfg": json.dumps(src.get("configuration") or {"use_fixture": True}),
                },
            ).mappings().first()
            if row:
                source_ids[row["source_key"]] = row["id"]

        # Offering cycle
        conn.execute(
            text(
                """
                INSERT INTO sitla_intel.offering_cycles (cycle_key, name, auction_month, auction_year, status)
                VALUES ('2026-JUN', 'June 2026 Mineral Auction', 'June', 2026, 'BIDDING_OPEN')
                ON CONFLICT (cycle_key) DO NOTHING
                """
            )
        )

        created = 0
        for opp in _demo_opportunities(now):
            scores = compute_scores(opp["score_inputs"])
            oid = str(uuid4())
            is_active = opp.get("is_active", True)
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.opportunities (
                      id, account_id, canonical_key, reference_number, best_title,
                      opportunity_type, raw_opportunity_type, lifecycle_status, raw_status,
                      is_active, is_historical, is_demo,
                      county_name, county_fips, published_commodity, commodities, acreage,
                      legal_description_raw, township, range, section_summary, meridian, plss_key,
                      latitude, longitude, geometry_accuracy, offering_cycle,
                      bidding_start_at, bidding_end_at, application_deadline, award_date,
                      minimum_bid, winning_bid, annual_rental, royalty_rate, application_fee,
                      primary_term_years, rights_clarity,
                      mineral_potential_score, acquisition_readiness_score, overall_priority_score,
                      priority_tier, score_explanation_json, review_status,
                      official_detail_url, first_observed_at, last_observed_at
                    ) VALUES (
                      CAST(:id AS uuid), :aid, :ck, :ref, :title,
                      :otype, :otype, :life, :life,
                      :active, :hist, true,
                      :county, :fips, :commodity, CAST(:coms AS text[]), :acre,
                      :legal, :twp, :rng, :sec, :mer, :plss,
                      :lat, :lon, 'COORDINATE', :cycle,
                      :bstart, :bend, :adead, :award,
                      :minbid, :winbid, :rent, :royalty, :fee,
                      :term, 'UNKNOWN',
                      :ms, :ascore, :os, :tier, CAST(:expl AS jsonb), 'OPEN',
                      :url, :obs, :obs
                    )
                    """
                ),
                {
                    "id": oid,
                    "aid": account_id,
                    "ck": opp["canonical_key"],
                    "ref": opp.get("reference_number"),
                    "title": opp["best_title"],
                    "otype": opp["opportunity_type"],
                    "life": opp["lifecycle_status"],
                    "active": is_active,
                    "hist": opp.get("is_historical", False),
                    "county": opp["county_name"],
                    "fips": opp.get("county_fips"),
                    "commodity": opp.get("published_commodity"),
                    "coms": opp.get("commodities") or [],
                    "acre": opp.get("acreage"),
                    "legal": opp.get("legal"),
                    "twp": opp.get("township"),
                    "rng": opp.get("range"),
                    "sec": opp.get("section_summary"),
                    "mer": opp.get("meridian"),
                    "plss": opp.get("plss_key"),
                    "lat": opp.get("latitude"),
                    "lon": opp.get("longitude"),
                    "cycle": opp.get("offering_cycle"),
                    "bstart": opp.get("bidding_start_at"),
                    "bend": opp.get("bidding_end_at"),
                    "adead": opp.get("application_deadline"),
                    "award": opp.get("award_date"),
                    "minbid": opp.get("minimum_bid"),
                    "winbid": opp.get("winning_bid"),
                    "rent": opp.get("annual_rental"),
                    "royalty": opp.get("royalty_rate"),
                    "fee": opp.get("application_fee"),
                    "term": opp.get("primary_term_years"),
                    "ms": scores["mineral_potential_score"],
                    "ascore": scores["acquisition_readiness_score"],
                    "os": scores["overall_priority_score"],
                    "tier": scores["priority_tier"],
                    "expl": json.dumps(scores["explanation_json"]),
                    "url": opp.get("official_detail_url"),
                    "obs": now - timedelta(days=1),
                },
            )
            sid = source_ids.get(opp["source_key"])
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.opportunity_observations (
                      opportunity_id, source_id, source_record_key, observed_at,
                      raw_title, raw_reference_number, raw_status, normalized_status,
                      raw_opportunity_type, raw_commodity, raw_legal_description, acreage,
                      minimum_bid, winning_bid, application_deadline, bidding_end_at,
                      official_detail_url, raw_payload_json
                    ) VALUES (
                      CAST(:oid AS uuid), CAST(:sid AS uuid), :key, :obs,
                      :title, :ref, :life, :life,
                      :otype, :commodity, :legal, :acre,
                      :minbid, :winbid, :adead, :bend,
                      :url, CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    "oid": oid,
                    "sid": sid,
                    "key": opp["canonical_key"],
                    "obs": now - timedelta(days=1),
                    "title": opp["best_title"],
                    "ref": opp.get("reference_number"),
                    "life": opp["lifecycle_status"],
                    "otype": opp["opportunity_type"],
                    "commodity": opp.get("published_commodity"),
                    "legal": opp.get("legal"),
                    "acre": opp.get("acreage"),
                    "minbid": opp.get("minimum_bid"),
                    "winbid": opp.get("winning_bid"),
                    "adead": opp.get("application_deadline"),
                    "bend": opp.get("bidding_end_at"),
                    "url": opp.get("official_detail_url"),
                    "payload": json.dumps({"demo": True}),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.opportunity_events (
                      opportunity_id, event_type, event_at, title, description
                    ) VALUES (
                      CAST(:oid AS uuid), 'FIRST_DISCOVERED', :obs, 'First observed', :descr
                    )
                    """
                ),
                {"oid": oid, "obs": now - timedelta(days=10), "descr": opp["best_title"]},
            )
            if opp.get("mine_name"):
                conn.execute(
                    text(
                        """
                        INSERT INTO sitla_intel.mineral_evidence (
                          opportunity_id, evidence_type, mine_name, commodity_normalized,
                          inside_parcel, confidence, metadata_json
                        ) VALUES (
                          CAST(:oid AS uuid), 'OCCURRENCE', :mine, :com, true, 0.7,
                          CAST(:meta AS jsonb)
                        )
                        """
                    ),
                    {
                        "oid": oid,
                        "mine": opp["mine_name"],
                        "com": (opp.get("commodities") or ["Unknown"])[0],
                        "meta": json.dumps({"demo": True}),
                    },
                )
            conn.execute(
                text(
                    """
                    INSERT INTO sitla_intel.evidence_items (
                      opportunity_id, fact_key, fact_value_json, evidence_class,
                      source_id, source_url, extraction_method, confidence
                    ) VALUES (
                      CAST(:oid AS uuid), 'official_listing', CAST(:val AS jsonb), 'SITLA',
                      CAST(:sid AS uuid), :url, 'demo_seed', 0.95
                    )
                    """
                ),
                {
                    "oid": oid,
                    "val": json.dumps({"value": opp["best_title"]}),
                    "sid": sid,
                    "url": opp.get("official_detail_url"),
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
                    "expl": json.dumps(scores["explanation_json"]),
                },
            )
            if opp.get("winning_bid"):
                conn.execute(
                    text(
                        """
                        INSERT INTO sitla_intel.bid_results (
                          opportunity_id, winning_bidder, winning_bid, bid_per_acre,
                          outcome, result_date, source_url
                        ) VALUES (
                          CAST(:oid AS uuid), :bidder, :bid, :ppa, 'AWARDED', :rdate, :url
                        )
                        """
                    ),
                    {
                        "oid": oid,
                        "bidder": opp.get("winning_bidder") or "Unknown",
                        "bid": opp["winning_bid"],
                        "ppa": float(opp["winning_bid"]) / float(opp["acreage"] or 1),
                        "rdate": opp.get("award_date"),
                        "url": opp.get("official_detail_url"),
                    },
                )
            if opp.get("review_task"):
                titles = {
                    "RIGHTS_CLARITY": "Clarify mineral rights offered vs surface",
                    "DEADLINE_DILIGENCE": "Complete diligence before application deadline",
                    "COMMODITY_RIGHTS": "Confirm lithium/brine rights scope in offering docs",
                }
                conn.execute(
                    text(
                        """
                        INSERT INTO sitla_intel.review_tasks (
                          opportunity_id, task_type, priority, status, title, instructions
                        ) VALUES (
                          CAST(:oid AS uuid), :tt, 40, 'OPEN', :title, :instr
                        )
                        """
                    ),
                    {
                        "oid": oid,
                        "tt": opp["review_task"],
                        "title": titles.get(opp["review_task"], opp["review_task"]),
                        "instr": "Review official SITLA documents before bidding or applying. AI is not a decision-maker.",
                    },
                )
            created += 1

        return {"ok": True, "seeded": True, "demo_count": created}
