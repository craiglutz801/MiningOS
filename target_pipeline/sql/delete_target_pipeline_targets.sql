-- Remove all targets inserted/updated by target_pipeline (see areas_of_focus.source).
-- focus_reports rows for those areas CASCADE delete (see 002_minerals_and_focus.sql).
--
-- Preview:
--   SELECT id, name, source, characteristics->'target_pipeline' AS tp FROM areas_of_focus WHERE source = 'target_pipeline';
--
-- Delete all pipeline-managed targets:
DELETE FROM areas_of_focus
WHERE source = 'target_pipeline';

-- Delete only one import batch (set run id from pipeline log or characteristics JSON):
-- DELETE FROM areas_of_focus
-- WHERE source = 'target_pipeline'
--   AND characteristics->'target_pipeline'->>'run_id' = 'YOUR_RUN_ID_HERE';
