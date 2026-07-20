-- SITLA Intelligence — additive isolation schema
-- Never mutates existing Mining OS tables.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS sitla_intel;

CREATE TABLE IF NOT EXISTS sitla_intel.sources (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_key          TEXT NOT NULL UNIQUE,
  name                TEXT NOT NULL,
  source_category     TEXT NOT NULL DEFAULT 'SITLA',
  base_url            TEXT,
  listing_url         TEXT,
  parser_kind         TEXT NOT NULL DEFAULT 'MANUAL_UPLOAD',
  adapter_class       TEXT,
  enabled             BOOLEAN NOT NULL DEFAULT false,
  manual_only         BOOLEAN NOT NULL DEFAULT false,
  refresh_schedule    TEXT,
  freshness_sla_hours INT NOT NULL DEFAULT 168,
  health_status       TEXT NOT NULL DEFAULT 'UNCONFIGURED',
  last_success_at     TIMESTAMPTZ,
  last_failure_at     TIMESTAMPTZ,
  consecutive_failures INT NOT NULL DEFAULT 0,
  notes               TEXT,
  configuration_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.source_runs (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id           UUID REFERENCES sitla_intel.sources(id) ON DELETE SET NULL,
  run_type            TEXT NOT NULL DEFAULT 'LISTING_REFRESH',
  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at        TIMESTAMPTZ,
  status              TEXT NOT NULL DEFAULT 'running',
  trigger_type        TEXT NOT NULL DEFAULT 'manual',
  records_discovered  INT NOT NULL DEFAULT 0,
  records_created     INT NOT NULL DEFAULT 0,
  records_updated     INT NOT NULL DEFAULT 0,
  records_unchanged   INT NOT NULL DEFAULT 0,
  records_failed      INT NOT NULL DEFAULT 0,
  error_message       TEXT,
  metrics_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.raw_artifacts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id           UUID REFERENCES sitla_intel.sources(id) ON DELETE SET NULL,
  source_run_id       UUID REFERENCES sitla_intel.source_runs(id) ON DELETE SET NULL,
  source_url          TEXT,
  retrieved_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  filename            TEXT,
  media_type          TEXT,
  storage_uri         TEXT,
  sha256              TEXT,
  byte_size           INT,
  metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.opportunities (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id                  BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
  canonical_key               TEXT NOT NULL,
  reference_number            TEXT,
  lease_number                TEXT,
  best_title                  TEXT,
  opportunity_type            TEXT NOT NULL DEFAULT 'UNKNOWN',
  raw_opportunity_type        TEXT,
  lifecycle_status            TEXT NOT NULL DEFAULT 'DISCOVERED',
  raw_status                  TEXT,
  is_active                   BOOLEAN NOT NULL DEFAULT true,
  is_historical               BOOLEAN NOT NULL DEFAULT false,
  is_demo                     BOOLEAN NOT NULL DEFAULT false,
  county_name                 TEXT,
  county_fips                 TEXT,
  published_commodity         TEXT,
  published_resource_text     TEXT,
  commodities                 TEXT[] NOT NULL DEFAULT '{}',
  acreage                     DOUBLE PRECISION,
  legal_description_raw       TEXT,
  township                    TEXT,
  range                       TEXT,
  section_summary             TEXT,
  meridian                    TEXT,
  plss_key                    TEXT,
  latitude                    DOUBLE PRECISION,
  longitude                   DOUBLE PRECISION,
  geometry_accuracy           TEXT NOT NULL DEFAULT 'UNKNOWN',
  offering_cycle              TEXT,
  announcement_date           DATE,
  nomination_deadline         TIMESTAMPTZ,
  application_deadline        TIMESTAMPTZ,
  bidding_start_at            TIMESTAMPTZ,
  bidding_end_at              TIMESTAMPTZ,
  award_date                  DATE,
  minimum_bid                 NUMERIC(14, 2),
  winning_bid                 NUMERIC(14, 2),
  annual_rental               NUMERIC(14, 2),
  royalty_rate                TEXT,
  application_fee             NUMERIC(14, 2),
  bond_amount                 NUMERIC(14, 2),
  primary_term_years          INT,
  rights_clarity              TEXT NOT NULL DEFAULT 'UNKNOWN',
  surface_rights_status       TEXT NOT NULL DEFAULT 'UNKNOWN',
  mineral_rights_status       TEXT NOT NULL DEFAULT 'UNKNOWN',
  mineral_potential_score     DOUBLE PRECISION NOT NULL DEFAULT 0,
  acquisition_readiness_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  overall_priority_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
  priority_tier               TEXT NOT NULL DEFAULT 'E',
  data_completeness_score     DOUBLE PRECISION NOT NULL DEFAULT 0,
  source_freshness_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
  score_explanation_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  review_status               TEXT NOT NULL DEFAULT 'OPEN',
  official_detail_url         TEXT,
  external_bid_url            TEXT,
  enrichment_status           TEXT NOT NULL DEFAULT 'pending',
  last_enriched_at            TIMESTAMPTZ,
  first_observed_at           TIMESTAMPTZ,
  last_observed_at            TIMESTAMPTZ,
  watch_count                 INT NOT NULL DEFAULT 0,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sitla_opp_account_canonical
  ON sitla_intel.opportunities (account_id, canonical_key)
  WHERE account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sitla_opp_lifecycle
  ON sitla_intel.opportunities (lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_sitla_opp_score
  ON sitla_intel.opportunities (overall_priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_sitla_opp_county
  ON sitla_intel.opportunities (county_name);
CREATE INDEX IF NOT EXISTS idx_sitla_opp_deadline
  ON sitla_intel.opportunities (bidding_end_at, application_deadline);

CREATE TABLE IF NOT EXISTS sitla_intel.opportunity_observations (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  source_id             UUID REFERENCES sitla_intel.sources(id) ON DELETE SET NULL,
  source_run_id         UUID REFERENCES sitla_intel.source_runs(id) ON DELETE SET NULL,
  raw_artifact_id       UUID REFERENCES sitla_intel.raw_artifacts(id) ON DELETE SET NULL,
  source_record_key     TEXT,
  observed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_date        DATE,
  raw_title             TEXT,
  raw_reference_number  TEXT,
  raw_status            TEXT,
  normalized_status     TEXT,
  raw_opportunity_type  TEXT,
  raw_commodity         TEXT,
  raw_legal_description TEXT,
  acreage               DOUBLE PRECISION,
  minimum_bid           NUMERIC(14, 2),
  winning_bid           NUMERIC(14, 2),
  application_deadline  TIMESTAMPTZ,
  bidding_start_at      TIMESTAMPTZ,
  bidding_end_at        TIMESTAMPTZ,
  official_detail_url   TEXT,
  external_bid_url      TEXT,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  record_hash           TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sitla_obs_opp
  ON sitla_intel.opportunity_observations (opportunity_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS sitla_intel.opportunity_events (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  event_type            TEXT NOT NULL,
  event_at              TIMESTAMPTZ,
  source_observation_id UUID REFERENCES sitla_intel.opportunity_observations(id) ON DELETE SET NULL,
  source_id             UUID REFERENCES sitla_intel.sources(id) ON DELETE SET NULL,
  title                 TEXT,
  description           TEXT,
  amount                NUMERIC(14, 2),
  metadata_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.offering_cycles (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cycle_key             TEXT NOT NULL UNIQUE,
  name                  TEXT NOT NULL,
  auction_month         TEXT,
  auction_year          INT,
  announcement_date     DATE,
  bidding_start_at      TIMESTAMPTZ,
  bidding_end_at        TIMESTAMPTZ,
  status                TEXT NOT NULL DEFAULT 'UNKNOWN',
  notes                 TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.legal_description_parts (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  part_index            INT NOT NULL DEFAULT 0,
  township              TEXT,
  range                 TEXT,
  section               TEXT,
  aliquot               TEXT,
  meridian              TEXT,
  raw_text              TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.geometry_versions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  centroid_lat          DOUBLE PRECISION,
  centroid_lon          DOUBLE PRECISION,
  acreage               DOUBLE PRECISION,
  accuracy              TEXT NOT NULL DEFAULT 'UNKNOWN',
  geometry_wkt          TEXT,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_current            BOOLEAN NOT NULL DEFAULT true,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.commercial_terms (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  minimum_bid           NUMERIC(14, 2),
  annual_rental         NUMERIC(14, 2),
  royalty_rate          TEXT,
  application_fee       NUMERIC(14, 2),
  bond_amount           NUMERIC(14, 2),
  primary_term_years    INT,
  terms_summary         TEXT,
  source_url            TEXT,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.mineral_evidence (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  evidence_type         TEXT NOT NULL DEFAULT 'OCCURRENCE',
  mine_name             TEXT,
  prospect_name         TEXT,
  commodity_normalized  TEXT,
  production_status     TEXT,
  distance_meters       DOUBLE PRECISION,
  inside_parcel         BOOLEAN NOT NULL DEFAULT false,
  confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
  source_url            TEXT,
  metadata_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.claim_context (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  mlrs_serial_number    TEXT,
  claim_name            TEXT,
  claim_status          TEXT,
  claimant_name         TEXT,
  distance_meters       DOUBLE PRECISION,
  inside_parcel         BOOLEAN NOT NULL DEFAULT false,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.historical_matches (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  related_opportunity_id UUID REFERENCES sitla_intel.opportunities(id) ON DELETE SET NULL,
  match_type            TEXT NOT NULL DEFAULT 'REOFFERING',
  match_confidence      DOUBLE PRECISION NOT NULL DEFAULT 0,
  summary               TEXT,
  metadata_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.bid_results (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  offering_cycle_id     UUID REFERENCES sitla_intel.offering_cycles(id) ON DELETE SET NULL,
  winning_bidder        TEXT,
  winning_bid           NUMERIC(14, 2),
  bid_per_acre          NUMERIC(14, 4),
  outcome               TEXT,
  result_date           DATE,
  source_url            TEXT,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.rights_evidence (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  rights_aspect         TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'UNKNOWN',
  notes                 TEXT,
  source_url            TEXT,
  confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.risk_flags (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  risk_code             TEXT NOT NULL,
  severity              TEXT NOT NULL DEFAULT 'info',
  description           TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.evidence_items (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  fact_key              TEXT NOT NULL,
  fact_value_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_class        TEXT NOT NULL DEFAULT 'SITLA',
  source_id             UUID REFERENCES sitla_intel.sources(id) ON DELETE SET NULL,
  source_url            TEXT,
  source_record_key     TEXT,
  extraction_method     TEXT NOT NULL DEFAULT 'manual',
  confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.score_snapshots (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id              UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  score_version               TEXT NOT NULL DEFAULT 'sitla-v1.0',
  calculated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  mineral_potential_score     DOUBLE PRECISION NOT NULL DEFAULT 0,
  acquisition_readiness_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  risk_penalty                DOUBLE PRECISION NOT NULL DEFAULT 0,
  overall_priority_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
  priority_tier               TEXT NOT NULL DEFAULT 'E',
  explanation_json            JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.review_tasks (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  task_type             TEXT NOT NULL,
  priority              INT NOT NULL DEFAULT 50,
  status                TEXT NOT NULL DEFAULT 'OPEN',
  title                 TEXT NOT NULL,
  instructions          TEXT,
  input_context_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
  decision              TEXT,
  decision_notes        TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at          TIMESTAMPTZ,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.watchlists (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id            BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  user_id               BIGINT REFERENCES users(id) ON DELETE SET NULL,
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  watch_reason          TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (account_id, opportunity_id)
);

CREATE TABLE IF NOT EXISTS sitla_intel.opportunity_target_links (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  area_of_focus_id      BIGINT NOT NULL REFERENCES areas_of_focus(id) ON DELETE CASCADE,
  link_type             TEXT NOT NULL DEFAULT 'RELATED_TARGET',
  confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_by            BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sitla_intel.alert_events (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id          UUID REFERENCES sitla_intel.opportunities(id) ON DELETE CASCADE,
  account_id              BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
  alert_type              TEXT NOT NULL,
  severity                TEXT NOT NULL DEFAULT 'info',
  detected_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  previous_value_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  new_value_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
  delivery_status         TEXT NOT NULL DEFAULT 'pending',
  delivery_channels_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
  dedupe_key              TEXT,
  error_message           TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sitla_alert_dedupe
  ON sitla_intel.alert_events (dedupe_key)
  WHERE dedupe_key IS NOT NULL;
