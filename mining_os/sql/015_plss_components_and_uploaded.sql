-- Add explicit State, Township, Range, Section (sector) and uploaded flag for every target.
-- location_plss remains the display/input PLSS string; these columns are parsed and stored.
ALTER TABLE areas_of_focus
  ADD COLUMN IF NOT EXISTS township TEXT,
  ADD COLUMN IF NOT EXISTS range TEXT,
  ADD COLUMN IF NOT EXISTS section TEXT,
  ADD COLUMN IF NOT EXISTS is_uploaded BOOLEAN DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_areas_of_focus_township ON areas_of_focus (township);
CREATE INDEX IF NOT EXISTS idx_areas_of_focus_range ON areas_of_focus (range);
CREATE INDEX IF NOT EXISTS idx_areas_of_focus_section ON areas_of_focus (section);
CREATE INDEX IF NOT EXISTS idx_areas_of_focus_is_uploaded ON areas_of_focus (is_uploaded);
