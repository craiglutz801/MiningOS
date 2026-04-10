-- Log of every discovery run: goal (replace/supplement), output (areas_added, log, errors).
CREATE TABLE IF NOT EXISTS discovery_runs (
  id SERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  replace BOOLEAN NOT NULL,
  limit_per_mineral INT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  minerals_checked TEXT[],
  areas_added INT NOT NULL DEFAULT 0,
  log TEXT[],
  errors TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_discovery_runs_created_at ON discovery_runs (created_at DESC);
