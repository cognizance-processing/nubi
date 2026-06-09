-- Migration 0022: Wallet, wallet ledger, and topup configuration tables.
-- Implements the prepaid credit wallet model for Nubi EE billing.
-- All monetary values are stored in USD cents (integer) to avoid floating-point drift.

-- ---------------------------------------------------------------------------
-- wallet_balance: one row per organisation, tracks the current balance.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet_balance (
    org_id              UUID        PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    balance_usd_cents   BIGINT      NOT NULL DEFAULT 0
                            CONSTRAINT wallet_balance_non_negative CHECK (balance_usd_cents >= 0),
    balance_zar_cents   BIGINT      NOT NULL DEFAULT 0,   -- display only; updated on FX run
    last_fx_rate        NUMERIC(12, 6),                   -- USD→ZAR rate at last ZAR sync
    last_fx_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wallet_balance_org ON wallet_balance (org_id);

-- ---------------------------------------------------------------------------
-- wallet_topup_config: per-org auto-topup settings and saved card details.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet_topup_config (
    org_id                       UUID         PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    auto_topup_enabled           BOOLEAN      NOT NULL DEFAULT FALSE,
    threshold_usd_cents          INT          NOT NULL DEFAULT 1000,    -- trigger when balance < $10.00
    topup_amount_usd_cents       INT          NOT NULL DEFAULT 5000,    -- charge card for $50.00
    monthly_topup_cap_usd_cents  INT,                                   -- NULL = unlimited auto-topups/month
    spend_cap_usd_cents          INT,                                   -- NULL = unlimited hard monthly spend cap
    topup_in_flight              BOOLEAN      NOT NULL DEFAULT FALSE,   -- idempotency lock
    topup_in_flight_at           TIMESTAMPTZ,                           -- when the lock was claimed; stale claims self-heal after a TTL
    -- Saved Paystack card details (populated after first successful payment)
    paystack_authorization_code  VARCHAR(100),
    paystack_customer_email      VARCHAR(255),
    paystack_customer_code       VARCHAR(100),
    paystack_card_last4          CHAR(4),
    paystack_card_brand          VARCHAR(50),
    paystack_card_exp_month      CHAR(2),
    paystack_card_exp_year       CHAR(4),
    paystack_auth_reusable       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at                   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Backfill for databases that created the table before topup_in_flight_at existed
-- (keeps the migration safe to re-run, mirroring the IF NOT EXISTS pattern above).
ALTER TABLE wallet_topup_config ADD COLUMN IF NOT EXISTS topup_in_flight_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- wallet_ledger: append-only ledger of every credit and debit.
-- Rows are never updated; corrections use ADJUSTMENT_CREDIT / ADJUSTMENT_DEBIT.
-- ---------------------------------------------------------------------------

-- CREATE TYPE has no IF NOT EXISTS form, so guard it with a DO block that
-- swallows duplicate_object — keeping the migration safe to re-run (mirrors the
-- DO-block pattern used in 0021/0018).
DO $$ BEGIN
    CREATE TYPE wallet_entry_type AS ENUM (
        'TOPUP_MANUAL',
        'TOPUP_AUTO',
        'TOPUP_PROMO',
        'TOPUP_FAILED',
        'USAGE_LLM',
        'USAGE_STORAGE',
        'USAGE_COMPUTE',
        'USAGE_EMBED',
        'USAGE_OVERAGE',
        'ADJUSTMENT_CREDIT',
        'ADJUSTMENT_DEBIT',
        'EXPIRY'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS wallet_ledger (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID            NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    entry_type              wallet_entry_type NOT NULL,
    amount_usd_cents        BIGINT          NOT NULL,   -- positive = credit, negative = debit
    balance_after_usd_cents BIGINT          NOT NULL,   -- snapshot of balance after this entry
    description             TEXT,
    ref_id                  VARCHAR(200),               -- Paystack reference, job id, etc.
    metadata                JSONB,                      -- model, tokens, session_id, zar_charged, etc.
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wallet_ledger_org_created
    ON wallet_ledger (org_id, created_at DESC);

-- DB-level idempotency backstop: a given external reference (Paystack charge,
-- billing-cycle overage draw, …) may produce at most ONE effective ledger row.
-- TOPUP_FAILED rows are excluded — a failed attempt records the reference for
-- audit but must never block the later successful credit for the same charge.
DROP INDEX IF EXISTS idx_wallet_ledger_ref;
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_ledger_ref
    ON wallet_ledger (ref_id)
    WHERE ref_id IS NOT NULL AND entry_type <> 'TOPUP_FAILED';
