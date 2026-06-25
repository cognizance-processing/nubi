-- Migration 0018: FX rates table.
--
-- fx_rates
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

-- ── fx_rates ──────────────────────────────────────────────────────────────

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

