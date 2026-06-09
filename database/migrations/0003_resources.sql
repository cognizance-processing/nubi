-- 0003_resources.sql
-- Domain resources: bridges, datastores (+ encrypted connector secrets),
-- boards, queries, widgets, datasets, and org-scoped named secrets.
--
-- Every project-scoped resource carries ``project_id uuid NOT NULL`` — a
-- resource always belongs to exactly one project (the application resolves
-- the org's default project when the caller does not specify one).
--
-- Security model (connector_secrets / secrets):
--   The application encrypts credentials before INSERT and decrypts after
--   SELECT (AES-256-GCM for connector_secrets, Fernet for secrets).  The DB
--   stores only ciphertext.  Master keys NEVER live in the database.

-- ── bridges: one row per agent that provides VPC reachability ────────────────

CREATE TABLE IF NOT EXISTS bridges (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    name         text        NOT NULL,
    status       text        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'online', 'offline')),
    last_seen_at timestamptz,
    config       jsonb       NOT NULL DEFAULT '{}',   -- non-secret transport metadata
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bridges_org_id ON bridges (org_id);

-- ── datastores: connectors (+ network reachability primitives) ───────────────

CREATE TABLE IF NOT EXISTS datastores (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id   uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by   uuid        NOT NULL REFERENCES users    (id) ON DELETE RESTRICT,
    name         text        NOT NULL,
    config       jsonb       NOT NULL DEFAULT '{}',
    network_mode text        NOT NULL DEFAULT 'direct'
                             CHECK (network_mode IN ('direct', 'bridge', 'ssh_tunnel', 'psc', 'cloudsql_proxy')),
    bridge_id    uuid        REFERENCES bridges (id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS datastores_project_id_idx ON datastores (project_id);
CREATE INDEX IF NOT EXISTS idx_datastores_bridge_id  ON datastores (bridge_id);

-- ── connector_secrets: exactly one encrypted blob per datastore ──────────────

CREATE TABLE IF NOT EXISTS connector_secrets (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    datastore_id uuid        NOT NULL REFERENCES datastores (id) ON DELETE CASCADE,
    org_id       uuid        NOT NULL REFERENCES orgs       (id) ON DELETE CASCADE,
    ciphertext   bytea       NOT NULL,   -- AES-256-GCM ciphertext with appended authentication tag
    nonce        bytea       NOT NULL,   -- 12-byte GCM nonce; unique per encryption operation
    key_version  integer     NOT NULL DEFAULT 1,   -- identifies which app master key was used
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (datastore_id)               -- one secret blob per datastore
);

COMMENT ON TABLE connector_secrets IS
    'Stores AES-256-GCM ciphertext of connector credentials. '
    'Encryption and decryption happen exclusively in the application layer. '
    'The master key never leaves the application; the database holds only ciphertext and nonce.';

COMMENT ON COLUMN connector_secrets.ciphertext IS
    'AES-256-GCM ciphertext with the 16-byte authentication tag appended (ciphertext || tag).';

COMMENT ON COLUMN connector_secrets.nonce IS
    '12-byte (96-bit) GCM nonce. Must be unique for every encryption operation under the same key.';

COMMENT ON COLUMN connector_secrets.key_version IS
    'Monotonically increasing integer identifying the app master key used for this ciphertext. '
    'Increment when rotating keys so stale rows can be re-encrypted.';

CREATE INDEX IF NOT EXISTS idx_connector_secrets_datastore_id
    ON connector_secrets (datastore_id);

CREATE INDEX IF NOT EXISTS idx_connector_secrets_org_id
    ON connector_secrets (org_id);

-- ── boards / queries / widgets: project-scoped resource CRUD tables ──────────
-- All three share the generic resource column shape consumed by the Repo
-- protocol (app/repos): id, org_id, project_id, created_by, name, config.

CREATE TABLE IF NOT EXISTS boards (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users    (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS boards_project_id_idx ON boards (project_id);

CREATE TABLE IF NOT EXISTS queries (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users    (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS queries_project_id_idx ON queries (project_id);

CREATE TABLE IF NOT EXISTS widgets (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users    (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS widgets_project_id_idx ON widgets (project_id);

-- ── datasets: catalog for the lakehouse data-plane ───────────────────────────
-- Tracks CSV uploads + materialized query outputs as queryable datasets.
-- Each dataset row points to a Parquet file on object storage and has a
-- linked 'datastores' row so it flows through the normal connector path.

DO $$ BEGIN
    CREATE TYPE dataset_source AS ENUM ('upload', 'materialized');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS datasets (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    storage_uri     text        NOT NULL,         -- full URI e.g. file:///... or s3://...
    format          text        NOT NULL DEFAULT 'parquet',
    schema_json     jsonb,                        -- inferred column schema [{name, type}]
    created_by      uuid        NOT NULL REFERENCES users(id),
    source          dataset_source NOT NULL DEFAULT 'upload',
    datastore_id    uuid        REFERENCES datastores(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_datasets_org ON datasets (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_datasets_datastore ON datasets (datastore_id)
    WHERE datastore_id IS NOT NULL;

-- ── secrets: org-scoped named secrets (Fernet-encrypted values) ──────────────
-- Distinct from ``connector_secrets`` (a 1:1 blob per datastore): this is a
-- general-purpose named key-value store used by the flows engine
-- (TaskContext.secrets) and exposed via the /secrets API.

CREATE TABLE IF NOT EXISTS secrets (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    name            text        NOT NULL
                                CHECK (char_length(name) > 0),
    value_encrypted bytea       NOT NULL,
    created_by      uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, name)
);

COMMENT ON TABLE secrets IS
    'Org-scoped named secrets with Fernet-encrypted values. '
    'Encryption and decryption happen exclusively in the application layer '
    'via app.secrets.crypto. The master key (NUBI_SECRETS_KEY) never enters '
    'the database.';

COMMENT ON COLUMN secrets.value_encrypted IS
    'Fernet token (URL-safe base64-encoded AES-128-CBC ciphertext + HMAC-SHA256). '
    'Decrypt with app.secrets.crypto.decrypt().';

COMMENT ON COLUMN secrets.name IS
    'Human-readable identifier for the secret within the org scope. '
    'Used as the key in TaskContext.secrets ({{ secrets.NAME }}).';

CREATE INDEX IF NOT EXISTS idx_secrets_org_id
    ON secrets (org_id);

CREATE INDEX IF NOT EXISTS idx_secrets_org_name
    ON secrets (org_id, name);
