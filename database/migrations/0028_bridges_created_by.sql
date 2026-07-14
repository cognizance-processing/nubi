-- 0028_bridges_created_by.sql
-- Add created_by to bridges (parity with datastores.created_by).
--
-- Safe as NOT NULL with no default: the bridges table has never actually been
-- persisted to (bridges lived in an in-process Python dict — see
-- app/bridges/store.py header) so there are zero existing rows to backfill.

ALTER TABLE bridges
    ADD COLUMN IF NOT EXISTS created_by uuid NOT NULL
        REFERENCES users (id) ON DELETE RESTRICT;
