-- Migration: 0025_audit_log.sql
--
-- Unified org-scoped action audit-log.
--
-- POPIA compliance contract:
--   - Records METADATA only: who (actor_user_id), what (action + resource ids),
--     when (at), and non-sensitive context (summary jsonb).
--   - NEVER stores row data, query literals, filter values, secret material, or
--     any content that could carry PII.
--   - summary is a JSONB dict of metadata keys supplied by the caller; callers
--     are responsible for keeping it POPIA-safe (see app/audit.py docstring).
--
-- IN-PLACE ONLY: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- NO ALTER, NO DROP.

CREATE TABLE IF NOT EXISTS audit_log (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         text        NOT NULL,
    actor_user_id  text,                          -- nullable: system/embed actors may have no user id
    actor_kind     text        NOT NULL CHECK (actor_kind IN ('access', 'embed', 'system')),
    action         text        NOT NULL,           -- e.g. 'board.create', 'metric.update', 'connector.delete'
    resource_type  text        NOT NULL,           -- e.g. 'board', 'metric', 'connector'
    resource_id    text,                           -- the resource's id (may be null for bulk ops)
    summary        jsonb       NOT NULL DEFAULT '{}',  -- non-sensitive metadata only
    at             timestamptz NOT NULL DEFAULT now()
);

-- Primary access pattern: org's audit trail, newest-first
CREATE INDEX IF NOT EXISTS audit_log_org_at
    ON audit_log (org_id, at DESC);

-- Secondary: filter by resource type within an org
CREATE INDEX IF NOT EXISTS audit_log_org_resource_type
    ON audit_log (org_id, resource_type);
