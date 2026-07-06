-- Migration: 0026_orgs_external_key.sql
--
-- Forward migration: add orgs.external_key to databases created before it
-- existed. 0002_orgs_projects.sql defines the column for FRESH installs; this
-- idempotent ALTER covers already-migrated databases (the runner never re-runs
-- 0002, so an edit there does not reach an existing DB). Safe to re-run and safe
-- on fresh installs — IF NOT EXISTS skips when the column is already present, so
-- fresh and upgraded schemas converge to the same shape.
--
-- external_key is a stable, host-controlled identifier an embedding host signs
-- in its JWT `org` claim instead of Nubi's internal org UUID (see
-- app/auth/verify.py::_resolve_embed_org and PUT /admin/orgs/{id}/external-key).
-- Nullable; UNIQUE (citext) allows many NULLs and case-insensitive uniqueness.

ALTER TABLE orgs ADD COLUMN IF NOT EXISTS external_key citext UNIQUE;
