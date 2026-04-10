-- Minerals of interest (editable list driving discovery and priority)
CREATE TABLE IF NOT EXISTS minerals_of_interest (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  sort_order  INT DEFAULT 0,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

-- Seed default minerals (Tungsten, Scandium, Beryllium, Uranium, Fluorspar, Germanium)
INSERT INTO minerals_of_interest (name, sort_order)
VALUES
  ('Tungsten', 1),
  ('Scandium', 2),
  ('Beryllium', 3),
  ('Uranium', 4),
  ('Fluorspar', 5),
  ('Germanium', 6)
ON CONFLICT (name) DO UPDATE SET sort_order = EXCLUDED.sort_order;

-- Areas of focus: claims/mines with location, mineral, status, reports
CREATE TABLE IF NOT EXISTS areas_of_focus (
  id                BIGSERIAL PRIMARY KEY,
  name              TEXT NOT NULL,
  location_plss      TEXT,
  location_coords    TEXT,
  latitude          DOUBLE PRECISION,  -- optional WGS84
  longitude         DOUBLE PRECISION,  -- optional WGS84
  minerals          TEXT[],
  status            TEXT,              -- paid | unpaid | unknown | N/A
  status_checked_at  TIMESTAMPTZ,
  report_links      TEXT[],
  report_summary    TEXT,
  validity_notes    TEXT,
  source            TEXT,              -- e.g. 'data_files', 'blm', 'discovery_agent'
  external_id       TEXT,              -- docket, serial number, etc.
  blm_case_url      TEXT,
  blm_serial_number TEXT,
  roi_score         INT,              -- 0-100 placeholder
  created_at        TIMESTAMPTZ DEFAULT now(),
  updated_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_areas_of_focus_minerals ON areas_of_focus USING GIN (minerals);
CREATE INDEX IF NOT EXISTS idx_areas_of_focus_status ON areas_of_focus (status);
CREATE INDEX IF NOT EXISTS idx_areas_of_focus_source ON areas_of_focus (source);

-- Optional: store report metadata when we discover govt/reports
CREATE TABLE IF NOT EXISTS focus_reports (
  id          BIGSERIAL PRIMARY KEY,
  area_id     BIGINT REFERENCES areas_of_focus(id) ON DELETE CASCADE,
  title       TEXT,
  url         TEXT,
  source      TEXT,
  snippet     TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_focus_reports_area ON focus_reports (area_id);
