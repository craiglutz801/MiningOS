-- After backfilling plss_normalized to section-level (State+Township+Range+Section),
-- re-merge duplicates: one target per plss_normalized, merged minerals and report_links.
WITH keepers AS (
  SELECT MIN(id) AS id, plss_normalized
  FROM areas_of_focus
  WHERE plss_normalized IS NOT NULL AND plss_normalized != ''
  GROUP BY plss_normalized
),
merged AS (
  SELECT k.id,
         (SELECT array_agg(DISTINCT m ORDER BY m) FROM areas_of_focus a, unnest(COALESCE(a.minerals, '{}')) m WHERE a.plss_normalized = k.plss_normalized) AS minerals,
         (SELECT array_agg(DISTINCT r ORDER BY r) FROM areas_of_focus a, unnest(COALESCE(a.report_links, '{}')) r WHERE a.plss_normalized = k.plss_normalized) AS report_links
  FROM keepers k
)
UPDATE areas_of_focus a
SET minerals = COALESCE(m.minerals, a.minerals),
    report_links = CASE WHEN m.report_links = '{}' THEN a.report_links ELSE m.report_links END,
    updated_at = now()
FROM merged m
WHERE a.id = m.id;

DELETE FROM areas_of_focus a
USING areas_of_focus a2
WHERE a.plss_normalized IS NOT NULL
  AND a.plss_normalized = a2.plss_normalized
  AND a.id > a2.id;
