-- 0001_auth.sql
-- AUTH baseline: extensions, user accounts, OAuth links, refresh-token
-- sessions, the access-token denylist, and login analytics.
--
-- Extensions: citext (case-insensitive text) and pgcrypto (gen_random_uuid()).
-- Both are bundled with Postgres/Neon — no external installs needed.
--
-- SECURITY: users.is_superadmin is granted ONLY by manual SQL —
--     UPDATE users SET is_superadmin = true WHERE email = '...';
-- — or by the seed script (backend/seed.py, which marks SUPERUSER_EMAIL).
-- It is NEVER settable via any API endpoint: no request payload field maps to
-- this column anywhere in the backend, and all /admin/* routes merely READ it
-- (via the require_superadmin dependency, which re-loads the user row from the
-- DB on every request).

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── users: core accounts ─────────────────────────────────────────────────────
-- password_hash is nullable: OAuth-only users have no password.

CREATE TABLE IF NOT EXISTS users (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email           citext      NOT NULL UNIQUE,
    password_hash   text        NULL,
    email_verified  boolean     NOT NULL DEFAULT false,
    name            text,
    avatar_url      text,
    is_superadmin   boolean     NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ── oauth_accounts: provider links for a user (e.g. Google) ──────────────────
-- One user can have multiple provider accounts.
-- unique(provider, provider_account_id) prevents duplicate links.

CREATE TABLE IF NOT EXISTS oauth_accounts (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider            text        NOT NULL,
    provider_account_id text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_oauth_accounts_provider_account UNIQUE (provider, provider_account_id)
);

-- ── sessions: rotating refresh-token families with reuse detection ───────────
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

-- ── revoked_tokens: access-token denylist for immediate logout revocation ────
--
-- After logout, the caller's access-token jti is inserted here.  The
-- application checks this table on every authenticated request; any token
-- whose jti is present is rejected even if the JWT signature is otherwise
-- valid and the token has not yet expired.  Rows are pruned by a periodic
-- background job once expires_at has passed.

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti         text        PRIMARY KEY,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz NOT NULL DEFAULT now()
);

-- Index used by the periodic purge query (DELETE WHERE expires_at < now()).
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at
    ON revoked_tokens (expires_at);

-- ── login_events: one row per successful login/registration ─────────────────
--
-- GEO: login_events.ip rows are lazily geolocated into ip_geo via ipinfo.io.
-- Self-hosters enable this by setting IPINFO_TOKEN in the root .env (free tier
-- token from https://ipinfo.io). Without a token, login analytics still work —
-- locations simply stay null. Private/loopback IPs are never looked up.

CREATE TABLE IF NOT EXISTS login_events (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip          text,                       -- first X-Forwarded-For hop, else socket peer
    user_agent  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Latest-login-per-user lookups (admin users list) and 30-day rollups.
CREATE INDEX IF NOT EXISTS idx_login_events_user_created
    ON login_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_events_created
    ON login_events (created_at);

-- ── ip_geo: cache of ipinfo.io lookups, keyed by IP ──────────────────────────

CREATE TABLE IF NOT EXISTS ip_geo (
    ip           text        PRIMARY KEY,
    country      text,                      -- ISO country code, e.g. 'ZA'
    region       text,
    city         text,
    org          text,                      -- AS org, e.g. 'AS328474 Vodacom'
    looked_up_at timestamptz
);
