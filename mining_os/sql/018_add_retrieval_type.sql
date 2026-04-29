-- Add retrieval_type to classify where a target came from.
-- Canonical values:
--   - 'Known Mine'  (MRDS auto-import / known mine retrieval flow)
--   - 'User Added'  (manual, CSV, PDF, discovery, existing historical rows)

ALTER TABLE areas_of_focus
  ADD COLUMN IF NOT EXISTS retrieval_type TEXT;

-- Backfill existing rows deterministically.
UPDATE areas_of_focus
SET retrieval_type = CASE
  WHEN COALESCE(source, '') = 'mrds_auto' THEN 'Known Mine'
  ELSE 'User Added'
END
WHERE retrieval_type IS NULL OR TRIM(retrieval_type) = '';

CREATE INDEX IF NOT EXISTS idx_areas_of_focus_retrieval_type
  ON areas_of_focus (retrieval_type);
