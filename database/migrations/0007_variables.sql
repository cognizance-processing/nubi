-- 0007_variables.sql
-- Variables store: persistent, org- and (optionally) project-scoped key/value
-- pairs.  Each variable's ``value`` is arbitrary JSON.
--
-- Scoping: a variable is either project-scoped (project_id set) or org-global
-- (project_id NULL).  Uniqueness is per (org, project-or-global, key) — a
-- project var and an org-global var with the SAME key must NOT collide.  Since
-- project_id is nullable a plain UNIQUE constraint can't distinguish NULLs
-- (NULLs are never equal), so we use a UNIQUE INDEX over COALESCE(project_id,
-- <zero-uuid>) to fold the global scope onto a sentinel uuid.
--
-- This is the PERSISTENT store only — the run-scoped overlay and Python
-- set_var SDK are a later slice and are NOT part of this table.

CREATE TABLE IF NOT EXISTS variables (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id  uuid        REFERENCES projects (id) ON DELETE CASCADE,
    key         text        NOT NULL,
    value       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_by  uuid        REFERENCES users    (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Uniqueness per (org, project-or-global, key).  COALESCE folds the org-global
-- scope (project_id NULL) onto a fixed sentinel uuid so a global var and a
-- project var sharing a key are distinct rows but two globals with the same key
-- conflict.  A table UNIQUE constraint can't use COALESCE — hence the index.
CREATE UNIQUE INDEX IF NOT EXISTS variables_org_project_key_idx
    ON variables (
        org_id,
        COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid),
        key
    );

-- Fast lookup / listing by org + project.
CREATE INDEX IF NOT EXISTS variables_org_project_idx
    ON variables (org_id, project_id);
