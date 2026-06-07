-- 0009_connectors_secrets.sql
-- Schema for connector secrets (encrypted at rest, app-layer AES-256-GCM)
-- and VPC reachability primitives (bridges, network_mode on datastores).
--
-- Security model:
--   The application encrypts credentials before INSERT and decrypts after
--   SELECT using AES-256-GCM.  The DB stores only ciphertext + nonce.
--   The master key NEVER lives in the database.  No pgcrypto is used.

-- -------------------------------------------------------------------------
-- 1. bridges — one row per agent that provides VPC reachability
-- -------------------------------------------------------------------------
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

-- -------------------------------------------------------------------------
-- 2. connector_secrets — exactly one encrypted blob per datastore
-- -------------------------------------------------------------------------
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

-- -------------------------------------------------------------------------
-- 3. Reachability columns on datastores
-- -------------------------------------------------------------------------
ALTER TABLE datastores
    ADD COLUMN IF NOT EXISTS network_mode text NOT NULL DEFAULT 'direct'
        CHECK (network_mode IN ('direct', 'bridge', 'ssh_tunnel', 'psc', 'cloudsql_proxy'));

ALTER TABLE datastores
    ADD COLUMN IF NOT EXISTS bridge_id uuid REFERENCES bridges (id) ON DELETE SET NULL;

-- -------------------------------------------------------------------------
-- 4. Indexes
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_connector_secrets_datastore_id
    ON connector_secrets (datastore_id);

CREATE INDEX IF NOT EXISTS idx_connector_secrets_org_id
    ON connector_secrets (org_id);

CREATE INDEX IF NOT EXISTS idx_bridges_org_id
    ON bridges (org_id);

CREATE INDEX IF NOT EXISTS idx_datastores_bridge_id
    ON datastores (bridge_id);
