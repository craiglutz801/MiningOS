-- Add AI locations and web-search URLs to discovery run log.
ALTER TABLE discovery_runs
  ADD COLUMN IF NOT EXISTS locations_from_ai JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS urls_from_web_search TEXT[] DEFAULT '{}';
