CREATE TABLE IF NOT EXISTS metric_spec_versions (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    metric_id  text        NOT NULL,
    version    int         NOT NULL CHECK (version >= 1),
    spec       jsonb       NOT NULL,
    created_by uuid        REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    note       text,
    UNIQUE (metric_id, version)
);
CREATE INDEX IF NOT EXISTS metric_spec_versions_metric_id_idx ON metric_spec_versions (metric_id, version DESC);
CREATE INDEX IF NOT EXISTS metric_spec_versions_org_id_idx ON metric_spec_versions (org_id);
