"""Seed realistic pilot-county demo opportunities for UI validation."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from mining_os.db import get_engine
from mining_os.tax_intel.scoring import compute_scores

log = logging.getLogger("mining_os.tax_intel.demo_seed")

PILOT_SOURCES = [
    {
        "source_key": "ut_beaver_tax_sale",
        "name": "Beaver County UT — Annual Tax Sale",
        "state": "UT",
        "county_fips": "49001",
        "county_name": "Beaver",
        "publication_scope": "SALE_ELIGIBLE_ONLY",
        "parser_kind": "HTML_TABLE",
        "listing_url": "https://www.beaver.utah.gov/",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Pilot: annual statutory tax-sale list. Does not cover all unpaid parcels.",
    },
    {
        "source_key": "ut_juab_tax_sale",
        "name": "Juab County UT — Tax Sale Notices",
        "state": "UT",
        "county_fips": "49023",
        "county_name": "Juab",
        "publication_scope": "AUCTION_ONLY",
        "parser_kind": "PDF",
        "listing_url": "https://juabcounty.gov/",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Pilot: sale-stage PDF notices.",
    },
    {
        "source_key": "ut_tooele_tax_sale",
        "name": "Tooele County UT — Tax Sale",
        "state": "UT",
        "county_fips": "49045",
        "county_name": "Tooele",
        "publication_scope": "SALE_ELIGIBLE_ONLY",
        "parser_kind": "CIVICPLUS_PAGE",
        "listing_url": "https://tooeleco.org/",
        "health_status": "STALE",
        "enabled": True,
        "manual_only": False,
        "notes": "Pilot: CivicPlus page; last success outside freshness SLA.",
    },
    {
        "source_key": "id_shoshone_tax_deed",
        "name": "Shoshone County ID — Tax Deed Auction",
        "state": "ID",
        "county_fips": "16079",
        "county_name": "Shoshone",
        "publication_scope": "TAX_DEEDED_ONLY",
        "parser_kind": "HTML_TABLE",
        "listing_url": "https://shoshonecounty.id.gov/tax-deed-auction/",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Pilot: county-held / tax-deed auction inventory.",
    },
    {
        "source_key": "id_custer_pending_deed",
        "name": "Custer County ID — Pending Tax Deed",
        "state": "ID",
        "county_fips": "16037",
        "county_name": "Custer",
        "publication_scope": "PENDING_TAX_DEED",
        "parser_kind": "HTML_TABLE",
        "listing_url": "https://www.co.custer.id.us/",
        "health_status": "MANUAL",
        "enabled": True,
        "manual_only": True,
        "notes": "Pilot: manual upload until structured listing is confirmed.",
    },
    {
        "source_key": "id_lemhi_property",
        "name": "Lemhi County ID — Property / Tax Records",
        "state": "ID",
        "county_fips": "16059",
        "county_name": "Lemhi",
        "publication_scope": "DELINQUENT_SUBSET",
        "parser_kind": "MANUAL_UPLOAD",
        "listing_url": "https://www.lemhicountyidaho.org/248/Lemhi-County-Online-Property-Records",
        "health_status": "MANUAL",
        "enabled": True,
        "manual_only": True,
        "notes": "Pilot: fixture + manual CSV upload until live property API is configured.",
    },
    {
        "source_key": "nv_white_pine_tax_sale",
        "name": "White Pine County NV — Tax Sale",
        "state": "NV",
        "county_fips": "32033",
        "county_name": "White Pine",
        "publication_scope": "AUCTION_ONLY",
        "parser_kind": "HTML_TABLE",
        "listing_url": "https://www.whitepinecounty.net/331/Tax-Sale",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Pilot: treasurer tax-sale list.",
    },
    {
        "source_key": "nv_nye_tax_sale",
        "name": "Nye County NV — Online Tax Sale Auctions",
        "state": "NV",
        "county_fips": "32023",
        "county_name": "Nye",
        "publication_scope": "AUCTION_ONLY",
        "parser_kind": "AUCTION_PLATFORM",
        "listing_url": "https://www.nyecountynv.gov/1118/2026-Online-Only-Tax-Sale-Auctions",
        "health_status": "HEALTHY",
        "enabled": True,
        "manual_only": False,
        "notes": "Pilot: county-linked auction platform.",
    },
    {
        "source_key": "nv_elko_trustee",
        "name": "Elko County NV — Trustee Property",
        "state": "NV",
        "county_fips": "32007",
        "county_name": "Elko",
        "publication_scope": "TRUST_INVENTORY",
        "parser_kind": "ARC_GIS_FEATURE_SERVER",
        "listing_url": "https://gis.elkocountynv.net/",
        "health_status": "DEGRADED",
        "enabled": True,
        "manual_only": False,
        "notes": "Pilot: trustee inventory + parcel GIS; intermittent GIS timeouts.",
    },
]


def _demo_opportunities(now: datetime) -> list[dict[str, Any]]:
    auction_soon = now + timedelta(days=18)
    auction_later = now + timedelta(days=52)
    return [
        {
            "canonical_key": "demo:ut:beaver:apn:01-0123-0001",
            "state": "UT",
            "county_fips": "49001",
            "county_name": "Beaver",
            "primary_apn": "01-0123-0001",
            "best_name": "Horn Silver Lode — Patented Claim Group",
            "property_address": "Unincorporated Beaver County, UT",
            "acreage": 41.2,
            "latitude": 38.3124,
            "longitude": -113.2218,
            "geometry_accuracy": "COUNTY_GIS",
            "plss_key": "UT T28S R12W Sec 14",
            "township": "0280S",
            "range": "0120W",
            "section": "014",
            "meridian": "26",
            "tax_delinquency_status": "DELINQUENT",
            "sale_lifecycle_status": "AUCTION_SCHEDULED",
            "auction_start_at": auction_soon,
            "amount_due": 18420.55,
            "minimum_bid": 18420.55,
            "years_delinquent": 4,
            "publication_scope": "SALE_ELIGIBLE_ONLY",
            "patent_classification": "CONFIRMED",
            "patent_confidence": 0.93,
            "mineral_signal": "HIGH",
            "mineral_confidence": 0.88,
            "access_status": "APPARENT_ROAD_CONTACT",
            "surface_mineral_unity_status": "UNKNOWN",
            "title_review_status": "NOT_REVIEWED",
            "environmental_risk_level": "LOW",
            "commodities": ["Silver", "Lead", "Zinc"],
            "score_inputs": {
                "mine_inside_parcel": True,
                "commodity_evidence": True,
                "historic_production": True,
                "patent_tied_to_mine": True,
                "mineral_survey_coherence": True,
                "technical_docs": True,
                "favorable_district": True,
                "nearby_active_claims": True,
                "nearby_occurrences": True,
                "geological_support": True,
                "clear_sale_stage": True,
                "patent_confirmed": True,
                "geometry_confirmed": True,
                "bid_or_amount_known": True,
                "owner_or_legal_known": True,
                "apparent_access": True,
                "source_fresh": True,
                "data_complete": True,
                "title_not_reviewed": True,
            },
            "owner": "SILVER RANGE HOLDINGS LLC",
            "legal": "Mineral Survey No. 4127, Horn Silver Lode and Fraction, T28S R12W Sec 14 SLM",
            "mineral_survey": "4127",
            "patent_number": "1123456",
            "mine_name": "Horn Silver Mine",
            "source_key": "ut_beaver_tax_sale",
            "review_task": None,
        },
        {
            "canonical_key": "demo:nv:white_pine:apn:005-220-08",
            "state": "NV",
            "county_fips": "32033",
            "county_name": "White Pine",
            "primary_apn": "005-220-08",
            "best_name": "Robinson District Placer Tract",
            "property_address": "Near Ruth, NV",
            "acreage": 18.6,
            "latitude": 39.2781,
            "longitude": -114.9912,
            "geometry_accuracy": "STATE_AGGREGATED_PARCEL",
            "plss_key": "NV T16N R63E Sec 22",
            "township": "0160N",
            "range": "0630E",
            "section": "022",
            "meridian": "21",
            "tax_delinquency_status": "DELINQUENT",
            "sale_lifecycle_status": "AUCTION_SCHEDULED",
            "auction_start_at": auction_later,
            "amount_due": 9620.00,
            "minimum_bid": 10000.00,
            "years_delinquent": 3,
            "publication_scope": "AUCTION_ONLY",
            "patent_classification": "PROBABLE",
            "patent_confidence": 0.78,
            "mineral_signal": "HIGH",
            "mineral_confidence": 0.74,
            "access_status": "APPARENT_PUBLIC_ACCESS",
            "surface_mineral_unity_status": "UNKNOWN",
            "title_review_status": "NOT_REVIEWED",
            "environmental_risk_level": "MEDIUM",
            "commodities": ["Copper", "Gold"],
            "score_inputs": {
                "mine_inside_parcel": False,
                "commodity_evidence": True,
                "historic_production": True,
                "patent_tied_to_mine": True,
                "mineral_survey_coherence": True,
                "favorable_district": True,
                "nearby_active_claims": True,
                "nearby_occurrences": True,
                "geological_support": True,
                "clear_sale_stage": True,
                "patent_probable": True,
                "geometry_confirmed": True,
                "bid_or_amount_known": True,
                "owner_or_legal_known": True,
                "apparent_access": True,
                "source_fresh": True,
                "data_complete": False,
                "title_not_reviewed": True,
                "approx_geometry": False,
            },
            "owner": "DESERT COPPER PARTNERS",
            "legal": "Patented mining claim; MS 2891; Robinson Mining District",
            "mineral_survey": "2891",
            "patent_number": "987654",
            "mine_name": "Veteran Prospect",
            "source_key": "nv_white_pine_tax_sale",
            "review_task": "PATENT_MATCH_AMBIGUOUS",
        },
        {
            "canonical_key": "demo:id:shoshone:apn:RP47N04E150600",
            "state": "ID",
            "county_fips": "16079",
            "county_name": "Shoshone",
            "primary_apn": "RP47N04E150600",
            "best_name": "Bunker Hill Adjacent Mill Site Parcel",
            "property_address": "Kellogg vicinity, ID",
            "acreage": 5.4,
            "latitude": 47.5389,
            "longitude": -116.1195,
            "geometry_accuracy": "AUTHORITATIVE_PARCEL",
            "plss_key": "ID T48N R2E Sec 15",
            "township": "0480N",
            "range": "0020E",
            "section": "015",
            "meridian": "01",
            "tax_delinquency_status": "DELINQUENT",
            "sale_lifecycle_status": "COUNTY_OR_TRUSTEE_HELD",
            "auction_start_at": None,
            "amount_due": 4215.75,
            "minimum_bid": 4500.00,
            "years_delinquent": 6,
            "publication_scope": "TAX_DEEDED_ONLY",
            "patent_classification": "POSSIBLE",
            "patent_confidence": 0.52,
            "mineral_signal": "MEDIUM",
            "mineral_confidence": 0.61,
            "access_status": "NO_APPARENT_MAPPED_ACCESS",
            "surface_mineral_unity_status": "UNKNOWN",
            "title_review_status": "NOT_REVIEWED",
            "environmental_risk_level": "HIGH",
            "commodities": ["Lead", "Silver", "Zinc"],
            "score_inputs": {
                "mine_inside_parcel": False,
                "commodity_evidence": True,
                "historic_production": False,
                "patent_tied_to_mine": False,
                "mineral_survey_coherence": False,
                "favorable_district": True,
                "nearby_active_claims": False,
                "nearby_occurrences": True,
                "geological_support": True,
                "clear_sale_stage": True,
                "patent_probable": False,
                "geometry_confirmed": True,
                "bid_or_amount_known": True,
                "owner_or_legal_known": True,
                "apparent_access": False,
                "no_mapped_access": True,
                "source_fresh": True,
                "data_complete": True,
                "severe_environmental": True,
                "title_not_reviewed": True,
            },
            "owner": "SHOSHONE COUNTY",
            "legal": "Part of patented mill-site tract; Coeur d'Alene Mining District",
            "mineral_survey": None,
            "patent_number": None,
            "mine_name": "Bunker Hill (nearby)",
            "source_key": "id_shoshone_tax_deed",
            "review_task": "ACCESS_UNKNOWN",
        },
        {
            "canonical_key": "demo:nv:nye:apn:001-081-12",
            "state": "NV",
            "county_fips": "32023",
            "county_name": "Nye",
            "primary_apn": "001-081-12",
            "best_name": "Tonopah Flats Residential Lot",
            "property_address": "Tonopah, NV",
            "acreage": 0.25,
            "latitude": 38.0671,
            "longitude": -117.2301,
            "geometry_accuracy": "COUNTY_GIS",
            "plss_key": "NV T3N R42E Sec 33",
            "township": "0030N",
            "range": "0420E",
            "section": "033",
            "meridian": "21",
            "tax_delinquency_status": "DELINQUENT",
            "sale_lifecycle_status": "SALE_ELIGIBLE",
            "auction_start_at": auction_later,
            "amount_due": 2180.00,
            "minimum_bid": 2180.00,
            "years_delinquent": 2,
            "publication_scope": "AUCTION_ONLY",
            "patent_classification": "UNLIKELY",
            "patent_confidence": 0.12,
            "mineral_signal": "LOW",
            "mineral_confidence": 0.1,
            "access_status": "APPARENT_PUBLIC_ACCESS",
            "surface_mineral_unity_status": "UNKNOWN",
            "title_review_status": "NOT_REVIEWED",
            "environmental_risk_level": "LOW",
            "commodities": [],
            "score_inputs": {
                "clear_sale_stage": True,
                "geometry_confirmed": True,
                "bid_or_amount_known": True,
                "owner_or_legal_known": True,
                "apparent_access": True,
                "source_fresh": True,
                "data_complete": True,
            },
            "owner": "JANE Q PUBLIC",
            "legal": "Lot 12, Block 8, Tonopah Townsite",
            "mineral_survey": None,
            "patent_number": None,
            "mine_name": None,
            "source_key": "nv_nye_tax_sale",
            "review_task": None,
        },
        {
            "canonical_key": "demo:ut:juab:apn:XA-2210-A",
            "state": "UT",
            "county_fips": "49023",
            "county_name": "Juab",
            "primary_apn": "XA-2210-A",
            "best_name": "Dragon Mine Area Claim Fragment",
            "property_address": "Tintic District vicinity, UT",
            "acreage": 12.0,
            "latitude": 39.9215,
            "longitude": -112.1088,
            "geometry_accuracy": "PLSS_APPROXIMATION",
            "plss_key": "UT T10S R2W Sec 28",
            "township": "0100S",
            "range": "0020W",
            "section": "028",
            "meridian": "26",
            "tax_delinquency_status": "DELINQUENT",
            "sale_lifecycle_status": "NOTICE_PUBLISHED",
            "auction_start_at": auction_soon + timedelta(days=10),
            "amount_due": 7450.00,
            "minimum_bid": None,
            "years_delinquent": 3,
            "publication_scope": "AUCTION_ONLY",
            "patent_classification": "POSSIBLE",
            "patent_confidence": 0.48,
            "mineral_signal": "HIGH",
            "mineral_confidence": 0.7,
            "access_status": "UNKNOWN",
            "surface_mineral_unity_status": "UNKNOWN",
            "title_review_status": "NOT_REVIEWED",
            "environmental_risk_level": "MEDIUM",
            "commodities": ["Fluorspar", "Beryllium"],
            "score_inputs": {
                "mine_inside_parcel": False,
                "commodity_evidence": True,
                "historic_production": True,
                "patent_tied_to_mine": False,
                "mineral_survey_coherence": True,
                "favorable_district": True,
                "nearby_active_claims": True,
                "nearby_occurrences": True,
                "geological_support": True,
                "clear_sale_stage": True,
                "geometry_confirmed": False,
                "approx_geometry": True,
                "bid_or_amount_known": True,
                "owner_or_legal_known": True,
                "source_fresh": True,
                "title_not_reviewed": True,
            },
            "owner": "TINTIC LEGACY TRUST",
            "legal": "Portion of patented claims; Mineral Survey candidate MS 1904; Tintic Mining District",
            "mineral_survey": "1904",
            "patent_number": None,
            "mine_name": "Dragon Mine (associated)",
            "source_key": "ut_juab_tax_sale",
            "review_task": "MINERAL_SURVEY_CANDIDATE",
        },
        {
            "canonical_key": "demo:nv:elko:apn:006-540-18",
            "state": "NV",
            "county_fips": "32007",
            "county_name": "Elko",
            "primary_apn": "006-540-18",
            "best_name": "Tuscarora District Lode Parcel",
            "property_address": "Tuscarora vicinity, NV",
            "acreage": 20.1,
            "latitude": 41.3012,
            "longitude": -116.2215,
            "geometry_accuracy": "COUNTY_GIS",
            "plss_key": "NV T39N R51E Sec 9",
            "township": "0390N",
            "range": "0510E",
            "section": "009",
            "meridian": "21",
            "tax_delinquency_status": "DELINQUENT",
            "sale_lifecycle_status": "COUNTY_OR_TRUSTEE_HELD",
            "auction_start_at": None,
            "amount_due": 11250.00,
            "minimum_bid": 12500.00,
            "years_delinquent": 5,
            "publication_scope": "TRUST_INVENTORY",
            "patent_classification": "CONFIRMED",
            "patent_confidence": 0.9,
            "mineral_signal": "HIGH",
            "mineral_confidence": 0.85,
            "access_status": "POSSIBLE_PRIVATE_ACCESS",
            "surface_mineral_unity_status": "UNKNOWN",
            "title_review_status": "NOT_REVIEWED",
            "environmental_risk_level": "LOW",
            "commodities": ["Gold", "Silver"],
            "score_inputs": {
                "mine_inside_parcel": True,
                "commodity_evidence": True,
                "historic_production": True,
                "patent_tied_to_mine": True,
                "mineral_survey_coherence": True,
                "technical_docs": False,
                "favorable_district": True,
                "nearby_active_claims": True,
                "nearby_occurrences": True,
                "geological_support": True,
                "clear_sale_stage": True,
                "patent_confirmed": True,
                "geometry_confirmed": True,
                "bid_or_amount_known": True,
                "owner_or_legal_known": True,
                "apparent_access": False,
                "source_fresh": False,
                "source_stale": True,
                "data_complete": True,
                "title_not_reviewed": True,
            },
            "owner": "ELKO COUNTY TREASURER, TRUSTEE",
            "legal": "MS 3341 Independence Lode; Tuscarora Mining District",
            "mineral_survey": "3341",
            "patent_number": "556677",
            "mine_name": "Independence Mine",
            "source_key": "nv_elko_trustee",
            "review_task": "SOURCE_STALE",
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
                      WHERE table_schema = 'tax_intel'
                        AND table_name = 'tax_opportunities'
                    )
                    """
                )
            ).scalar()
        )


def _sync_source_adapter_config(conn) -> None:
    """Keep pilot source adapter config current even after initial demo seed."""
    has_adapter_col = conn.execute(
        text(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'tax_intel'
                AND table_name = 'source_registry'
                AND column_name = 'adapter_class'
            )
            """
        )
    ).scalar()
    for src in PILOT_SOURCES:
        cfg = json.dumps(
            {
                "use_fixture": True,
                "fixture_file": f"{src['source_key']}.json",
                "refresh_cron": "0 6 * * *",
                "allow_live_html": False,
                "live_candidate_url": src.get("listing_url"),
                "live_status": "pending_validation",
            }
        )
        if has_adapter_col:
            conn.execute(
                text(
                    """
                    UPDATE tax_intel.source_registry
                    SET configuration_json = CAST(:cfg AS jsonb),
                        adapter_class = 'FixtureJsonAdapter',
                        enabled = :enabled,
                        manual_only = :manual_only,
                        notes = :notes,
                        updated_at = now()
                    WHERE source_key = :source_key
                    """
                ),
                {
                    "cfg": cfg,
                    "enabled": src["enabled"],
                    "manual_only": src["manual_only"],
                    "notes": src["notes"],
                    "source_key": src["source_key"],
                },
            )
        else:
            conn.execute(
                text(
                    """
                    UPDATE tax_intel.source_registry
                    SET configuration_json = CAST(:cfg AS jsonb),
                        enabled = :enabled,
                        manual_only = :manual_only,
                        notes = :notes,
                        updated_at = now()
                    WHERE source_key = :source_key
                    """
                ),
                {
                    "cfg": cfg,
                    "enabled": src["enabled"],
                    "manual_only": src["manual_only"],
                    "notes": src["notes"],
                    "source_key": src["source_key"],
                },
            )


def ensure_demo_seed(account_id: int) -> dict[str, Any]:
    """Idempotently seed pilot sources + demo opportunities for an account."""
    if not schema_ready():
        return {"ok": False, "error": "tax_intel schema not migrated", "seeded": False}

    eng = get_engine()
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM tax_intel.tax_opportunities
                WHERE account_id = :aid AND is_demo = true
                """
            ),
            {"aid": account_id},
        ).scalar()
        if existing and int(existing) > 0:
            _sync_source_adapter_config(conn)
            return {"ok": True, "seeded": False, "demo_count": int(existing)}

        source_ids: dict[str, str] = {}
        for src in PILOT_SOURCES:
            row = conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.source_registry (
                      source_key, name, source_category, state, county_fips, county_name,
                      listing_url, parser_kind, publication_scope, enabled, is_official,
                      manual_only, freshness_sla_hours, health_status, notes,
                      configuration_json, adapter_class,
                      last_success_at, last_failure_at
                    ) VALUES (
                      :source_key, :name, 'TAX', :state, :county_fips, :county_name,
                      :listing_url, :parser_kind, :publication_scope, :enabled, true,
                      :manual_only, 168, :health_status, :notes,
                      CAST(:configuration_json AS jsonb), 'FixtureJsonAdapter',
                      CASE WHEN :health_status = 'HEALTHY' THEN now() - interval '2 days'
                           WHEN :health_status = 'STALE' THEN now() - interval '20 days'
                           WHEN :health_status = 'DEGRADED' THEN now() - interval '5 days'
                           ELSE NULL END,
                      CASE WHEN :health_status IN ('FAILED', 'DEGRADED') THEN now() - interval '1 day'
                           ELSE NULL END
                    )
                    ON CONFLICT (source_key) DO UPDATE SET
                      name = EXCLUDED.name,
                      health_status = EXCLUDED.health_status,
                      notes = EXCLUDED.notes,
                      enabled = EXCLUDED.enabled,
                      configuration_json = EXCLUDED.configuration_json,
                      adapter_class = EXCLUDED.adapter_class,
                      updated_at = now()
                    RETURNING id::text, source_key
                    """
                ),
                {
                    **src,
                    "configuration_json": json.dumps(
                        {
                            "use_fixture": True,
                            "fixture_file": f"{src['source_key']}.json",
                            "refresh_cron": "0 6 * * *",
                            "allow_live_html": False,
                            "live_candidate_url": src.get("listing_url"),
                            "live_status": "pending_validation",
                        }
                    ),
                },
            ).mappings().first()
            if row:
                source_ids[row["source_key"]] = row["id"]

        created = 0
        for opp in _demo_opportunities(now):
            scores = compute_scores(opp["score_inputs"])
            completeness = 80.0 if opp["score_inputs"].get("data_complete") else 55.0
            freshness = 100.0 if opp["score_inputs"].get("source_fresh") else 50.0
            opp_id = str(uuid4())
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.tax_opportunities (
                      id, account_id, canonical_key, state, county_fips, county_name,
                      primary_apn, best_name, property_address, acreage, latitude, longitude,
                      geometry_accuracy, plss_key, township, range, section, meridian,
                      tax_delinquency_status, sale_lifecycle_status,
                      first_observed_at, last_observed_at, next_event_date, auction_start_at,
                      amount_due, minimum_bid, years_delinquent, publication_scope,
                      patent_classification, patent_confidence, mineral_signal, mineral_confidence,
                      access_status, surface_mineral_unity_status, title_review_status,
                      environmental_risk_level, data_completeness_score, source_freshness_score,
                      mineral_potential_score, acquisition_readiness_score, overall_priority_score,
                      priority_tier, review_status, is_active, is_demo, commodities,
                      score_explanation_json, summary_json
                    ) VALUES (
                      CAST(:id AS uuid), :account_id, :canonical_key, :state, :county_fips, :county_name,
                      :primary_apn, :best_name, :property_address, :acreage, :latitude, :longitude,
                      :geometry_accuracy, :plss_key, :township, :range, :section, :meridian,
                      :tax_delinquency_status, :sale_lifecycle_status,
                      :first_observed_at, :last_observed_at, :next_event_date, :auction_start_at,
                      :amount_due, :minimum_bid, :years_delinquent, :publication_scope,
                      :patent_classification, :patent_confidence, :mineral_signal, :mineral_confidence,
                      :access_status, :surface_mineral_unity_status, :title_review_status,
                      :environmental_risk_level, :data_completeness_score, :source_freshness_score,
                      :mineral_potential_score, :acquisition_readiness_score, :overall_priority_score,
                      :priority_tier, 'OPEN', true, true, CAST(:commodities AS text[]),
                      CAST(:score_explanation_json AS jsonb), CAST(:summary_json AS jsonb)
                    )
                    """
                ),
                {
                    "id": opp_id,
                    "account_id": account_id,
                    "canonical_key": opp["canonical_key"],
                    "state": opp["state"],
                    "county_fips": opp["county_fips"],
                    "county_name": opp["county_name"],
                    "primary_apn": opp["primary_apn"],
                    "best_name": opp["best_name"],
                    "property_address": opp["property_address"],
                    "acreage": opp["acreage"],
                    "latitude": opp["latitude"],
                    "longitude": opp["longitude"],
                    "geometry_accuracy": opp["geometry_accuracy"],
                    "plss_key": opp["plss_key"],
                    "township": opp["township"],
                    "range": opp["range"],
                    "section": opp["section"],
                    "meridian": opp["meridian"],
                    "tax_delinquency_status": opp["tax_delinquency_status"],
                    "sale_lifecycle_status": opp["sale_lifecycle_status"],
                    "first_observed_at": now - timedelta(days=120),
                    "last_observed_at": now - timedelta(days=1),
                    "next_event_date": opp["auction_start_at"].date() if opp["auction_start_at"] else None,
                    "auction_start_at": opp["auction_start_at"],
                    "amount_due": opp["amount_due"],
                    "minimum_bid": opp["minimum_bid"],
                    "years_delinquent": opp["years_delinquent"],
                    "publication_scope": opp["publication_scope"],
                    "patent_classification": opp["patent_classification"],
                    "patent_confidence": opp["patent_confidence"],
                    "mineral_signal": opp["mineral_signal"],
                    "mineral_confidence": opp["mineral_confidence"],
                    "access_status": opp["access_status"],
                    "surface_mineral_unity_status": opp["surface_mineral_unity_status"],
                    "title_review_status": opp["title_review_status"],
                    "environmental_risk_level": opp["environmental_risk_level"],
                    "data_completeness_score": completeness,
                    "source_freshness_score": freshness,
                    "mineral_potential_score": scores["mineral_potential_score"],
                    "acquisition_readiness_score": scores["acquisition_readiness_score"],
                    "overall_priority_score": scores["overall_priority_score"],
                    "priority_tier": scores["priority_tier"],
                    "commodities": "{"
                    + ",".join('"' + c.replace('"', '\\"') + '"' for c in opp["commodities"])
                    + "}",
                    "score_explanation_json": json.dumps(scores["explanation_json"]),
                    "summary_json": json.dumps(
                        {
                            "disclaimer": (
                                "Patent status and tax-sale status do not establish current mineral ownership. "
                                "Current deeds, reservations, severances, liens, and title history require separate review."
                            ),
                            "coverage_note": "Demo fixture — publicly available records from enabled pilot sources.",
                        }
                    ),
                },
            )
            created += 1

            src_id = source_ids.get(opp["source_key"])
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
                {"oid": opp_id, "sid": src_id, "raw": opp["primary_apn"], "norm": opp["primary_apn"]},
            )
            if opp.get("mineral_survey"):
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.parcel_identifiers (
                          opportunity_id, source_id, identifier_type, raw_value, normalized_value, is_primary
                        ) VALUES (
                          CAST(:oid AS uuid), CAST(:sid AS uuid), 'MINERAL_SURVEY_NUMBER', :raw, :norm, false
                        )
                        """
                    ),
                    {
                        "oid": opp_id,
                        "sid": src_id,
                        "raw": f"MS {opp['mineral_survey']}",
                        "norm": opp["mineral_survey"],
                    },
                )

            obs_id = str(uuid4())
            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.tax_observations (
                      id, opportunity_id, source_id, source_record_key, observed_at, effective_date,
                      raw_owner_name, normalized_owner_name, raw_apn, normalized_apn,
                      raw_legal_description, property_address, raw_status, normalized_status,
                      amount_due, minimum_bid, years_delinquent, sale_date, raw_payload_json
                    ) VALUES (
                      CAST(:id AS uuid), CAST(:oid AS uuid), CAST(:sid AS uuid), :key, :observed_at, :effective_date,
                      :owner, :owner, :apn, :apn,
                      :legal, :addr, :raw_status, :norm_status,
                      :amount_due, :minimum_bid, :years, :sale_date, CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    "id": obs_id,
                    "oid": opp_id,
                    "sid": src_id,
                    "key": opp["canonical_key"],
                    "observed_at": now - timedelta(days=1),
                    "effective_date": date.today() - timedelta(days=1),
                    "owner": opp["owner"],
                    "apn": opp["primary_apn"],
                    "legal": opp["legal"],
                    "addr": opp["property_address"],
                    "raw_status": opp["sale_lifecycle_status"].replace("_", " ").title(),
                    "norm_status": opp["sale_lifecycle_status"],
                    "amount_due": opp["amount_due"],
                    "minimum_bid": opp["minimum_bid"],
                    "years": opp["years_delinquent"],
                    "sale_date": opp["auction_start_at"].date() if opp["auction_start_at"] else None,
                    "payload": json.dumps({"demo": True, "source_key": opp["source_key"]}),
                },
            )

            for etype, title, when in [
                ("DISCOVERED", "First observed on county source", now - timedelta(days=120)),
                ("DELINQUENT", "Listed as delinquent", now - timedelta(days=90)),
                (
                    opp["sale_lifecycle_status"],
                    f"Current stage: {opp['sale_lifecycle_status'].replace('_', ' ').title()}",
                    now - timedelta(days=1),
                ),
            ]:
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.tax_events (
                          opportunity_id, event_type, event_at, source_observation_id, title, description
                        ) VALUES (
                          CAST(:oid AS uuid), :etype, :event_at, CAST(:obs AS uuid), :title, :descr
                        )
                        """
                    ),
                    {
                        "oid": opp_id,
                        "etype": etype,
                        "event_at": when,
                        "obs": obs_id,
                        "title": title,
                        "descr": opp["legal"],
                    },
                )

            patent_id = None
            if opp.get("patent_number") or opp.get("mineral_survey"):
                patent_id = str(uuid4())
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.patent_records (
                          id, source_id, patent_number, accession_number, document_type, state, county_name,
                          patentee_name, total_acres, township, range, section, meridian,
                          legal_description, mineral_survey_numbers, claim_names, document_url
                        ) VALUES (
                          CAST(:id AS uuid), CAST(:sid AS uuid), :patent_number, :accession, 'MINERAL_PATENT',
                          :state, :county, :patentee, :acres, :twp, :rng, :sec, :mer,
                          :legal, CAST(:ms AS text[]), CAST(:claims AS text[]), :url
                        )
                        """
                    ),
                    {
                        "id": patent_id,
                        "sid": src_id,
                        "patent_number": opp.get("patent_number"),
                        "accession": opp.get("patent_number"),
                        "state": opp["state"],
                        "county": opp["county_name"],
                        "patentee": "Historic patentee (demo)",
                        "acres": opp["acreage"],
                        "twp": opp["township"],
                        "rng": opp["range"],
                        "sec": opp["section"],
                        "mer": opp["meridian"],
                        "legal": opp["legal"],
                        "ms": (
                            '{"' + opp["mineral_survey"].replace('"', '\\"') + '"}'
                            if opp.get("mineral_survey")
                            else "{}"
                        ),
                        "claims": '{"' + opp["best_name"].replace('"', '\\"') + '"}',
                        "url": "https://glorecords.blm.gov/",
                    },
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.opportunity_patent_matches (
                          opportunity_id, patent_record_id, match_status, match_confidence, match_method,
                          mineral_survey_score, claim_name_score, plss_score, evidence_summary_json
                        ) VALUES (
                          CAST(:oid AS uuid), CAST(:pid AS uuid), :status, :conf, 'deterministic_v1',
                          :ms, :claim, :plss, CAST(:summary AS jsonb)
                        )
                        """
                    ),
                    {
                        "oid": opp_id,
                        "pid": patent_id,
                        "status": opp["patent_classification"],
                        "conf": opp["patent_confidence"],
                        "ms": 40 if opp.get("mineral_survey") else 0,
                        "claim": 20 if opp.get("mineral_survey") else 5,
                        "plss": 15,
                        "summary": json.dumps(
                            {
                                "signals": {
                                    "mineral_survey_exact": bool(opp.get("mineral_survey")),
                                    "plss_match": True,
                                    "claim_name": bool(opp.get("best_name")),
                                },
                                "requires_review": opp["patent_classification"]
                                in {"PROBABLE", "POSSIBLE"},
                            }
                        ),
                    },
                )

            if opp.get("mine_name"):
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.mineral_evidence (
                          opportunity_id, source_id, evidence_type, mine_name, commodity_normalized,
                          production_status, distance_meters, inside_parcel, confidence, source_url
                        ) VALUES (
                          CAST(:oid AS uuid), CAST(:sid AS uuid), 'MINE_OCCURRENCE', :mine, :commodity,
                          :prod, :dist, :inside, :conf, :url
                        )
                        """
                    ),
                    {
                        "oid": opp_id,
                        "sid": src_id,
                        "mine": opp["mine_name"],
                        "commodity": (opp["commodities"][0] if opp["commodities"] else None),
                        "prod": "CONFIRMED_ON_PROPERTY"
                        if opp["score_inputs"].get("mine_inside_parcel")
                        else "NEARBY_PRODUCTION",
                        "dist": 0 if opp["score_inputs"].get("mine_inside_parcel") else 850,
                        "inside": bool(opp["score_inputs"].get("mine_inside_parcel")),
                        "conf": opp["mineral_confidence"],
                        "url": "https://mrdata.usgs.gov/mrds/",
                    },
                )

            if opp["score_inputs"].get("nearby_active_claims"):
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.claim_context (
                          opportunity_id, mlrs_serial_number, claim_name, claim_status, claim_type,
                          distance_meters, inside_parcel
                        ) VALUES (
                          CAST(:oid AS uuid), :serial, :name, 'ACTIVE', 'Lode', :dist, false
                        )
                        """
                    ),
                    {
                        "oid": opp_id,
                        "serial": f"{opp['state']}DEMO{opp['primary_apn'][-4:]}",
                        "name": f"Nearby claim near {opp['best_name'][:24]}",
                        "dist": 420,
                    },
                )

            evidence_facts = [
                ("primary_apn", opp["primary_apn"], "PARCEL", 0.95),
                ("sale_lifecycle_status", opp["sale_lifecycle_status"], "TAX", 0.9),
                ("amount_due", opp["amount_due"], "TAX", 0.85),
                ("patent_classification", opp["patent_classification"], "PATENT", opp["patent_confidence"]),
                ("legal_description", opp["legal"], "LEGAL_DESCRIPTION", 0.8),
            ]
            if opp.get("mineral_survey"):
                evidence_facts.append(
                    ("mineral_survey_number", opp["mineral_survey"], "MINERAL_SURVEY", 0.9)
                )
            for fact_key, value, eclass, conf in evidence_facts:
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.evidence_items (
                          opportunity_id, fact_key, fact_value_json, evidence_class, source_id,
                          source_url, extraction_method, confidence, is_primary
                        ) VALUES (
                          CAST(:oid AS uuid), :fact_key, CAST(:value AS jsonb), :eclass, CAST(:sid AS uuid),
                          :url, 'demo_seed', :conf, true
                        )
                        """
                    ),
                    {
                        "oid": opp_id,
                        "fact_key": fact_key,
                        "value": json.dumps({"value": value}),
                        "eclass": eclass,
                        "sid": src_id,
                        "url": PILOT_SOURCES[0]["listing_url"],
                        "conf": conf,
                    },
                )

            conn.execute(
                text(
                    """
                    INSERT INTO tax_intel.score_snapshots (
                      opportunity_id, score_version, mineral_potential_score,
                      acquisition_readiness_score, risk_penalty, overall_priority_score,
                      priority_tier, explanation_json
                    ) VALUES (
                      CAST(:oid AS uuid), :ver, :min_s, :acq_s, :pen, :ovr, :tier, CAST(:expl AS jsonb)
                    )
                    """
                ),
                {
                    "oid": opp_id,
                    "ver": scores["score_version"],
                    "min_s": scores["mineral_potential_score"],
                    "acq_s": scores["acquisition_readiness_score"],
                    "pen": scores["risk_penalty"],
                    "ovr": scores["overall_priority_score"],
                    "tier": scores["priority_tier"],
                    "expl": json.dumps(scores["explanation_json"]),
                },
            )

            if opp.get("review_task"):
                conn.execute(
                    text(
                        """
                        INSERT INTO tax_intel.review_tasks (
                          opportunity_id, task_type, priority, status, title, instructions
                        ) VALUES (
                          CAST(:oid AS uuid), :ttype, 70, 'OPEN', :title, :instr
                        )
                        """
                    ),
                    {
                        "oid": opp_id,
                        "ttype": opp["review_task"],
                        "title": opp["review_task"].replace("_", " ").title(),
                        "instr": "Review evidence ledger and confirm or reject the current classification.",
                    },
                )

        log.info("Seeded %d demo tax opportunities for account %s", created, account_id)
        return {"ok": True, "seeded": True, "demo_count": created}
