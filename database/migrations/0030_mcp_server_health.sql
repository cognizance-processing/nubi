-- 0030_mcp_server_health.sql
-- Live connection status for registered MCP servers, matching the shape
-- `bridges` already has (status + last_seen_at) and what `datastores.config.health`
-- gives direct-mode connectors — so all three "is this thing reachable" surfaces
-- in Settings speak the same language instead of three different (or absent) ones.
--
-- Populated by POST /mcp/servers/{id}/test, which calls the real MCP handshake
-- (initialize + tools/list) via app.ai.mcp — the same client the agent's tool
-- loop already uses to call these servers, not a new bespoke ping.

ALTER TABLE mcp_servers
    ADD COLUMN IF NOT EXISTS last_tested_at     timestamptz NULL,
    ADD COLUMN IF NOT EXISTS last_test_ok        boolean     NULL,
    ADD COLUMN IF NOT EXISTS last_test_tool_count integer    NULL,
    ADD COLUMN IF NOT EXISTS last_test_error     text        NULL;
