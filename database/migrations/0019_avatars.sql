-- 0019_avatars.sql
-- Add avatar_url columns to users and orgs if not already present.
-- Both columns are nullable text — NULL means "no avatar set".
--
-- users.avatar_url already exists in 0002_users.sql but some deployments
-- may have been created before that column was added; this migration is
-- idempotent via ADD COLUMN IF NOT EXISTS.
--
-- orgs.avatar_url is new — used by the Organisation settings panel.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS avatar_url text NULL;

ALTER TABLE orgs
    ADD COLUMN IF NOT EXISTS avatar_url text NULL;
