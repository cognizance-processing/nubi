-- 0023_schema_drift.sql
--
-- Schema-drift detection tables (C: Data health & observability).
--
-- dataset_schema_snapshots
--   Stores the last-seen column list for each (org_id, dataset_key).  On every
--   observation we compare the incoming columns to this snapshot; if they differ
--   we write drift_event rows and upsert the snapshot.  "First observation" is a
--   no-op: we just write the snapshot and fire no events (no baseline -> no drift).
--
-- schema_drift_events
--   Immutable append-only log of individual column-level changes detected per
--   (org_id, dataset_key).  change_type is one of:
--     'added'        -- column present in new snapshot but not in the last one.
--     'removed'      -- column present in last snapshot but not in the new one.
--     'type_changed' -- column present in both but the type differs.
--
-- Indexes
--   idx_schema_snapshots_org  -- org-scoped list scans.
--   idx_schema_drift_events_org_dataset -- fast lookup of drift events per dataset.
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dataset_schema_snapshots (
    org_id       uuid    NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    dataset_key  text    NOT NULL,
    columns      jsonb   NOT NULL DEFAULT '[]',
    captured_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, dataset_key)
);

CREATE INDEX IF NOT EXISTS idx_schema_snapshots_org
    ON dataset_schema_snapshots (org_id);

CREATE TABLE IF NOT EXISTS schema_drift_events (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    dataset_key  text        NOT NULL,
    change_type  text        NOT NULL
                     CHECK (change_type IN ('added', 'removed', 'type_changed')),
    column_name  text        NOT NULL,
    from_type    text        NULL,
    to_type      text        NULL,
    detected_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schema_drift_events_org_dataset
    ON schema_drift_events (org_id, dataset_key);
