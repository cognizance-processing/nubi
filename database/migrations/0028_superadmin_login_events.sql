-- 0028_superadmin_login_events.sql
-- Super-admin flag + login analytics (login_events) + IP geolocation cache (ip_geo).
--
-- SECURITY: users.is_superadmin is granted ONLY by manual SQL —
--     UPDATE users SET is_superadmin = true WHERE email = '...';
-- — or by the seed script (backend/seed.py, which marks SUPERUSER_EMAIL).
-- It is NEVER settable via any API endpoint: no request payload field maps to
-- this column anywhere in the backend, and all /admin/* routes merely READ it
-- (via the require_superadmin dependency, which re-loads the user row from the
-- DB on every request).
--
-- GEO: login_events.ip rows are lazily geolocated into ip_geo via ipinfo.io.
-- Self-hosters enable this by setting IPINFO_TOKEN in the root .env (free tier
-- token from https://ipinfo.io). Without a token, login analytics still work —
-- locations simply stay null. Private/loopback IPs are never looked up.

-- ── users.is_superadmin ──────────────────────────────────────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_superadmin boolean NOT NULL DEFAULT false;

-- ── login_events: one row per successful login/registration ─────────────────
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
