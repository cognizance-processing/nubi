-- Migration 0020: Add map fan-out and branch tracking columns to task_runs.
--
-- parent_task_run_id — for map child task_runs, references the parent map
--   task_run in the same flow_run.  NULL for all other task_runs.
--
-- branch_taken — for branch task_runs, stores the label of the condition
--   that matched at runtime (e.g. 'condition_0', 'condition_1', 'default').
--   NULL for all other task_runs.
--
-- An index on parent_task_run_id enables efficient lookup of all child
-- task_runs for a given map node during fan-in collection.

ALTER TABLE task_runs
  ADD COLUMN IF NOT EXISTS parent_task_run_id UUID REFERENCES task_runs(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS branch_taken TEXT;

CREATE INDEX IF NOT EXISTS task_runs_parent_idx
  ON task_runs (parent_task_run_id)
  WHERE parent_task_run_id IS NOT NULL;
