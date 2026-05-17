ALTER TABLE areas_of_focus
  ADD COLUMN IF NOT EXISTS tag TEXT;

CREATE INDEX IF NOT EXISTS idx_areas_of_focus_account_tag
  ON areas_of_focus (account_id, tag);
