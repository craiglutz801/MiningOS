-- Add state_abbr and claim_type to areas_of_focus for filtering (List of Locations)
ALTER TABLE areas_of_focus
  ADD COLUMN IF NOT EXISTS state_abbr TEXT,
  ADD COLUMN IF NOT EXISTS claim_type TEXT;

-- Backfill state_abbr from location_plss (first token if it looks like 2-letter state)
UPDATE areas_of_focus
SET state_abbr = UPPER(TRIM(SPLIT_PART(TRIM(location_plss), ' ', 1)))
WHERE state_abbr IS NULL
  AND location_plss IS NOT NULL
  AND LENGTH(TRIM(SPLIT_PART(TRIM(location_plss), ' ', 1))) = 2
  AND TRIM(SPLIT_PART(TRIM(location_plss), ' ', 1)) ~ '^[A-Za-z]{2}$';

CREATE INDEX IF NOT EXISTS idx_areas_of_focus_state_abbr ON areas_of_focus (state_abbr);
CREATE INDEX IF NOT EXISTS idx_areas_of_focus_claim_type ON areas_of_focus (claim_type);
