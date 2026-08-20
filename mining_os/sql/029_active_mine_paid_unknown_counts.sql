-- Active Mine Search: paid / unknown claim counts for list columns.

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS paid_claim_count INT;

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS unknown_claim_count INT;
