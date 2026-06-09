-- Migration 0024: JWT Issuers — managed JWKS/issuer configurations for embed
-- token verification. Replaces code-only issuer registration with a DB-backed
-- org-scoped store so host integrations can be configured without code changes.

-- ---------------------------------------------------------------------------
-- jwt_issuers: one row per configured JWT issuer for an org.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jwt_issuers (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    issuer          TEXT        NOT NULL,           -- the "iss" claim value
    jwks_url        TEXT,                           -- HTTPS JWKS endpoint URL
    static_jwks_json JSONB,                         -- static JWKS (no network fetch)
    algorithms      TEXT[]      NOT NULL DEFAULT ARRAY['RS256'],
    audience        TEXT        NOT NULL,           -- expected "aud" claim
    enabled         BOOLEAN     NOT NULL DEFAULT TRUE,
    created_by      UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique issuer string per org (one config per iss value per org).
CREATE UNIQUE INDEX IF NOT EXISTS idx_jwt_issuers_org_issuer
    ON jwt_issuers (org_id, issuer);

-- Fast lookup by org + enabled.
CREATE INDEX IF NOT EXISTS idx_jwt_issuers_org_enabled
    ON jwt_issuers (org_id, enabled);
