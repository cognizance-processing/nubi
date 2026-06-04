-- PROD: Metering persistence — kernel usage events table
-- Forward-only; never edit after applying.

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
