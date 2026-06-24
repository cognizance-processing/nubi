-- Migration 0016: webhook_endpoints — first-class outbound event sink.
--
-- Host products register HTTPS endpoints to receive Nubi signals (events):
--   watch_breach, freshness_stale, query_failed, flow_completed.
--
-- Each endpoint is OWNED BY ONE ORG (org_id) and is delivered ONLY events that
-- belong to that org — there is no cross-org fan-out. Delivery is signed with
-- HMAC-SHA256 over the JSON body using the endpoint's per-endpoint signing
-- secret; the host verifies the ``X-Nubi-Signature`` header.
--
-- SECURITY: the signing secret is NEVER stored in plaintext. It is encrypted at
-- rest with the application-layer Fernet scheme (app.secrets.crypto, keyed by
-- NUBI_SECRETS_KEY) — the SAME discipline used by the ``secrets`` table. Only
-- the ciphertext (bytea) is persisted; list/read APIs never return it.
--
-- ``event_types`` is the set of event names this endpoint is subscribed to.
-- An event is delivered to an endpoint only when its type is in this array AND
-- ``active`` is true. An empty array means "no events" (the endpoint receives
-- nothing until configured).

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           uuid        NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    -- Human-readable label so an operator can tell endpoints apart.
    name             text        NOT NULL DEFAULT 'webhook',
    -- Destination HTTPS URL the signed POST is delivered to.
    url              text        NOT NULL,
    -- Fernet-encrypted signing secret (HMAC-SHA256 key). Never stored plaintext,
    -- never returned by list/read APIs.
    secret_encrypted bytea       NOT NULL,
    -- Subscribed event types, e.g. {'watch_breach','flow_completed'}.
    event_types      text[]      NOT NULL DEFAULT '{}',
    -- When false, the endpoint receives no deliveries (soft-disable).
    active           boolean     NOT NULL DEFAULT TRUE,
    created_by       uuid        NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_org_id ON webhook_endpoints (org_id);
-- Hot path: "active endpoints for this org subscribed to event X" — the index
-- on org_id covers the org scoping; the event_types/active filter is applied
-- in-query (small per-org cardinality).
