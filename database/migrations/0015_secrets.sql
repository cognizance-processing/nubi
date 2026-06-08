-- 0015_secrets.sql
-- Org-scoped named secrets encrypted at the application layer (Fernet /
-- AES-128-CBC + HMAC-SHA256).  This table is distinct from
-- ``connector_secrets`` (0009), which is a 1:1 blob per datastore.  The
-- ``secrets`` table is a general-purpose named key-value store used by the
-- flows engine (TaskContext.secrets) and exposed via the /secrets API.
--
-- Security model:
--   The application encrypts the plaintext value before INSERT and decrypts
--   after SELECT using Fernet (from the ``cryptography`` package).  The
--   master key (NUBI_SECRETS_KEY) NEVER lives in the database.

-- -------------------------------------------------------------------------
-- 1. secrets — one row per (org, name) pair
-- -------------------------------------------------------------------------
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

-- -------------------------------------------------------------------------
-- 2. Indexes
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_secrets_org_id
    ON secrets (org_id);

CREATE INDEX IF NOT EXISTS idx_secrets_org_name
    ON secrets (org_id, name);
