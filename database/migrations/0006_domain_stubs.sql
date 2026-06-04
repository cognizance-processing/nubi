-- 0006_domain_stubs.sql
-- Minimal stubs for domain entities: datastores, boards, queries, widgets, chats.
-- Each table carries just enough columns for future CRUD milestones.
-- No business logic is added here — that belongs in later milestones.

CREATE TABLE IF NOT EXISTS datastores (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS boards (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS queries (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS widgets (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chats (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    created_by uuid        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    name       text        NOT NULL,
    config     jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
