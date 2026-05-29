-- Public, no-login share links for a tailored target view (dashboard + PDF).
-- A share link captures a set of target ids within an account; the public
-- viewer reads live data scoped to that account via the link's token.

CREATE TABLE IF NOT EXISTS share_links (
  id          BIGSERIAL PRIMARY KEY,
  token       TEXT NOT NULL UNIQUE,
  account_id  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  created_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
  title       TEXT,
  area_ids    BIGINT[] NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ,
  revoked     BOOLEAN NOT NULL DEFAULT false,
  view_count  BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_share_links_token ON share_links (token);
CREATE INDEX IF NOT EXISTS idx_share_links_account ON share_links (account_id);
