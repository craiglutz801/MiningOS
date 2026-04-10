CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================================
-- BLM claims (open)
-- ============================================================
CREATE TABLE IF NOT EXISTS blm_claims_open (
  id              BIGSERIAL PRIMARY KEY,
  source_objectid BIGINT,
  state_abbr      TEXT,
  claim_name      TEXT,
  serial_num      TEXT,
  claim_type      TEXT,
  case_disposition TEXT,
  case_status     TEXT,
  admin_state     TEXT,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  geom            geometry(MultiPolygon, 4326),
  geom_centroid   geography(Point, 4326)
);

CREATE INDEX IF NOT EXISTS idx_blm_open_geom     ON blm_claims_open USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_blm_open_centroid  ON blm_claims_open USING GIST (geom_centroid);
CREATE INDEX IF NOT EXISTS idx_blm_open_state     ON blm_claims_open (state_abbr);

-- ============================================================
-- BLM claims (closed)
-- ============================================================
CREATE TABLE IF NOT EXISTS blm_claims_closed (
  id              BIGSERIAL PRIMARY KEY,
  source_objectid BIGINT,
  state_abbr      TEXT,
  claim_name      TEXT,
  serial_num      TEXT,
  claim_type      TEXT,
  case_disposition TEXT,
  case_status     TEXT,
  admin_state     TEXT,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  geom            geometry(MultiPolygon, 4326),
  geom_centroid   geography(Point, 4326)
);

CREATE INDEX IF NOT EXISTS idx_blm_closed_geom     ON blm_claims_closed USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_blm_closed_centroid  ON blm_claims_closed USING GIST (geom_centroid);
CREATE INDEX IF NOT EXISTS idx_blm_closed_state     ON blm_claims_closed (state_abbr);

-- ============================================================
-- PLSS Sections
-- ============================================================
CREATE TABLE IF NOT EXISTS plss_sections (
  id          BIGSERIAL PRIMARY KEY,
  state_abbr  TEXT,
  meridian    TEXT,
  township    TEXT,
  range       TEXT,
  section     TEXT,
  trs         TEXT,
  updated_at  TIMESTAMPTZ DEFAULT now(),
  geom        geometry(MultiPolygon, 4326)
);

CREATE INDEX IF NOT EXISTS idx_plss_geom ON plss_sections USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_plss_trs  ON plss_sections (trs);

-- ============================================================
-- MRDS occurrences (points)
-- ============================================================
CREATE TABLE IF NOT EXISTS mrds_occurrences (
  id              BIGSERIAL PRIMARY KEY,
  mrds_id         TEXT,
  name            TEXT,
  state_abbr      TEXT,
  commodities     TEXT[],
  reference_text  TEXT,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  geom            geography(Point, 4326)
);

CREATE INDEX IF NOT EXISTS idx_mrds_geom  ON mrds_occurrences USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_mrds_state ON mrds_occurrences (state_abbr);

-- ============================================================
-- Candidates (materialized by build_candidates pipeline)
-- ============================================================
CREATE TABLE IF NOT EXISTS candidates (
  id                BIGSERIAL PRIMARY KEY,
  claim_table       TEXT NOT NULL,         -- 'open' in MVP
  claim_id          BIGINT NOT NULL,
  state_abbr        TEXT,
  serial_num        TEXT,
  claim_name        TEXT,
  claim_type        TEXT,
  case_status       TEXT,
  case_disposition  TEXT,
  trs               TEXT,
  mrds_hit_count    INT DEFAULT 0,
  commodities       TEXT[],
  has_reference_text BOOLEAN DEFAULT false,
  score             INT DEFAULT 0,
  geom_centroid     geography(Point, 4326),
  geom              geometry(MultiPolygon, 4326),
  updated_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_candidates_score    ON candidates (score DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_geom     ON candidates USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_candidates_centroid ON candidates USING GIST (geom_centroid);
CREATE INDEX IF NOT EXISTS idx_candidates_trs      ON candidates (trs);
