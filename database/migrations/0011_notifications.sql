-- 0011_notifications.sql
-- Notifications & integrations subsystem.
--
-- Unifies "connected channels" so ONE per-org integration powers both inbound
-- chat (app/chat/gateway.py) and outbound alerts (app/notify/*). Adds an in-app
-- notification feed and Web Push (VAPID) subscriptions.
--
-- Tables
--   org_integrations    — a connected channel for an org (slack/whatsapp/
--                         google_chat/teams/email/webhook). Non-secret config in
--                         `config` jsonb; secret material (webhook URLs, bot
--                         tokens, smtp password) lives in integration_secrets.
--   integration_secrets — AES-256-GCM ciphertext per integration, mirroring the
--                         connector_secrets table (app/security/crypto.py).
--   notifications       — in-app notification feed. user_id NULL ⇒ org-broadcast
--                         (visible to all members); otherwise targeted.
--   push_subscriptions  — Web Push (VAPID) endpoints, one per browser/device.
--
-- All tenant-scoped by org_id, mirroring 0009_watches.sql / 0010_api_keys.sql.

-- ── Connected integrations ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS org_integrations (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs  (id) ON DELETE CASCADE,
    created_by  uuid        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    -- Channel kind. Matches app.notify.channels.get_channel() kinds plus the
    -- two new ones (google_chat, teams). 'webhook' is a generic JSON POST.
    kind        text        NOT NULL CHECK (
                    kind IN ('slack','whatsapp','google_chat','teams','email','webhook')
                ),
    -- Human label shown in the UI (e.g. "Data-ops Slack").
    name        text        NOT NULL CHECK (char_length(name) > 0),
    -- Non-secret configuration: slack channel id, whatsapp phone_number_id,
    -- email recipients[], etc. NEVER contains secret material (see secrets table).
    config      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- Whether this integration is active for inbound chat + outbound alerts.
    enabled     boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS org_integrations_org_idx
    ON org_integrations (org_id, enabled);

-- ── Integration secrets (encrypted at rest; mirrors connector_secrets) ─────
CREATE TABLE IF NOT EXISTS integration_secrets (
    integration_id uuid       PRIMARY KEY REFERENCES org_integrations (id) ON DELETE CASCADE,
    org_id         uuid       NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    ciphertext     bytea      NOT NULL,
    nonce          bytea      NOT NULL,
    key_version    integer    NOT NULL DEFAULT 1,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ── In-app notification feed ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    -- NULL ⇒ org-wide broadcast (every member sees it). Otherwise targeted to
    -- a single user. Read-state for broadcasts is tracked per-user in
    -- notification_reads (below) since read_at can't be per-user on one row.
    user_id     uuid        REFERENCES users (id) ON DELETE CASCADE,
    -- Event class, e.g. 'watch_breach', 'flow_failed', 'flow_succeeded',
    -- 'share', 'comment', 'system'. Free-form; the app maps to icons.
    type        text        NOT NULL,
    severity    text        NOT NULL DEFAULT 'info'
                            CHECK (severity IN ('info','success','warning','error')),
    title       text        NOT NULL,
    body        text        NOT NULL DEFAULT '',
    -- Optional in-app deep link (e.g. '/watches/abc' or '/flows/xyz').
    link        text,
    -- Arbitrary structured context (watch_id, flow_run_id, metric_id, …).
    metadata    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- Read timestamp for TARGETED (user_id IS NOT NULL) notifications. For
    -- broadcasts, see notification_reads.
    read_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS notifications_feed_idx
    ON notifications (org_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS notifications_unread_idx
    ON notifications (org_id, user_id) WHERE read_at IS NULL;

-- Per-user read receipts for org-broadcast notifications (user_id IS NULL rows).
CREATE TABLE IF NOT EXISTS notification_reads (
    notification_id uuid       NOT NULL REFERENCES notifications (id) ON DELETE CASCADE,
    user_id         uuid       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    read_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (notification_id, user_id)
);

-- ── Web Push (VAPID) subscriptions ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    org_id      uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    -- The browser push endpoint URL (unique per device/browser).
    endpoint    text        NOT NULL UNIQUE,
    -- The subscription's public key + auth secret (from PushSubscription.toJSON).
    p256dh      text        NOT NULL,
    auth        text        NOT NULL,
    user_agent  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz
);

CREATE INDEX IF NOT EXISTS push_subscriptions_user_idx
    ON push_subscriptions (user_id);
