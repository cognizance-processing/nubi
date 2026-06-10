-- 0002_orgs_projects.sql
-- Multi-tenancy spine: organisations, their membership roster, pending
-- invitations, and the project layer (org → project → resources).
--
-- An org is the account/billing boundary; a *project* is the workspace /
-- deploy / git unit that groups resources. Every resource belongs to exactly
-- one project. To keep the model frictionless, a "Default" project is created
-- for each org at org-creation time (in application code, not here).

-- ── orgs ──────────────────────────────────────────────────────────────────────
-- slug is citext so lookups are case-insensitive and UNIQUE is enforced
-- case-insensitively (e.g. "Acme" and "acme" are the same slug).
--
-- On register the application creates a personal org + owner membership
-- (handled in application code, not here).

CREATE TABLE IF NOT EXISTS orgs (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text        NOT NULL,
    slug       citext      NOT NULL UNIQUE,
    avatar_url text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_members (
    org_id     uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    user_id    uuid        NOT NULL REFERENCES users  (id) ON DELETE CASCADE,
    role       text        NOT NULL DEFAULT 'member',

    PRIMARY KEY (org_id, user_id)
);

-- ── org_invites: pending invitations to join an organisation ─────────────────
-- Accepting an invite (via its token) creates the org_members row. Invites are
-- org-scoped and carry one of the org roles (owner / admin / member / viewer);
-- org_members.role uses the same set.

CREATE TABLE IF NOT EXISTS org_invites (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    email        text        NOT NULL,                       -- invitee email (lower-cased by the app)
    role         text        NOT NULL DEFAULT 'member'
                     CONSTRAINT org_invites_role_check
                     CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    token        text        NOT NULL UNIQUE,                -- secret accept token (URL-safe)
    status       text        NOT NULL DEFAULT 'pending'
                     CONSTRAINT org_invites_status_check
                     CHECK (status IN ('pending', 'accepted', 'revoked')),
    invited_by   uuid        REFERENCES users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    accepted_at  timestamptz,
    expires_at   timestamptz NOT NULL DEFAULT now() + INTERVAL '14 days'
);

-- List pending invites for an org quickly.
CREATE INDEX IF NOT EXISTS idx_org_invites_org_status
    ON org_invites (org_id, status);

-- At most one PENDING invite per (org, email) — re-inviting updates in place.
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_invites_org_email_pending
    ON org_invites (org_id, lower(email))
    WHERE status = 'pending';

-- ── projects: the workspace / deploy / git unit inside an org ────────────────
-- ``git`` is a jsonb blob reserved for git provider configuration (remote
-- binding); it must not be overloaded with app flags.

CREATE TABLE IF NOT EXISTS projects (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    name       text        NOT NULL,
    slug       citext      NOT NULL,
    created_by uuid        REFERENCES users (id) ON DELETE SET NULL,
    git        jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (org_id, slug)
);

CREATE INDEX IF NOT EXISTS projects_org_id_idx ON projects (org_id);
