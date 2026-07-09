-- Migration 0029 (EE): per-org BYO (bring-your-own) AI provider API keys.
--
-- Lets an organisation on ANY PAID tier supply its own LLM vendor API key
-- (Anthropic / OpenAI / Gemini) so metered calls route through THEIR key
-- instead of the operator's — and are therefore NOT charged from the org's
-- usage wallet (their key, their vendor bill).  Free-tier orgs cannot set a
-- key (enforced in app.ee.billing.org_ai_keys, not just at this table).
--
-- Encryption
-- ----------
-- Reuses the SAME AES-256-GCM application-layer encryption mechanism as
-- connector secrets (app.security.crypto / CONNECTOR_SECRET_KEY — see
-- migration 0001 era connector_secrets table for the sibling schema). The DB
-- receives only ciphertext + nonce + key_version; the master key lives
-- exclusively in the application environment, never in Postgres.
--
-- One row per (org_id, provider) — an org may store at most one key per
-- vendor.  ``provider`` is a bare vendor id matching
-- app.ai.provider.ALLOWED_MODELS keys ('anthropic' | 'openai' | 'gemini').

CREATE TABLE IF NOT EXISTS org_ai_keys (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    provider    text        NOT NULL CHECK (provider IN ('anthropic', 'openai', 'gemini')),
    ciphertext  bytea       NOT NULL,
    nonce       bytea       NOT NULL,
    key_version int         NOT NULL,
    created_by  uuid        NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_org_ai_keys_org ON org_ai_keys (org_id);
