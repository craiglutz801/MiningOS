-- Optional coordinates on standalone `targets` table (for OUTPUT_TABLE=targets).
-- Safe to run if columns already exist (e.g. from create_targets_table.sql).
ALTER TABLE targets ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
ALTER TABLE targets ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;
