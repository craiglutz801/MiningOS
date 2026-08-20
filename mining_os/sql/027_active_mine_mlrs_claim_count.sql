-- Active Mine Search: persist MLRS scrape total claim count for list columns.

ALTER TABLE active_mine_intel.candidate_sites
  ADD COLUMN IF NOT EXISTS mlrs_claim_count INT;
