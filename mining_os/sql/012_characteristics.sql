-- Optional JSON for target metadata and report references (0, 1, or more reports with links).
ALTER TABLE areas_of_focus
  ADD COLUMN IF NOT EXISTS characteristics JSONB DEFAULT '{}';

COMMENT ON COLUMN areas_of_focus.characteristics IS 'Optional JSON: e.g. {"reports": [{"url": "...", "label": "..."}], ...}';
