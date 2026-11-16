/**
 * apiKeys.ts — API client for long-lived API keys (Settings → Connections).
 *
 * Mirrors backend/app/routes/auth.py's api-keys endpoints:
 *   POST   /auth/api-keys        { name } → { key, api_key: {...} }  (raw key shown ONCE)
 *   GET    /auth/api-keys        → { api_keys: [...] }
 *   DELETE /auth/api-keys/{id}
 *
 * `get/post/del` (from api.ts) prepend /api/v1 and attach the auth + active-org
 * headers. List reads degrade gracefully to [] so the page never hard-fails.
 *
 * This is the credential Nubi documents (docs/mcp.md) for an external MCP
 * client — Claude Desktop, Claude Code — to authenticate against the
 * Nubi-as-MCP-server endpoint, POST /api/v1/mcp. See app/auth/deps.py::
 * verified_identity for the server-side resolution of a nubi_ak_… key.
 *
 * SECURITY: the raw `nubi_ak_…` key returned by createApiKey() is shown to
 * the user exactly once and never persisted or logged here.
 */

import { get, post, del } from './api.js'

// ---------------------------------------------------------------------------
// API key CRUD
// ---------------------------------------------------------------------------

export async function listApiKeys() {
  try {
    const data = await get('/auth/api-keys')
    if (Array.isArray(data?.api_keys)) return data.api_keys
    return []
  } catch {
    return []
  }
}

export async function createApiKey(name: string) {
  return post('/auth/api-keys', { name })
}

export async function revokeApiKey(id: string) {
  return del(`/auth/api-keys/${id}`)
}

// ---------------------------------------------------------------------------
// MCP connection snippets
// ---------------------------------------------------------------------------

/**
 * Absolute URL for Nubi's MCP JSON-RPC endpoint.
 *
 * Prefers `VITE_BACKEND_URL` (same source api.ts uses to build request URLs)
 * since an external MCP client — Claude Desktop, Claude Code — runs outside
 * the browser and can't rely on the dev-only `/api/v1` proxy. Falls back to
 * the page's own origin, correct for most self-hosted single-box setups.
 * The snippet is copy-editable, so a wrong guess just means editing one line.
 */
export function mcpEndpointUrl(): string {
  const backend = import.meta.env.VITE_BACKEND_URL
  const origin = backend && backend.length > 0 ? backend : window.location.origin
  return origin.replace(/\/+$/, '') + '/api/v1/mcp'
}

/** One-line `claude mcp add` command for Claude Code. */
export function claudeCodeAddCommand(rawKey: string): string {
  return `claude mcp add --transport http nubi ${mcpEndpointUrl()} --header "Authorization: Bearer ${rawKey}"`
}

/** claude_desktop_config.json `mcpServers` block for Claude Desktop. */
export function claudeDesktopConfigSnippet(rawKey: string): string {
  const config = {
    mcpServers: {
      nubi: {
        url: mcpEndpointUrl(),
        transport: { type: 'http' },
        headers: { Authorization: `Bearer ${rawKey}` },
      },
    },
  }
  return JSON.stringify(config, null, 2)
}
