-- MCP server registry: per-org external MCP server configurations.
-- Migration 0019: mcp_servers
CREATE TABLE IF NOT EXISTS mcp_servers (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name            text NOT NULL,
    url             text NOT NULL,
    transport       text NOT NULL DEFAULT 'http',
    auth_secret_ciphertext  bytea     NULL,
    auth_secret_nonce       bytea     NULL,
    auth_secret_key_version integer   NULL,
    enabled         boolean NOT NULL DEFAULT true,
    created_by      uuid NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_org_enabled
    ON mcp_servers (org_id, enabled);
