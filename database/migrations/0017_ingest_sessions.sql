-- Migration 0017: ingest_sessions — Postgres-backed ingest session store.
--
-- Replaces the in-memory + object-storage-sidecar store used in the OSS
-- initial build (feat/embed-bi-substrate) with a durable, org-scoped
-- Postgres table.
--
-- DESIGN
-- ------
-- One row per ingest session.  The state machine is enforced at the
-- application layer (PgIngestSessionStore.transition) using a
-- compare-and-swap WHERE clause rather than DB CHECK constraints, so no
-- ALTER is needed as the state machine evolves.
--
-- Idempotency is enforced by the UNIQUE constraint on
-- (org_id, datastore_id, idempotency_key).  Concurrent producers with the
-- same key receive the existing session — no duplicate opens.
--
-- SECURITY
-- --------
-- org_id is a FK to orgs(id) ON DELETE CASCADE: deleting an org purges all
-- its ingest sessions automatically (no orphans).  All application queries
-- filter on org_id so cross-org data leakage is impossible at the store
-- layer (defence-in-depth on top of any RLS policies).

CREATE TABLE IF NOT EXISTS ingest_sessions (
    -- Primary key
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Org ownership (cascade-delete on org removal)
    org_id           uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,

    -- The managed-lake datastore this session targets.
    -- Not a FK to datastores because datastores may be soft-deleted or
    -- managed in a separate repo; the application layer validates existence.
    datastore_id     uuid        NOT NULL,

    -- User who opened the session.
    user_id          uuid        NOT NULL,

    -- Publish mode: 'full_replace' replaces the entire table prefix;
    -- 'append' adds parts under a partition sub-prefix.
    mode             text        NOT NULL CHECK (mode IN ('full_replace', 'append')),

    -- Producer-supplied idempotency key; uniqueness is per
    -- (org, datastore) scope so the same key may be reused across datastores.
    idempotency_key  text        NOT NULL,

    -- Declared column schema: [{name: str, type: str}, …]
    schema           jsonb       NOT NULL DEFAULT '[]'::jsonb,

    -- Partition value (required for append, NULL for full_replace).
    -- E.g. 'dt=2026-06-25', 'region=za/dt=2026-06-25'.
    partition        text        NULL,

    -- Logical table name within the datastore (defaults to 'default').
    table_name       text        NOT NULL DEFAULT 'default',

    -- Server-generated run id — scopes the staging area for this session.
    run_id           text        NOT NULL,

    -- State machine: open → committing → committed (terminal)
    --                open → aborted (terminal)
    -- 'committing' is a transient lock; only one concurrent commit wins the
    -- CAS (WHERE state = 'open') and proceeds.
    state            text        NOT NULL DEFAULT 'open'
                                 CHECK (state IN ('open', 'committing', 'committed', 'aborted')),

    -- Stored commit result (set on committed); shape mirrors the HTTP response.
    result           jsonb       NULL,

    -- Last error message (overwritten on each transition attempt that errors).
    error            text        NULL,

    -- Audit timestamps
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Idempotency constraint: same producer key cannot open two sessions for the
-- same (org, datastore) pair.  The application uses ON CONFLICT DO NOTHING
-- + re-select to deduplicate concurrent opens safely.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingest_sessions_idem
    ON ingest_sessions (org_id, datastore_id, idempotency_key);

-- Hot-path lookup: "open sessions for this org+datastore" (e.g. status page,
-- concurrent-commit guard).
CREATE INDEX IF NOT EXISTS idx_ingest_sessions_org_ds_state
    ON ingest_sessions (org_id, datastore_id, state);
