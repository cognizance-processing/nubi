-- Migration 0018: FX rates table + subscriptions tier CHECK expansion.
--
-- Part 1: fx_rates
--     Stores the latest USD→ZAR (and future currency pair) exchange rates
--     fetched by the EE billing FX service.  One row per (base, quote) pair
--     — upserted on each daily refresh.  Historical rows are retained for
--     audit purposes via a companion audit table if needed in future.
--
--     Fields:
--         id         — surrogate UUID primary key.
--         base       — source currency ISO code (e.g. 'USD').
--         quote      — target currency ISO code (e.g. 'ZAR').
--         rate       — mid-market rate (quote units per 1 base unit).
--         source     — name of the FX API that provided the rate.
--         fetched_at — UTC timestamp when the rate was fetched.
--
--     The (base, quote) UNIQUE constraint is used by the ON CONFLICT upsert
--     in PgFxRateStore so that only the latest rate per pair is stored in
--     this table.
--
-- Part 2: subscriptions.tier CHECK expansion
--     The 0017_billing.sql migration created the subscriptions table with a
--     CHECK constraint limiting tier to ('free', 'pro', 'enterprise').
--     This migration adds the two new tiers ('starter', 'business') from the
--     v1.0 pricing blueprint.
--
--     PostgreSQL does not support ALTER TABLE … ALTER COLUMN … SET CHECK
--     directly — we must DROP the old constraint and ADD a new one.

-- ── Part 1: fx_rates ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fx_rates (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    base        text        NOT NULL,
    quote       text        NOT NULL,
    rate        numeric(18, 6) NOT NULL CHECK (rate > 0),
    source      text        NOT NULL DEFAULT 'unknown',
    fetched_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_fx_rates_base_quote UNIQUE (base, quote)
);

-- Index for time-series queries (find rates newer than N hours for staleness check).
CREATE INDEX IF NOT EXISTS idx_fx_rates_fetched_at
    ON fx_rates (base, quote, fetched_at DESC);

-- ── Part 2: subscriptions.tier CHECK expansion ────────────────────────────
-- Drop the original 3-value CHECK and replace with a 5-value CHECK that
-- includes 'starter' and 'business'.

-- First, find and drop the existing constraint by name (from migration 0017).
-- We use DO $$ to handle the case where the constraint name differs across
-- environments (created by different Postgres versions or tools).

DO $$
DECLARE
    _conname text;
BEGIN
    -- Find the CHECK constraint on subscriptions.tier.
    SELECT conname
      INTO _conname
      FROM pg_constraint
     WHERE conrelid = 'subscriptions'::regclass
       AND contype = 'c'
       AND pg_get_constraintdef(oid) LIKE '%tier%'
     LIMIT 1;

    IF _conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE subscriptions DROP CONSTRAINT %I', _conname);
    END IF;
END $$;

-- Add the expanded CHECK constraint covering all five billing tiers.
ALTER TABLE subscriptions
    ADD CONSTRAINT subscriptions_tier_check
    CHECK (tier IN ('free', 'starter', 'pro', 'business', 'enterprise'));
