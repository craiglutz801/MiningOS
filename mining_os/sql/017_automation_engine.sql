-- Automation Engine: rules + run log
-- Rules define: filter (which targets), action (what to do), outcome (what happens after),
-- schedule (cron expression or on-demand). Runs are logged with per-target results.

CREATE TABLE IF NOT EXISTS automation_rules (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  enabled       BOOLEAN NOT NULL DEFAULT true,

  -- Filter: JSON with keys matching list_areas params
  -- e.g. {"priority":"monitoring_high"}, {"mineral":"Gold","state_abbr":"NV"}
  filter_config JSONB NOT NULL DEFAULT '{}',

  -- Action: one of the supported action types
  -- fetch_claim_records | lr2000_report | check_blm_status | generate_report
  action_type   TEXT NOT NULL,

  -- Outcome: what to do after the action completes
  -- log_only | email_always | email_on_change | email_on_error
  outcome_type  TEXT NOT NULL DEFAULT 'log_only',

  -- Schedule: cron expression (e.g. "0 8 * * 1" = every Monday 8am)
  -- NULL or empty = on-demand only
  schedule_cron TEXT,

  -- Limit how many targets per run (safety)
  max_targets   INT NOT NULL DEFAULT 50,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS automation_run_log (
  id            BIGSERIAL PRIMARY KEY,
  rule_id       INT NOT NULL REFERENCES automation_rules(id) ON DELETE CASCADE,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  trigger_type  TEXT NOT NULL DEFAULT 'manual',  -- manual | scheduled
  status        TEXT NOT NULL DEFAULT 'running',  -- running | completed | failed
  targets_total INT NOT NULL DEFAULT 0,
  targets_ok    INT NOT NULL DEFAULT 0,
  targets_err   INT NOT NULL DEFAULT 0,
  changes_found INT NOT NULL DEFAULT 0,
  email_sent    BOOLEAN NOT NULL DEFAULT false,
  error_message TEXT,
  -- Per-target results as JSONB array
  -- [{id, name, ok, action_result, changed, error}]
  results       JSONB NOT NULL DEFAULT '[]',
  summary       TEXT
);

CREATE INDEX IF NOT EXISTS idx_automation_run_log_rule_id ON automation_run_log(rule_id);
CREATE INDEX IF NOT EXISTS idx_automation_run_log_started_at ON automation_run_log(started_at DESC);
