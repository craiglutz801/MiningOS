-- Multi-account auth foundation.
-- Existing single-tenant data is backfilled into the default "Craig" account.

-- ---------------------------------------------------------------------------
-- Accounts / users / memberships / sessions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS accounts (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id               BIGSERIAL PRIMARY KEY,
  email            TEXT NOT NULL UNIQUE,
  username         TEXT NOT NULL UNIQUE,
  display_name     TEXT,
  password_hash    TEXT NOT NULL,
  is_active        BOOLEAN NOT NULL DEFAULT true,
  is_system_admin  BOOLEAN NOT NULL DEFAULT false,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_memberships (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'member',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT account_memberships_role_check CHECK (role IN ('owner', 'admin', 'member')),
  CONSTRAINT account_memberships_user_account_unique UNIQUE (user_id, account_id)
);

CREATE TABLE IF NOT EXISTS user_sessions (
  id                  BIGSERIAL PRIMARY KEY,
  user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  active_account_id   BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  session_token_hash  TEXT NOT NULL UNIQUE,
  expires_at          TIMESTAMPTZ NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_agent          TEXT,
  ip_address          TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_memberships_account_id
  ON account_memberships (account_id);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id
  ON user_sessions (user_id);

CREATE INDEX IF NOT EXISTS idx_user_sessions_active_account_id
  ON user_sessions (active_account_id);

CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
  ON user_sessions (expires_at);

-- ---------------------------------------------------------------------------
-- Default account for existing data
-- ---------------------------------------------------------------------------

INSERT INTO accounts (name)
VALUES ('Craig')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Add account ownership to user-scoped tables
-- ---------------------------------------------------------------------------

ALTER TABLE minerals_of_interest
  ADD COLUMN IF NOT EXISTS account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;

ALTER TABLE areas_of_focus
  ADD COLUMN IF NOT EXISTS account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;

ALTER TABLE focus_reports
  ADD COLUMN IF NOT EXISTS account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;

ALTER TABLE discovery_prompts
  ADD COLUMN IF NOT EXISTS id BIGSERIAL,
  ADD COLUMN IF NOT EXISTS account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;

ALTER TABLE discovery_runs
  ADD COLUMN IF NOT EXISTS account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;

ALTER TABLE automation_rules
  ADD COLUMN IF NOT EXISTS account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;

-- ---------------------------------------------------------------------------
-- Backfill current rows into Craig
-- ---------------------------------------------------------------------------

UPDATE minerals_of_interest
SET account_id = (SELECT id FROM accounts WHERE name = 'Craig')
WHERE account_id IS NULL;

UPDATE areas_of_focus
SET account_id = (SELECT id FROM accounts WHERE name = 'Craig')
WHERE account_id IS NULL;

UPDATE focus_reports fr
SET account_id = COALESCE(
  fr.account_id,
  (SELECT a.account_id FROM areas_of_focus a WHERE a.id = fr.area_id),
  (SELECT id FROM accounts WHERE name = 'Craig')
)
WHERE fr.account_id IS NULL;

UPDATE discovery_prompts
SET account_id = (SELECT id FROM accounts WHERE name = 'Craig')
WHERE account_id IS NULL;

UPDATE discovery_runs
SET account_id = (SELECT id FROM accounts WHERE name = 'Craig')
WHERE account_id IS NULL;

UPDATE automation_rules
SET account_id = (SELECT id FROM accounts WHERE name = 'Craig')
WHERE account_id IS NULL;

-- ---------------------------------------------------------------------------
-- Tighten NOT NULL after backfill
-- ---------------------------------------------------------------------------

ALTER TABLE minerals_of_interest
  ALTER COLUMN account_id SET NOT NULL;

ALTER TABLE areas_of_focus
  ALTER COLUMN account_id SET NOT NULL;

ALTER TABLE focus_reports
  ALTER COLUMN account_id SET NOT NULL;

ALTER TABLE discovery_prompts
  ALTER COLUMN account_id SET NOT NULL;

ALTER TABLE discovery_runs
  ALTER COLUMN account_id SET NOT NULL;

ALTER TABLE automation_rules
  ALTER COLUMN account_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- Replace old single-tenant uniqueness with account-scoped uniqueness
-- ---------------------------------------------------------------------------

ALTER TABLE minerals_of_interest
  DROP CONSTRAINT IF EXISTS minerals_of_interest_name_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_minerals_of_interest_account_name_unique
  ON minerals_of_interest (account_id, name);

ALTER TABLE discovery_prompts
  DROP CONSTRAINT IF EXISTS discovery_prompts_pkey;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'discovery_prompts_id_pkey'
  ) THEN
    ALTER TABLE discovery_prompts
      ADD CONSTRAINT discovery_prompts_id_pkey PRIMARY KEY (id);
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_discovery_prompts_account_mineral_unique
  ON discovery_prompts (account_id, mineral_name);

DROP INDEX IF EXISTS idx_areas_of_focus_plss_normalized_unique;

CREATE UNIQUE INDEX IF NOT EXISTS idx_areas_of_focus_account_plss_normalized_unique
  ON areas_of_focus (account_id, plss_normalized)
  WHERE plss_normalized IS NOT NULL AND plss_normalized != '';

-- ---------------------------------------------------------------------------
-- Helpful account indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_areas_of_focus_account_id
  ON areas_of_focus (account_id);

CREATE INDEX IF NOT EXISTS idx_focus_reports_account_id
  ON focus_reports (account_id);

CREATE INDEX IF NOT EXISTS idx_discovery_runs_account_created_at
  ON discovery_runs (account_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_automation_rules_account_id
  ON automation_rules (account_id, created_at DESC);
