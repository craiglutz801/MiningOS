-- Tax Sales / Patented Claim Watch — additive isolation schema
-- Never mutates existing Mining OS tables.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS tax_intel;

CREATE TABLE IF NOT EXISTS tax_intel.source_registry (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_key          TEXT NOT NULL UNIQUE,
  name                TEXT NOT NULL,
  source_category     TEXT NOT NULL DEFAULT 'TAX',
  state               TEXT,
  county_fips         TEXT,
  county_name         TEXT,
  authority_level     INT NOT NULL DEFAULT 50,
  base_url            TEXT,
  listing_url         TEXT,
  parser_kind         TEXT NOT NULL DEFAULT 'MANUAL_UPLOAD',
  publication_scope   TEXT NOT NULL DEFAULT 'UNKNOWN',
  enabled             BOOLEAN NOT NULL DEFAULT false,
  is_official         BOOLEAN NOT NULL DEFAULT true,
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

CREATE TABLE IF NOT EXISTS tax_intel.source_runs (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id           UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
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

CREATE TABLE IF NOT EXISTS tax_intel.raw_artifacts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id           UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
  source_run_id       UUID REFERENCES tax_intel.source_runs(id) ON DELETE SET NULL,
  source_url          TEXT,
  retrieved_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  filename            TEXT,
  media_type          TEXT,
  storage_uri         TEXT,
  sha256              TEXT,
  byte_size           INT,
  text_extracted      BOOLEAN NOT NULL DEFAULT false,
  metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.tax_opportunities (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id                  BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
  canonical_key               TEXT NOT NULL,
  state                       TEXT NOT NULL,
  county_fips                 TEXT,
  county_name                 TEXT NOT NULL,
  primary_apn                 TEXT,
  best_name                   TEXT,
  property_address            TEXT,
  acreage                     DOUBLE PRECISION,
  latitude                    DOUBLE PRECISION,
  longitude                   DOUBLE PRECISION,
  geometry_accuracy           TEXT NOT NULL DEFAULT 'UNKNOWN',
  plss_key                    TEXT,
  township                    TEXT,
  range                       TEXT,
  section                     TEXT,
  meridian                    TEXT,
  tax_delinquency_status      TEXT NOT NULL DEFAULT 'UNKNOWN',
  sale_lifecycle_status       TEXT NOT NULL DEFAULT 'DISCOVERED',
  first_observed_at           TIMESTAMPTZ,
  last_observed_at            TIMESTAMPTZ,
  next_event_date             DATE,
  auction_start_at            TIMESTAMPTZ,
  amount_due                  NUMERIC(14, 2),
  minimum_bid                 NUMERIC(14, 2),
  currency                    TEXT NOT NULL DEFAULT 'USD',
  years_delinquent            INT,
  publication_scope           TEXT NOT NULL DEFAULT 'UNKNOWN',
  patent_classification       TEXT NOT NULL DEFAULT 'UNKNOWN',
  patent_confidence           DOUBLE PRECISION NOT NULL DEFAULT 0,
  mineral_signal              TEXT NOT NULL DEFAULT 'UNKNOWN',
  mineral_confidence          DOUBLE PRECISION NOT NULL DEFAULT 0,
  access_status               TEXT NOT NULL DEFAULT 'UNKNOWN',
  surface_mineral_unity_status TEXT NOT NULL DEFAULT 'NOT_REVIEWED',
  title_review_status         TEXT NOT NULL DEFAULT 'NOT_REVIEWED',
  environmental_risk_level    TEXT NOT NULL DEFAULT 'UNKNOWN',
  data_completeness_score     DOUBLE PRECISION NOT NULL DEFAULT 0,
  source_freshness_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
  mineral_potential_score     DOUBLE PRECISION NOT NULL DEFAULT 0,
  acquisition_readiness_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  overall_priority_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
  priority_tier               TEXT NOT NULL DEFAULT 'E',
  review_status               TEXT NOT NULL DEFAULT 'OPEN',
  watch_count                 INT NOT NULL DEFAULT 0,
  is_active                   BOOLEAN NOT NULL DEFAULT true,
  is_demo                     BOOLEAN NOT NULL DEFAULT false,
  commodities                 TEXT[] NOT NULL DEFAULT '{}',
  score_explanation_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary_json                JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tax_opp_account_canonical
  ON tax_intel.tax_opportunities (account_id, canonical_key)
  WHERE account_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tax_intel.parcel_identifiers (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id    UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  source_id         UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
  identifier_type   TEXT NOT NULL,
  raw_value         TEXT NOT NULL,
  normalized_value  TEXT,
  is_primary        BOOLEAN NOT NULL DEFAULT false,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.tax_observations (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  source_id             UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
  source_run_id         UUID REFERENCES tax_intel.source_runs(id) ON DELETE SET NULL,
  raw_artifact_id       UUID REFERENCES tax_intel.raw_artifacts(id) ON DELETE SET NULL,
  source_record_key     TEXT,
  observed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_date        DATE,
  raw_owner_name        TEXT,
  normalized_owner_name TEXT,
  raw_apn               TEXT,
  normalized_apn        TEXT,
  raw_legal_description TEXT,
  property_address      TEXT,
  raw_status            TEXT,
  normalized_status     TEXT,
  amount_due            NUMERIC(14, 2),
  minimum_bid           NUMERIC(14, 2),
  years_delinquent      INT,
  sale_date             DATE,
  is_redeemed           BOOLEAN NOT NULL DEFAULT false,
  is_withdrawn          BOOLEAN NOT NULL DEFAULT false,
  is_sold               BOOLEAN NOT NULL DEFAULT false,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  record_hash           TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.tax_events (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  event_type            TEXT NOT NULL,
  event_at              TIMESTAMPTZ,
  source_observation_id UUID REFERENCES tax_intel.tax_observations(id) ON DELETE SET NULL,
  title                 TEXT,
  description           TEXT,
  amount                NUMERIC(14, 2),
  metadata_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.patent_records (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id             UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
  patent_number         TEXT,
  accession_number      TEXT,
  document_type         TEXT,
  state                 TEXT,
  county_name           TEXT,
  patentee_name         TEXT,
  issue_date            DATE,
  total_acres           DOUBLE PRECISION,
  township              TEXT,
  range                 TEXT,
  section               TEXT,
  meridian              TEXT,
  legal_description     TEXT,
  mineral_survey_numbers TEXT[] NOT NULL DEFAULT '{}',
  claim_names           TEXT[] NOT NULL DEFAULT '{}',
  document_url          TEXT,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.opportunity_patent_matches (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  patent_record_id      UUID REFERENCES tax_intel.patent_records(id) ON DELETE SET NULL,
  match_status          TEXT NOT NULL DEFAULT 'UNREVIEWED',
  match_confidence      DOUBLE PRECISION NOT NULL DEFAULT 0,
  match_method          TEXT,
  mineral_survey_score  DOUBLE PRECISION NOT NULL DEFAULT 0,
  claim_name_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
  plss_score            DOUBLE PRECISION NOT NULL DEFAULT 0,
  geometry_overlap_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  contradiction_score   DOUBLE PRECISION NOT NULL DEFAULT 0,
  evidence_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  analyst_decision      TEXT,
  reviewed_at           TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.mineral_evidence (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  source_id             UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
  evidence_type         TEXT NOT NULL,
  mine_name             TEXT,
  prospect_name         TEXT,
  commodity_raw         TEXT,
  commodity_normalized  TEXT,
  deposit_type          TEXT,
  development_status    TEXT,
  production_status     TEXT,
  distance_meters       DOUBLE PRECISION,
  inside_parcel         BOOLEAN NOT NULL DEFAULT false,
  confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
  source_url            TEXT,
  metadata_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.claim_context (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  mlrs_serial_number    TEXT,
  claim_name            TEXT,
  claim_status          TEXT,
  claim_type            TEXT,
  claimant_name         TEXT,
  distance_meters       DOUBLE PRECISION,
  inside_parcel         BOOLEAN NOT NULL DEFAULT false,
  raw_payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.evidence_items (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  entity_type           TEXT,
  entity_id             UUID,
  fact_key              TEXT NOT NULL,
  fact_value_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_class        TEXT NOT NULL DEFAULT 'TAX',
  source_id             UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
  source_url            TEXT,
  source_record_key     TEXT,
  extraction_method     TEXT NOT NULL DEFAULT 'manual',
  confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
  is_primary            BOOLEAN NOT NULL DEFAULT true,
  is_contradictory      BOOLEAN NOT NULL DEFAULT false,
  analyst_verified      BOOLEAN NOT NULL DEFAULT false,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.score_snapshots (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id              UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  score_version               TEXT NOT NULL DEFAULT 'tax-v1.0',
  calculated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  mineral_potential_score     DOUBLE PRECISION NOT NULL DEFAULT 0,
  acquisition_readiness_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  risk_penalty                DOUBLE PRECISION NOT NULL DEFAULT 0,
  overall_priority_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
  priority_tier               TEXT NOT NULL DEFAULT 'E',
  explanation_json            JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.review_tasks (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  task_type             TEXT NOT NULL,
  priority              INT NOT NULL DEFAULT 50,
  status                TEXT NOT NULL DEFAULT 'OPEN',
  title                 TEXT NOT NULL,
  instructions          TEXT,
  input_context_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
  decision              TEXT,
  decision_notes        TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  due_at                TIMESTAMPTZ,
  completed_at          TIMESTAMPTZ,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tax_intel.watchlists (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id            BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  user_id               BIGINT REFERENCES users(id) ON DELETE SET NULL,
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  watch_reason          TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (account_id, opportunity_id)
);

CREATE TABLE IF NOT EXISTS tax_intel.opportunity_target_links (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id        UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  area_of_focus_id      BIGINT NOT NULL REFERENCES areas_of_focus(id) ON DELETE CASCADE,
  link_type             TEXT NOT NULL DEFAULT 'RELATED_TARGET',
  confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_by            BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tax_opp_state_county
  ON tax_intel.tax_opportunities (state, county_fips);
CREATE INDEX IF NOT EXISTS idx_tax_opp_lifecycle
  ON tax_intel.tax_opportunities (sale_lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_tax_opp_auction
  ON tax_intel.tax_opportunities (auction_start_at);
CREATE INDEX IF NOT EXISTS idx_tax_opp_score
  ON tax_intel.tax_opportunities (overall_priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_tax_opp_patent
  ON tax_intel.tax_opportunities (patent_classification);
CREATE INDEX IF NOT EXISTS idx_tax_opp_account_active
  ON tax_intel.tax_opportunities (account_id, is_active);
CREATE INDEX IF NOT EXISTS idx_tax_parcel_ids_norm
  ON tax_intel.parcel_identifiers (normalized_value);
CREATE INDEX IF NOT EXISTS idx_tax_obs_opp_observed
  ON tax_intel.tax_observations (opportunity_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_tax_patent_matches_opp
  ON tax_intel.opportunity_patent_matches (opportunity_id, match_confidence DESC);
CREATE INDEX IF NOT EXISTS idx_tax_mineral_ev_opp
  ON tax_intel.mineral_evidence (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_tax_review_status
  ON tax_intel.review_tasks (status, priority);
CREATE INDEX IF NOT EXISTS idx_tax_watch_account
  ON tax_intel.watchlists (account_id, opportunity_id);
