-- 0009_watches.sql
-- Watches — monitored metric thresholds with AI-explained breach alerts.
--
-- A watch monitors a single governed metric (see 0008_metrics.sql) at a chosen
-- grain/dimension and fires when a threshold (or change-over-time rule) is
-- breached. On breach the application generates a concise AI explanation
-- (app/ai/watch.py) and dispatches it to a notify channel (Slack/WhatsApp) via
-- app/chat/notify.py — the same dispatch the flow-run alert hook uses.
--
-- This table persists the watch definition; the application loads/evaluates it
-- on demand (POST /watches/{id}/evaluate or the tick endpoint). The runtime
-- config (dimensions, time_grain, threshold/comparison rule, channel config,
-- enabled) lives in ``config`` jsonb — exactly the shape app.ai.watch.Watch
-- parses.
--
-- Project-scoped like the other resources (id, org_id, project_id, created_by).

CREATE TABLE IF NOT EXISTS watches (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id   uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by   uuid        NOT NULL REFERENCES users    (id) ON DELETE RESTRICT,
    -- Stable, URL-safe slug callers reference. Unique per org so a watch
    -- resolves deterministically within a tenant.
    slug         text        NOT NULL CHECK (char_length(slug) > 0),
    name         text        NOT NULL,
    -- The governed metric this watch monitors (app/metrics/registry id). Not a
    -- FK: a watch may reference a slug-only seed metric (e.g. demo_revenue) that
    -- has no persisted row, so it is stored as plain text.
    metric_id    text        NOT NULL,
    -- Watch config (JSONB): dimensions[], time_grain, threshold {op, value} OR a
    -- change rule {kind:'change_pct', vs:'previous_period', op, value}, channel
    -- config {slack_webhook?/slack_channel?/whatsapp_to?}, enabled bool.
    config       jsonb       NOT NULL DEFAULT '{}',
    -- Evaluation bookkeeping: the last time the watch ran and the last state it
    -- resolved to (``ok`` / ``breached`` / ``error`` / ``unknown``).
    last_evaluated_at timestamptz,
    last_state        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, slug)
);

COMMENT ON TABLE watches IS
    'Monitored metric thresholds. On breach the application generates an AI '
    'explanation and dispatches it to a notify channel (Slack/WhatsApp) via '
    'app.chat.notify — the ask -> pin -> WATCH end-state.';

COMMENT ON COLUMN watches.config IS
    'Watch config (JSONB): dimensions[], time_grain, threshold {op, value} OR a '
    'change rule {kind:change_pct, vs:previous_period, op, value}, channel '
    'config (slack_webhook/slack_channel/whatsapp_to), enabled.';

COMMENT ON COLUMN watches.last_state IS
    'Last evaluated state: ok | breached | error | unknown.';

CREATE INDEX IF NOT EXISTS watches_project_id_idx ON watches (project_id);
CREATE INDEX IF NOT EXISTS watches_org_slug_idx   ON watches (org_id, slug);
-- Tick / due-watch scans select enabled watches by last evaluation time.
CREATE INDEX IF NOT EXISTS watches_evaluated_idx  ON watches (last_evaluated_at);
