-- Tax Sales Phase 3+ — alerts, parcel geometry versions, mineral surveys
-- Additive only; never mutates existing Mining OS tables.

CREATE TABLE IF NOT EXISTS tax_intel.alert_events (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id          UUID REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  account_id              BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
  alert_type              TEXT NOT NULL,
  severity                TEXT NOT NULL DEFAULT 'info',
  detected_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  previous_value_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  new_value_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
  delivery_status         TEXT NOT NULL DEFAULT 'pending',
  delivery_channels_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
  dedupe_key              TEXT,
  error_message           TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tax_alert_dedupe
  ON tax_intel.alert_events (dedupe_key)
  WHERE dedupe_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tax_alert_pending
  ON tax_intel.alert_events (delivery_status, detected_at DESC);

CREATE TABLE IF NOT EXISTS tax_intel.parcel_geometry_versions (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id          UUID NOT NULL REFERENCES tax_intel.tax_opportunities(id) ON DELETE CASCADE,
  source_id               UUID REFERENCES tax_intel.source_registry(id) ON DELETE SET NULL,
  geometry_wkt            TEXT,
  centroid_lat            DOUBLE PRECISION,
  centroid_lon            DOUBLE PRECISION,
  acreage                 DOUBLE PRECISION,
  accuracy                TEXT NOT NULL DEFAULT 'UNKNOWN',
  raw_payload_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_current              BOOLEAN NOT NULL DEFAULT true,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tax_parcel_geom_opp
  ON tax_intel.parcel_geometry_versions (opportunity_id, is_current);

CREATE TABLE IF NOT EXISTS tax_intel.mineral_surveys (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  state                   TEXT NOT NULL,
  survey_number           TEXT NOT NULL,
  survey_number_normalized TEXT NOT NULL,
  survey_name             TEXT,
  township                TEXT,
  range                   TEXT,
  section                 TEXT,
  meridian                TEXT,
  notes                   TEXT,
  source_url              TEXT,
  raw_payload_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (state, survey_number_normalized)
);

CREATE INDEX IF NOT EXISTS idx_tax_ms_norm
  ON tax_intel.mineral_surveys (state, survey_number_normalized);

-- Adapter configuration helpers on source registry (already has configuration_json).
ALTER TABLE tax_intel.source_registry
  ADD COLUMN IF NOT EXISTS adapter_class TEXT;

ALTER TABLE tax_intel.tax_opportunities
  ADD COLUMN IF NOT EXISTS enrichment_status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE tax_intel.tax_opportunities
  ADD COLUMN IF NOT EXISTS last_enriched_at TIMESTAMPTZ;
