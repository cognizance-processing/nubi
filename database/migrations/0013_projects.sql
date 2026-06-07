-- 0013_projects.sql
-- PROJECTS layer: org → project → resources.
--
-- An org is the account/billing boundary; a *project* is the workspace /
-- deploy / git unit that groups resources. Every resource belongs to exactly
-- one project. To keep the model frictionless, a "Default" project is created
-- for each org at org-creation time (in application code, not here).
--
-- project_id is added as a NULLABLE column on the resource tables so the chain
-- applies cleanly with no backfill (the DB is reset; seeds repopulate). New
-- resources are assigned a project_id by the application (defaulting to the
-- org's Default project).
--
-- Forward-only; never edit after applying.

CREATE TABLE IF NOT EXISTS projects (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    name       text        NOT NULL,
    slug       citext      NOT NULL,
    created_by uuid        REFERENCES users (id) ON DELETE SET NULL,
    git        jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (org_id, slug)
);

CREATE INDEX IF NOT EXISTS projects_org_id_idx ON projects (org_id);

-- ── Add nullable project_id (+ index) to each resource table ──────────────────

ALTER TABLE datastores ADD COLUMN IF NOT EXISTS project_id uuid
    REFERENCES projects (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS datastores_project_id_idx ON datastores (project_id);

ALTER TABLE boards ADD COLUMN IF NOT EXISTS project_id uuid
    REFERENCES projects (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS boards_project_id_idx ON boards (project_id);

ALTER TABLE queries ADD COLUMN IF NOT EXISTS project_id uuid
    REFERENCES projects (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS queries_project_id_idx ON queries (project_id);

-- widgets is part of the generic resource CRUD allowlist (created via the same
-- project-scoped route), so it needs project_id too.
ALTER TABLE widgets ADD COLUMN IF NOT EXISTS project_id uuid
    REFERENCES projects (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS widgets_project_id_idx ON widgets (project_id);

ALTER TABLE flows ADD COLUMN IF NOT EXISTS project_id uuid
    REFERENCES projects (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS flows_project_id_idx ON flows (project_id);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS project_id uuid
    REFERENCES projects (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS jobs_project_id_idx ON jobs (project_id);
