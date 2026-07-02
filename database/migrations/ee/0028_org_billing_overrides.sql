-- Migration 0028 (EE): per-org billing overrides — superadmin manual controls.
--
-- Some large / enterprise customers are managed OUTSIDE the self-serve Paystack
-- flow: they are invoiced manually (or not at all through us) and negotiate
-- bespoke quotas that don't match any published tier.  This table lets a
-- superadmin, from the /admin portal, (a) DISABLE self-serve billing for an org
-- and (b) OVERRIDE individual resource limits dynamically — without touching the
-- published tier catalogue in code and without fabricating a Paystack-driven
-- ``subscriptions`` row (which implies a billing relationship these orgs don't
-- have).
--
-- Relationship to ``subscriptions`` (0017)
-- ----------------------------------------
-- ``subscriptions`` is written ONLY by the Paystack webhook / checkout flow and
-- reflects the org's self-serve plan.  ``org_billing_overrides`` is written ONLY
-- by superadmins and is layered ON TOP at resolution time:
--     effective tier   = tier_override  ?? subscription.tier  ?? 'free'
--     effective limits = tier_catalogue(effective tier) with limit_overrides applied
--     billing_disabled = self-serve checkout blocked + overage billing turned off
-- Keeping the two tables separate means a webhook can never clobber an admin
-- override, and an admin override never invents a spurious subscription.
--
-- limit_overrides shape (jsonb)
-- -----------------------------
-- ``{ "<TierLimits field>": <number> | null, ... }`` where the key is one of the
-- overridable ``max_*`` fields (see app.ee.billing.quota.OVERRIDE_KEYS).  A JSON
-- number sets a hard cap; JSON ``null`` means "unlimited" for that dimension; an
-- ABSENT key falls back to the tier default.  Example:
--     { "max_ai_calls_per_month": 100000, "max_storage_gb": null }

CREATE TABLE IF NOT EXISTS org_billing_overrides (
    org_id            uuid        PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    -- When true: self-serve Paystack checkout is blocked for this org AND
    -- overage billing is turned off, so effective limits become hard caps
    -- (a dimension set to null in limit_overrides is unlimited).
    billing_disabled  boolean     NOT NULL DEFAULT false,
    -- Optional base tier the effective limits derive from.  NULL = use the org's
    -- subscription tier (or 'free' when it has no subscription).
    tier_override     text        NULL
                                  CHECK (tier_override IS NULL OR
                                         tier_override IN ('free', 'starter', 'team', 'pro', 'enterprise')),
    -- Per-dimension limit overrides. See header for shape.
    limit_overrides   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- Free-text superadmin note (e.g. contract reference). No PII.
    note              text        NULL,
    -- The superadmin who last wrote this row (audit convenience; nullable so a
    -- deleted admin user doesn't cascade-remove the override — SET NULL).
    updated_by        uuid        NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Fast "does this org have billing disabled?" filter for reporting jobs.
CREATE INDEX IF NOT EXISTS idx_org_billing_overrides_disabled
    ON org_billing_overrides (billing_disabled)
    WHERE billing_disabled = true;
