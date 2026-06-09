-- M21: Extend task_runs.state CHECK to include 'waiting_children', 'upstream_failed',
--      and 'timed_out' — new states written by the flows runtime for map fan-out,
--      branch routing, and timeout handling.  The original constraint (M12) only
--      included the seven states present at initial schema creation.
-- Forward-only; never edit after applying.

-- Drop the existing constraint and recreate it with the full state set.
-- Using IF EXISTS on the DROP so the migration is safe to re-run on a fresh DB
-- where the old constraint name may differ (Pg auto-generates names when unnamed).
-- We identify the constraint by querying pg_constraint to be robust.

DO $$
DECLARE
    _constraint_name text;
BEGIN
    -- Find the CHECK constraint on task_runs.state.
    SELECT con.conname
      INTO _constraint_name
      FROM pg_constraint con
      JOIN pg_class     rel ON rel.oid = con.conrelid
      JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
     WHERE nsp.nspname = current_schema()
       AND rel.relname  = 'task_runs'
       AND con.contype  = 'c'
       AND con.conname  LIKE '%state%'
     LIMIT 1;

    IF _constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE task_runs DROP CONSTRAINT %I', _constraint_name);
    END IF;
END;
$$;

ALTER TABLE task_runs
    ADD CONSTRAINT task_runs_state_check
    CHECK (state IN (
        'pending',
        'ready',
        'running',
        'success',
        'failed',
        'retrying',
        'skipped',
        'waiting_children',
        'upstream_failed',
        'timed_out'
    ));
