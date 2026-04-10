-- Default priority for targets is Low (was medium).
ALTER TABLE areas_of_focus
  ALTER COLUMN priority SET DEFAULT 'low';

-- Backfill: NULL/empty and old default 'medium' → 'low' (only 'high' stays high)
UPDATE areas_of_focus
SET priority = 'low'
WHERE priority IS NULL OR TRIM(COALESCE(priority, '')) = '' OR LOWER(TRIM(priority)) = 'medium';
