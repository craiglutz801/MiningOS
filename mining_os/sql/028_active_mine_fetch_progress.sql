-- Active Mine Search: per-step fetch job progress for live UI updates.

ALTER TABLE active_mine_intel.fetch_jobs
  ADD COLUMN IF NOT EXISTS progress_json JSONB NOT NULL DEFAULT '{}'::jsonb;
