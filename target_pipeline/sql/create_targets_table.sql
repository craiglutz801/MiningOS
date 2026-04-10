-- Optional standalone `targets` table for pipeline output (not used by the Mining OS app UI).
-- The app reads targets from `areas_of_focus`. Apply this only if OUTPUT_TABLE=targets.

CREATE TABLE IF NOT EXISTS targets (
  id BIGSERIAL PRIMARY KEY,
  target_name TEXT NOT NULL,
  state TEXT,
  county TEXT,
  plss TEXT NOT NULL DEFAULT '',
  plss_normalized TEXT NOT NULL,
  commodity TEXT NOT NULL DEFAULT '',
  source_count INT NOT NULL DEFAULT 0,
  has_report BOOLEAN NOT NULL DEFAULT false,
  score INT NOT NULL DEFAULT 0,
  deposit_names_json JSONB NOT NULL DEFAULT '[]',
  claim_ids_json JSONB NOT NULL DEFAULT '[]',
  report_links_json JSONB NOT NULL DEFAULT '[]',
  status TEXT,
  notes TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT targets_plss_commodity_unique UNIQUE (plss_normalized, commodity)
);

CREATE INDEX IF NOT EXISTS idx_targets_score ON targets (score DESC);
CREATE INDEX IF NOT EXISTS idx_targets_state ON targets (state);
