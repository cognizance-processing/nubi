CREATE TABLE flow_spec_versions (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_id    uuid        NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    org_id     uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    version    int         NOT NULL CHECK (version >= 1),
    spec       jsonb       NOT NULL,
    created_by uuid        REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (flow_id, version)
);
CREATE INDEX flow_spec_versions_flow_id_idx ON flow_spec_versions (flow_id, version DESC);
