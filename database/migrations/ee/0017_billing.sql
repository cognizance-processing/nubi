-- Migration 0017: EE billing tables — subscriptions and billing_events.
--
-- These tables back the EE billing sub-package and Nubi Cloud only.  They live
-- under database/migrations/ee/ and are applied ONLY when the cloud/EE layer is
-- active (migrate.py --ee, or NUBI_CLOUD=1 / NUBI_EE=1) — keeping the
-- open-source self-host schema thin (no billing tables it never uses).  The OSS
-- build never writes to these tables; the EE billing module is the sole writer.
--
-- subscriptions
--     One row per organisation.  Tracks the active billing tier, Paystack
--     customer / subscription codes, and the current billing period.
--     Upserted (not inserted) on every relevant Paystack webhook event.
--
-- billing_events
--     Append-only audit log of every Paystack webhook payload received.
--     Used for replay, debugging, and reconciliation.

-- ── subscriptions ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS subscriptions (
    id                          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                      uuid        NOT NULL UNIQUE REFERENCES orgs(id) ON DELETE CASCADE,
    tier                        text        NOT NULL DEFAULT 'free'
                                            CHECK (tier IN ('free', 'starter', 'team', 'pro', 'enterprise')),
    status                      text        NOT NULL DEFAULT 'active'
                                            CHECK (status IN ('active', 'cancelled', 'past_due', 'trialing')),
    paystack_customer_code      text        NULL,
    paystack_subscription_code  text        NULL,
    current_period_start        timestamptz NULL,
    current_period_end          timestamptz NULL,
    cancel_at_period_end        boolean     NOT NULL DEFAULT false,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now()
);

-- Index for rapid tier look-up by org.
CREATE INDEX IF NOT EXISTS idx_subscriptions_org_id
    ON subscriptions (org_id);

-- Index to find orgs whose subscription period is ending soon (renewal jobs).
CREATE INDEX IF NOT EXISTS idx_subscriptions_period_end
    ON subscriptions (current_period_end ASC NULLS LAST)
    WHERE status = 'active';

-- ── billing_events ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS billing_events (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    event_type  text        NOT NULL,
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Index for per-org event history queries (newest first).
CREATE INDEX IF NOT EXISTS idx_billing_events_org_created
    ON billing_events (org_id, created_at DESC);

-- Index for event-type queries (e.g. replay all charge.success events).
CREATE INDEX IF NOT EXISTS idx_billing_events_event_type
    ON billing_events (event_type, created_at DESC);
