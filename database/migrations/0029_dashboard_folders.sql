-- 0029_dashboard_folders.sql
-- Dashboard Folders: group boards within a project for organization.
--
-- Mirrors the boards table shape (id/org_id/project_id/created_by/name/
-- config/created_at/updated_at) so it can be registered as a generic
-- resource (see RESOURCE_TABLE_MAP in app/repos/base.py) and get list/
-- create/update/delete for free through the existing generic resource
-- router — no new route file needed.
--
-- Folder membership itself is NOT a column here or on boards: a board
-- records its folder as `config.folderId` (an opaque string, matching the
-- existing pass-through style used for widget/board appearance). This
-- avoids a join table (which only pays for itself under many-to-many
-- membership; a board only ever lives in one folder at a time here) and means folder
-- membership round-trips through git files-as-code sync/portability for
-- free, since `config` is already serialized verbatim everywhere.

CREATE TABLE IF NOT EXISTS dashboard_folders (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users    (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dashboard_folders_project_id_idx ON dashboard_folders (project_id);
