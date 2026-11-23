-- 0034_password_reset_tokens.sql
-- Single-use, short-lived links for POST /auth/forgot-password and
-- POST /auth/reset-password. As with sessions and api_keys, only the SHA-256
-- digest of a token is stored; the raw value exists only in the emailed link.

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash  text        PRIMARY KEY,
    user_id     uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_id
    ON password_reset_tokens (user_id);
