-- 0020_data_health.sql
--
-- Data health & freshness registry (B.1 / B.3).
--
-- dataset_freshness
--   Low-latency, pre-computed freshness registry populated by flow run
--   completion hooks and/or a scheduled freshness tick.  Every read is a
--   single indexed-row lookup — O(1) by (org_id, dataset_key) — suitable
--   for gating feature-lock UX without live computation.
--
--   Read SLO target: < 5 ms p99 (single primary-key equality scan on a
--   small, frequently-vacuumed table; fits in shared_buffers for any org
--   with < ~1 M datasets).
--
-- health_score_config
--   Per-org, per-dataset-key weight overrides for the three scoring
--   dimensions (freshness, completeness, availability).  Rows are optional;
--   when absent the route layer uses the documented defaults:
--     freshness=0.50, completeness=0.30, availability=0.20.
--
-- Indexes
--   idx_dataset_freshness_org_id  — list-all-for-org query (GET /health/freshness)
--   idx_health_score_config_org   — org-scoped config list
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dataset_freshness (
    org_id               uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    dataset_key          text        NOT NULL,
    last_success_at      timestamptz NULL,
    expected_interval_s  integer     NULL,   -- NULL means "no SLA configured"
    status               text        NOT NULL DEFAULT 'unknown'
                             CHECK (status IN ('fresh', 'stale', 'unknown')),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, dataset_key)
);

CREATE INDEX IF NOT EXISTS idx_dataset_freshness_org_id
    ON dataset_freshness (org_id);

-- ---------------------------------------------------------------------------
-- health_score_config — optional per-org weight overrides.
--
-- dataset_key may be NULL to represent an org-level default that applies to
-- every dataset in the org that has no specific override row.
--
-- weight_freshness + weight_completeness + weight_availability should sum to
-- 1.0; the application normalises them if they don't (defensive).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS health_score_config (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    dataset_key           text        NULL,        -- NULL = org-level default
    weight_freshness      numeric(5,4) NOT NULL DEFAULT 0.50,
    weight_completeness   numeric(5,4) NOT NULL DEFAULT 0.30,
    weight_availability   numeric(5,4) NOT NULL DEFAULT 0.20,
    created_at            timestamptz  NOT NULL DEFAULT now(),
    updated_at            timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (org_id, dataset_key)
);

CREATE INDEX IF NOT EXISTS idx_health_score_config_org
    ON health_score_config (org_id);
