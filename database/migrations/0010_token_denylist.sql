-- 0010_token_denylist.sql
-- Access-token denylist for immediate logout revocation.
--
-- Security model:
--   After logout, the caller's access-token jti is inserted here.
--   The application checks this table on every authenticated request;
--   any token whose jti is present is rejected even if the JWT signature
--   is otherwise valid and the token has not yet expired.
--
--   Rows are pruned by a periodic background job once expires_at has passed
--   (the JWT is already expired at that point, so the denylist entry is moot).

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti         text        PRIMARY KEY,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz NOT NULL DEFAULT now()
);

-- Index used by the periodic purge query (DELETE WHERE expires_at < now()).
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at
    ON revoked_tokens (expires_at);
