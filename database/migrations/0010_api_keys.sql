-- 0010_api_keys.sql
-- Long-lived API keys for CLI / CI authentication (files-as-code F-6).
--
-- A user mints an API key (POST /auth/api-keys) for non-interactive use by the
-- Nubi CLI or a CI pipeline. The key authenticates Bearer requests exactly like
-- a short-lived login access token, but does not expire on the 15-minute access
-- TTL — it is a stable credential the user can list and revoke at will.
--
-- SECURITY: the raw key is shown to the caller EXACTLY ONCE at mint time and is
-- NEVER stored. Only its SHA-256 hex digest (``token_hash``) is persisted, the
-- same one-way storage discipline the refresh-token ``sessions`` table uses. A
-- key is scoped to the minting user AND their org at mint time; on every
-- authenticated request the org/user binding is read back from this row so a
-- key can never act outside the org it was minted for.
--
-- The opaque key format is ``nubi_ak_<43-char-base64url>`` (see
-- app/auth/api_keys.py). The prefix lets the auth layer distinguish an API key
-- from a JWT access token without a decode attempt.

CREATE TABLE IF NOT EXISTS api_keys (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    org_id      uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    -- SHA-256 hex digest of the opaque key. The raw key is never stored.
    token_hash  text        NOT NULL UNIQUE,
    -- Human label so the user can tell their keys apart in the UI / CLI.
    name        text        NOT NULL DEFAULT 'CLI token',
    -- Last 4 chars of the raw key, shown in listings so a key is recognisable
    -- without exposing the secret (e.g. "…a1b2").
    last_four   text,
    -- Set when revoked (DELETE /auth/api-keys/{id}); a non-null value means the
    -- key is rejected on every subsequent request.
    revoked_at  timestamptz NULL,
    last_used_at timestamptz NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id    ON api_keys (user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_org_id     ON api_keys (org_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_token_hash ON api_keys (token_hash);
