-- Migration 0023: Datasets catalog for the lakehouse data-plane.
-- Tracks CSV uploads + materialized query outputs as queryable datasets.
-- Each dataset row points to a Parquet file on object storage and has a
-- linked 'datastores' row so it flows through the normal connector path.

-- ---------------------------------------------------------------------------
-- dataset_source: distinguishes upload origin from materialize origin.
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE dataset_source AS ENUM ('upload', 'materialized');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- datasets: one row per registered dataset (catalog entry).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS datasets (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    storage_uri     TEXT        NOT NULL,         -- full URI e.g. file:///... or s3://...
    format          TEXT        NOT NULL DEFAULT 'parquet',
    schema_json     JSONB,                        -- inferred column schema [{name, type}]
    created_by      UUID        NOT NULL REFERENCES users(id),
    source          dataset_source NOT NULL DEFAULT 'upload',
    datastore_id    UUID        REFERENCES datastores(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_datasets_org ON datasets (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_datasets_datastore ON datasets (datastore_id)
    WHERE datastore_id IS NOT NULL;
