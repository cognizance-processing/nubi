-- 0005_orgs.sql
-- Multi-tenancy spine: organisations and their membership roster.
--
-- slug is citext so lookups are case-insensitive and UNIQUE is enforced
-- case-insensitively (e.g. "Acme" and "acme" are the same slug).
--
-- On register the application creates a personal org + owner membership
-- (handled in application code, not here).

CREATE TABLE IF NOT EXISTS orgs (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text        NOT NULL,
    slug       citext      NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_members (
    org_id     uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    user_id    uuid        NOT NULL REFERENCES users  (id) ON DELETE CASCADE,
    role       text        NOT NULL DEFAULT 'member',

    PRIMARY KEY (org_id, user_id)
);
