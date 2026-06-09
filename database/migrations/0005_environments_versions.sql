-- 0005_environments_versions.sql
-- Project-scoped environments + resource versioning + env↔git binding.
--
-- environments — named deployment targets (dev, prod, ...) scoped to a
--   project.  ``key`` is the slug used in URLs/APIs (?env=prod) and is unique
--   per project.  ``is_default`` marks the env that viewers resolve to when
--   none is specified; ``protected`` envs accept definition changes only via
--   an explicit promote (never a direct checkpoint).  ``position`` orders the
--   promotion pipeline in the UI.
--
--   Each environment is bound to a git branch in the project's workspace
--   repo: ``git_branch`` (application creation default: 'main' for the
--   protected prod env, otherwise the env key) and ``last_synced_sha`` (the
--   branch head recorded by the last successful env↔git push/pull; NULL means
--   never synced).  The DB stays the runtime source of truth — git binding is
--   best-effort.
--
-- resource_versions — immutable, append-only snapshots of a resource's
--   definition (flow spec / board config / query config).  Polymorphic over
--   ``kind`` ('flow' | 'board' | 'query'), so resource_id carries no FK; the
--   application cleans up versions when the resource is deleted.
--   ``version`` is a per-(kind, resource) monotonically increasing integer
--   and ``config_hash`` (sha256 of canonical JSON) lets the app dedupe
--   checkpoints whose content did not change.  ``parent_version_id`` chains a
--   version to the latest version that preceded it (lineage for the git
--   graph); ``git_commit_sha`` records the commit a checkpoint/pull was
--   serialized to, when the git layer is available.
--
-- resource_environments — the pointer table: which version each environment
--   of a resource is pinned to.  One pointer per (kind, resource, env);
--   promoting copies a pointer from one env to another.
--
-- The application ensures every project's dev + prod environment pair (at
-- project creation and lazily on access) — no backfill is needed here.

CREATE TABLE IF NOT EXISTS environments (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    key             text        NOT NULL,
    name            text        NOT NULL,
    is_default      boolean     NOT NULL DEFAULT false,
    protected       boolean     NOT NULL DEFAULT false,
    position        integer     NOT NULL DEFAULT 0,
    git_branch      text,
    last_synced_sha text,
    created_at      timestamptz NOT NULL DEFAULT now(),

    UNIQUE (project_id, key)
);

CREATE INDEX IF NOT EXISTS environments_project_id_idx ON environments (project_id);

CREATE TABLE IF NOT EXISTS resource_versions (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    project_id        uuid        REFERENCES projects (id) ON DELETE CASCADE,
    kind              text        NOT NULL CHECK (kind IN ('flow', 'board', 'query')),
    resource_id       uuid        NOT NULL,
    version           integer     NOT NULL,
    config            jsonb       NOT NULL,
    config_hash       text        NOT NULL,
    message           text,
    parent_version_id uuid        NULL REFERENCES resource_versions (id) ON DELETE SET NULL,
    git_commit_sha    text        NULL,
    created_by        uuid        REFERENCES users (id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),

    UNIQUE (kind, resource_id, version)
);

CREATE INDEX IF NOT EXISTS resource_versions_resource_idx
    ON resource_versions (kind, resource_id, version DESC);

CREATE TABLE IF NOT EXISTS resource_environments (
    kind           text        NOT NULL CHECK (kind IN ('flow', 'board', 'query')),
    resource_id    uuid        NOT NULL,
    environment_id uuid        NOT NULL REFERENCES environments (id) ON DELETE CASCADE,
    version_id     uuid        NOT NULL REFERENCES resource_versions (id) ON DELETE CASCADE,
    promoted_by    uuid        REFERENCES users (id) ON DELETE SET NULL,
    promoted_at    timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (kind, resource_id, environment_id)
);
