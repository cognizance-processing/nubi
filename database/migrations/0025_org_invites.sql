-- Migration 0025: Org invites — pending invitations to join an organisation
-- with a specific role. Accepting an invite (via its token) creates the
-- org_members row. Invites are org-scoped and carry one of the org roles
-- (owner / admin / member / viewer); org_members.role uses the same set.

-- ---------------------------------------------------------------------------
-- org_invites: one row per outstanding (or resolved) invitation.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS org_invites (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    email        TEXT        NOT NULL,                       -- invitee email (lower-cased by the app)
    role         TEXT        NOT NULL DEFAULT 'member'
                     CONSTRAINT org_invites_role_check
                     CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    token        TEXT        NOT NULL UNIQUE,                -- secret accept token (URL-safe)
    status       TEXT        NOT NULL DEFAULT 'pending'
                     CONSTRAINT org_invites_status_check
                     CHECK (status IN ('pending', 'accepted', 'revoked')),
    invited_by   UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at  TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '14 days'
);

-- List pending invites for an org quickly.
CREATE INDEX IF NOT EXISTS idx_org_invites_org_status
    ON org_invites (org_id, status);

-- At most one PENDING invite per (org, email) — re-inviting updates in place.
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_invites_org_email_pending
    ON org_invites (org_id, lower(email))
    WHERE status = 'pending';
