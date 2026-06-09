-- Migration 0027: EE billing invoices + tier CHECK fix.
--
-- Forward-only; never edit after applying.
--
-- 1. Relax the subscriptions.tier CHECK to include the 'starter' and 'team'
--    tiers.  The original 0017 CHECK only allowed ('free','pro','enterprise'),
--    which predates the Starter (v3) and Team tiers now in tiers.py.
--
-- 2. invoices — one row per billing cycle per org.  Records the base
--    subscription, metered overages NOT covered by the prepaid wallet, VAT
--    (only when VAT-registered), and the total collected via Paystack (ZAR).
--    A jsonb snapshot of the issuing business entity is stored so historical
--    invoices remain reproducible even if company details change later.
--
-- 3. invoice_counters — per-year monotonic sequence for human-readable
--    invoice numbers (e.g. NUBI-2026-000123).

-- ── 1. subscriptions tier CHECK (free / starter / team / pro / enterprise) ──

ALTER TABLE subscriptions
    DROP CONSTRAINT IF EXISTS subscriptions_tier_check;

ALTER TABLE subscriptions
    ADD CONSTRAINT subscriptions_tier_check
    CHECK (tier IN ('free', 'starter', 'team', 'pro', 'enterprise'));

-- ── 2. invoices ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS invoices (
    id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              uuid          NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    invoice_number      text          NOT NULL UNIQUE,
    tier                text          NOT NULL,
    status              text          NOT NULL DEFAULT 'pending'
                                      CHECK (status IN ('draft', 'pending', 'paid', 'past_due', 'void')),
    currency            text          NOT NULL DEFAULT 'ZAR',
    period_start        timestamptz   NOT NULL,
    period_end          timestamptz   NOT NULL,
    issued_at           timestamptz   NOT NULL DEFAULT now(),
    paid_at             timestamptz   NULL,
    customer_email      text          NOT NULL DEFAULT '',
    customer_name       text          NOT NULL DEFAULT '',
    line_items          jsonb         NOT NULL DEFAULT '[]'::jsonb,
    business            jsonb         NOT NULL DEFAULT '{}'::jsonb,
    subtotal_zar        numeric(14,2) NOT NULL DEFAULT 0,
    vat_rate            numeric(6,4)  NOT NULL DEFAULT 0,
    vat_amount_zar      numeric(14,2) NOT NULL DEFAULT 0,
    total_zar           numeric(14,2) NOT NULL DEFAULT 0,
    wallet_applied_zar  numeric(14,2) NOT NULL DEFAULT 0,
    fx_rate             numeric(12,6) NULL,
    vat_number          text          NOT NULL DEFAULT '',
    paystack_reference  text          NULL,
    pdf_filename        text          NULL,
    notes               text          NOT NULL DEFAULT '',
    created_at          timestamptz   NOT NULL DEFAULT now()
);

-- Per-org invoice history (newest first).
CREATE INDEX IF NOT EXISTS idx_invoices_org_issued
    ON invoices (org_id, issued_at DESC);

-- Find unpaid invoices for dunning / retry jobs.
CREATE INDEX IF NOT EXISTS idx_invoices_status
    ON invoices (status, issued_at DESC)
    WHERE status IN ('pending', 'past_due');

-- ── 3. invoice_counters (human-readable per-year sequence) ──────────────────

CREATE TABLE IF NOT EXISTS invoice_counters (
    year        int     PRIMARY KEY,
    last_value  bigint  NOT NULL DEFAULT 0
);
