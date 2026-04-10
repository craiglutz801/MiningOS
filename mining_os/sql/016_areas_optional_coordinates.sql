-- Ensure optional WGS84 coordinates exist on targets (safe if already present from 002_minerals_and_focus.sql).
ALTER TABLE areas_of_focus ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
ALTER TABLE areas_of_focus ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

COMMENT ON COLUMN areas_of_focus.latitude IS 'Optional WGS84 latitude (decimal degrees, e.g. 40.5).';
COMMENT ON COLUMN areas_of_focus.longitude IS 'Optional WGS84 longitude (decimal degrees, e.g. -112.3).';
