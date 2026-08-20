-- Active Mine Search — additive isolation schema
-- Never mutates existing Mining OS tables beyond normal Target upserts at runtime.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS active_mine_intel;

CREATE TABLE IF NOT EXISTS active_mine_intel.runs (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  state_abbr          TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'pending',
  refresh             BOOLEAN NOT NULL DEFAULT true,
  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at         TIMESTAMPTZ,
  matcher_run_id      TEXT,
  site_count          INT NOT NULL DEFAULT 0,
  linked_count        INT NOT NULL DEFAULT 0,
  unresolved_plss     INT NOT NULL DEFAULT 0,
  targets_created     INT NOT NULL DEFAULT 0,
  targets_reused      INT NOT NULL DEFAULT 0,
  qc_json             JSONB NOT NULL DEFAULT '{}'::jsonb,
  manifest_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message       TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ami_runs_account_state
  ON active_mine_intel.runs (account_id, state_abbr, started_at DESC);

CREATE TABLE IF NOT EXISTS active_mine_intel.candidate_sites (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id                  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  run_id                      UUID NOT NULL REFERENCES active_mine_intel.runs(id) ON DELETE CASCADE,
  mine_site_id                TEXT NOT NULL,
  state_abbr                  TEXT NOT NULL,
  rank                        INT,
  name                        TEXT,
  operator_name               TEXT,
  commodity                   TEXT,
  county                      TEXT,
  latitude                    DOUBLE PRECISION,
  longitude                   DOUBLE PRECISION,
  total_score                 DOUBLE PRECISION,
  activity_score              DOUBLE PRECISION,
  claim_match_score           DOUBLE PRECISION,
  data_quality_score          DOUBLE PRECISION,
  penalty_score               DOUBLE PRECISION,
  confidence_category         TEXT,
  activity_label              TEXT,
  best_claim_serial           TEXT,
  best_claim_name             TEXT,
  best_match_type             TEXT,
  best_distance_meters        DOUBLE PRECISION,
  claim_count                 INT NOT NULL DEFAULT 0,
  claim_serials               TEXT[] NOT NULL DEFAULT '{}',
  blm_plan_present            BOOLEAN NOT NULL DEFAULT false,
  blm_notice_present          BOOLEAN NOT NULL DEFAULT false,
  msha_status                 TEXT,
  score_breakdown_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_summary_json       JSONB NOT NULL DEFAULT '[]'::jsonb,
  recommended_next_action     TEXT,
  location_plss               TEXT,
  township                    TEXT,
  range                       TEXT,
  section                     TEXT,
  meridian                    TEXT,
  plss_normalized             TEXT,
  plss_source                 TEXT,
  plss_status                 TEXT NOT NULL DEFAULT 'unresolved',
  area_of_focus_id            BIGINT REFERENCES areas_of_focus(id) ON DELETE SET NULL,
  unpaid_claim_count          INT,
  paid_claim_count            INT,
  unknown_claim_count         INT,
  mlrs_claim_count            INT,
  claim_status_rollup         TEXT,
  claims_fetched_at           TIMESTAMPTZ,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (account_id, state_abbr, mine_site_id)
);

CREATE INDEX IF NOT EXISTS idx_ami_sites_account_state
  ON active_mine_intel.candidate_sites (account_id, state_abbr);
CREATE INDEX IF NOT EXISTS idx_ami_sites_score
  ON active_mine_intel.candidate_sites (account_id, state_abbr, total_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_ami_sites_aof
  ON active_mine_intel.candidate_sites (area_of_focus_id);
CREATE INDEX IF NOT EXISTS idx_ami_sites_run
  ON active_mine_intel.candidate_sites (run_id);

CREATE TABLE IF NOT EXISTS active_mine_intel.candidate_matches (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id                  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  run_id                      UUID NOT NULL REFERENCES active_mine_intel.runs(id) ON DELETE CASCADE,
  mine_site_id                TEXT NOT NULL,
  claim_serial_number         TEXT,
  claim_name                  TEXT,
  match_type                  TEXT,
  distance_meters             DOUBLE PRECISION,
  total_score                 DOUBLE PRECISION,
  activity_score              DOUBLE PRECISION,
  claim_match_score           DOUBLE PRECISION,
  confidence_category         TEXT,
  score_breakdown_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_summary_json       JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ami_matches_run_site
  ON active_mine_intel.candidate_matches (run_id, mine_site_id);

CREATE TABLE IF NOT EXISTS active_mine_intel.fetch_jobs (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  state_abbr          TEXT,
  status              TEXT NOT NULL DEFAULT 'pending',
  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at         TIMESTAMPTZ,
  target_ids          BIGINT[] NOT NULL DEFAULT '{}',
  processed           INT NOT NULL DEFAULT 0,
  succeeded           INT NOT NULL DEFAULT 0,
  failed              INT NOT NULL DEFAULT 0,
  results_json        JSONB NOT NULL DEFAULT '[]'::jsonb,
  error_message       TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ami_fetch_jobs_account
  ON active_mine_intel.fetch_jobs (account_id, started_at DESC);
