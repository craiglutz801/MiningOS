-- Active Mine Search evidence model (T-041)
-- Additive columns + human verification checklist. Payment-status columns unchanged.

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS operational_status TEXT;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS regulatory_status TEXT;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS facility_type TEXT;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS tenure_class TEXT;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS verification_state TEXT NOT NULL DEFAULT 'Candidate';

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS fail_closed BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS tenure_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS contradictions_json JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS assertions_json JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS verification_checklist_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_ami_sites_operational
  ON active_mine_intel.candidate_sites (account_id, state_abbr, operational_status);

CREATE INDEX IF NOT EXISTS idx_ami_sites_verification
  ON active_mine_intel.candidate_sites (account_id, state_abbr, verification_state);

CREATE TABLE IF NOT EXISTS active_mine_intel.verification_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id          BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  site_id             UUID NOT NULL REFERENCES active_mine_intel.candidate_sites(id) ON DELETE CASCADE,
  from_state          TEXT,
  to_state            TEXT NOT NULL,
  reviewer_name       TEXT,
  reviewed_at         DATE,
  checklist_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
  notes               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ami_verify_site
  ON active_mine_intel.verification_events (site_id, created_at DESC);
