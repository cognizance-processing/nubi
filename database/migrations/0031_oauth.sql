-- 0031_oauth.sql
-- OAuth 2.1 authorization server for remote MCP clients (claude.ai, ChatGPT and
-- any other client that connects to POST /api/v1/mcp from a browser and cannot
-- be handed an API key to paste into a config file).
--
-- The flow is the one the MCP authorization spec prescribes: the client
-- discovers us via /.well-known metadata, registers itself (RFC 7591 dynamic
-- client registration), sends the user through /oauth/authorize (PKCE S256 is
-- mandatory), the user signs in to Nubi and approves the connection for ONE of
-- their orgs, and the client swaps the code for tokens at /oauth/token.
--
-- What the user approves is a GRANT: (client, user, org). Every token hangs off
-- a grant, so revoking the connection in Settings kills every token at once.
-- As with api_keys and sessions, only SHA-256 digests of codes and tokens are
-- stored; the raw values exist only in the response that hands them out.

CREATE TABLE IF NOT EXISTS oauth_clients (
    id                          text        PRIMARY KEY,
    client_name                 text        NOT NULL,
    redirect_uris               text[]      NOT NULL,
    -- 'none' = public client (PKCE only); 'client_secret_post' /
    -- 'client_secret_basic' = confidential client with a secret.
    token_endpoint_auth_method  text        NOT NULL DEFAULT 'none',
    client_secret_hash          text        NULL,
    created_at                  timestamptz NOT NULL DEFAULT now()
);

-- A pending /oauth/authorize call, parked server-side while the user signs in
-- and decides on the consent screen. Short-lived; the id is single-use.
CREATE TABLE IF NOT EXISTS oauth_auth_requests (
    id              text        PRIMARY KEY,
    client_id       text        NOT NULL REFERENCES oauth_clients (id) ON DELETE CASCADE,
    redirect_uri    text        NOT NULL,
    code_challenge  text        NOT NULL,
    scope           text        NOT NULL,
    state           text        NULL,
    resource        text        NULL,
    expires_at      timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_grants (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     text        NOT NULL REFERENCES oauth_clients (id) ON DELETE CASCADE,
    user_id       uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    org_id        uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    scope         text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz NULL,
    revoked_at    timestamptz NULL
);
CREATE INDEX IF NOT EXISTS oauth_grants_user_org_idx ON oauth_grants (user_id, org_id);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash       text        PRIMARY KEY,
    grant_id        uuid        NOT NULL REFERENCES oauth_grants (id) ON DELETE CASCADE,
    redirect_uri    text        NOT NULL,
    code_challenge  text        NOT NULL,
    expires_at      timestamptz NOT NULL,
    used_at         timestamptz NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    token_hash  text        PRIMARY KEY,
    grant_id    uuid        NOT NULL REFERENCES oauth_grants (id) ON DELETE CASCADE,
    kind        text        NOT NULL CHECK (kind IN ('access', 'refresh')),
    expires_at  timestamptz NOT NULL,
    -- Set when a refresh token is rotated or a token is explicitly revoked.
    revoked_at  timestamptz NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oauth_tokens_grant_idx ON oauth_tokens (grant_id);
