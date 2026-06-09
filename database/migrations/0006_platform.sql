-- 0006_platform.sql
-- Platform services: scheduled jobs, usage metering, streaming chat, and
-- managed JWT issuers for embed-token verification.

-- ── jobs + job_runs: scheduled jobs ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS jobs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    project_id   uuid        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by   uuid        NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    name         text        NOT NULL,
    kind         text        NOT NULL CHECK (kind IN ('query', 'python')),
    target       text        NOT NULL,
    schedule     text        NOT NULL,
    enabled      boolean     NOT NULL DEFAULT true,
    next_run_at  timestamptz,
    last_run_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobs_org_id_idx     ON jobs (org_id);
CREATE INDEX IF NOT EXISTS jobs_project_id_idx ON jobs (project_id);

CREATE TABLE IF NOT EXISTS job_runs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status       text        NOT NULL CHECK (status IN ('success', 'error')),
    started_at   timestamptz,
    finished_at  timestamptz,
    row_count    integer,
    message      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_runs_job_id_idx ON job_runs (job_id);

-- ── usage_events: metering persistence (kernel usage events) ─────────────────

CREATE TABLE IF NOT EXISTS usage_events (
    id           uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid,
    user_id      uuid,
    kind         text,
    tier         text,
    elapsed_ms   int,
    output_bytes bigint,
    units        double precision,
    created_at   timestamptz      NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usage_events_org_created_idx
    ON usage_events (org_id, created_at);

-- ── chats + chat_messages: streaming chat for the dashboard editor ───────────
--
-- chats          — one row per conversation, org- and user-scoped, optionally
--                  bound to a board (dashboard) being edited.
-- chat_messages  — the full turn-by-turn transcript.  `content` is JSONB so an
--                  assistant turn can store text plus the tool calls/results it
--                  made (and any proposed dashboard spec) in one row.

CREATE TABLE IF NOT EXISTS chats (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs(id)  ON DELETE CASCADE,
    user_id     uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    board_id    text,
    title       text        NOT NULL DEFAULT 'New chat',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chats_org_id_idx          ON chats (org_id);
CREATE INDEX IF NOT EXISTS chats_org_board_idx       ON chats (org_id, board_id);
CREATE INDEX IF NOT EXISTS chats_org_updated_at_idx  ON chats (org_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id     uuid        NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role        text        NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_chat_id_idx ON chat_messages (chat_id, created_at);

-- ── jwt_issuers: managed JWKS/issuer configurations for embed tokens ─────────
-- Replaces code-only issuer registration with a DB-backed org-scoped store so
-- host integrations can be configured without code changes.

CREATE TABLE IF NOT EXISTS jwt_issuers (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    issuer          text        NOT NULL,           -- the "iss" claim value
    jwks_url        text,                           -- HTTPS JWKS endpoint URL
    static_jwks_json jsonb,                         -- static JWKS (no network fetch)
    algorithms      text[]      NOT NULL DEFAULT ARRAY['RS256'],
    audience        text        NOT NULL,           -- expected "aud" claim
    enabled         boolean     NOT NULL DEFAULT TRUE,
    created_by      uuid        REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Unique issuer string per org (one config per iss value per org).
CREATE UNIQUE INDEX IF NOT EXISTS idx_jwt_issuers_org_issuer
    ON jwt_issuers (org_id, issuer);

-- Fast lookup by org + enabled.
CREATE INDEX IF NOT EXISTS idx_jwt_issuers_org_enabled
    ON jwt_issuers (org_id, enabled);
