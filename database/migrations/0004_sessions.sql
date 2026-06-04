-- 0004_sessions.sql
-- Refresh-token families for rotating refresh tokens with reuse detection.
--
-- token_hash   : SHA-256 of the opaque refresh token (never store raw).
-- family_id    : groups all rotations descended from the original issue.
-- parent_id    : the session row this one replaced (NULL for the root).
-- revoked_at   : set when the family is revoked (reuse detected or logout).
-- expires_at   : hard expiry; runner should clean up expired rows.

CREATE TABLE IF NOT EXISTS sessions (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash  text        NOT NULL UNIQUE,
    family_id   uuid        NOT NULL,
    parent_id   uuid        NULL,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz NULL,
    user_agent  text,
    ip          inet,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id   ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_family_id  ON sessions (family_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions (token_hash);
