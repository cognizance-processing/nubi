-- 0034_password_reset_tokens.sql
-- Single-use links for resetting a forgotten password. As with sessions and
-- api_keys, only the SHA-256 digest of the token is stored; the raw token
-- exists only in the email that carries it.

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash  text        NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,
    -- Set when the link is used, or voided because another link was.
    used_at     timestamptz NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_id ON password_reset_tokens (user_id);
