-- 0003_oauth_accounts.sql
-- OAuth provider links for a user (e.g. Google).
-- One user can have multiple provider accounts.
-- unique(provider, provider_account_id) prevents duplicate links.

CREATE TABLE IF NOT EXISTS oauth_accounts (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider            text        NOT NULL,
    provider_account_id text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_oauth_accounts_provider_account UNIQUE (provider, provider_account_id)
);
