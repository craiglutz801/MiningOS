-- Add priority to targets: Low, Medium, High (replaces ROI in UI). Default is Low.
ALTER TABLE areas_of_focus
  ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'low';

UPDATE areas_of_focus
SET priority = 'low'
WHERE priority IS NULL OR TRIM(COALESCE(priority, '')) = '';

CREATE INDEX IF NOT EXISTS idx_areas_of_focus_priority ON areas_of_focus (priority);
