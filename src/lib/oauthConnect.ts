/**
 * oauthConnect.ts — API client for connecting AI apps (claude.ai, ChatGPT) over OAuth.
 *
 * Mirrors backend/app/routes/oauth.py:
 *   GET    /oauth/requests/{id}            → consent-screen details
 *   POST   /oauth/requests/{id}/decision   { approve, org_id } → { redirect_to }
 *   GET    /oauth/connections              → { connections: [...] }  (active org)
 *   DELETE /oauth/connections/{id}
 *
 * The browser never sees a code or token here except inside `redirect_to`,
 * which the consent page hands straight to window.location.
 */

import { get, post, del } from './api.js'
import { mcpEndpointUrl } from './apiKeys.js'

export interface ConsentDetails {
  client_name: string
  redirect_host: string
  /** "Claude" / "ChatGPT" when the redirect host is one we recognise, else null. */
  verified_as: string | null
  /** The code goes to a loopback address — a desktop tool such as Claude Code. */
  local_app: boolean
  scope: string
  orgs: { id: string; name: string; role: string }[]
}

export interface Connection {
  id: string
  client_name: string
  redirect_host: string | null
  created_at: string
  last_used_at: string | null
}

export function getConsentRequest(id: string): Promise<ConsentDetails> {
  return get(`/oauth/requests/${encodeURIComponent(id)}`)
}

export function decideConsentRequest(
  id: string,
  approve: boolean,
  orgId?: string,
): Promise<{ redirect_to: string }> {
  return post(`/oauth/requests/${encodeURIComponent(id)}/decision`, { approve, org_id: orgId ?? null })
}

export async function listConnections(): Promise<Connection[]> {
  try {
    const data = await get('/oauth/connections')
    return Array.isArray(data?.connections) ? data.connections : []
  } catch {
    return []
  }
}

export function revokeConnection(id: string) {
  return del(`/oauth/connections/${encodeURIComponent(id)}`)
}

/** The URL a user pastes into claude.ai / ChatGPT as a custom connector. */
export const connectorUrl = mcpEndpointUrl

/**
 * Claude Code speaks remote MCP + OAuth natively: after this, `/mcp` →
 * Authenticate opens the same consent page claude.ai uses. No key involved.
 */
export function claudeCodeOAuthCommand(url: string): string {
  return `claude mcp add --transport http nubi ${url}`
}

/** claude.ai / ChatGPT connect from their servers, so they need public HTTPS. */
export function isPublicHttps(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(u.hostname)
  } catch {
    return false
  }
}

// A pending approval that has to survive a full-page sign-in round trip
// (Google). Session-scoped and only ever a same-app consent path.
const RETURN_KEY = 'nubi.oauthReturn'

export function rememberOAuthReturn(path: string) {
  try {
    sessionStorage.setItem(RETURN_KEY, path)
  } catch {
    /* storage blocked — the user can restart from their AI app */
  }
}

export function takeOAuthReturn(): string | null {
  try {
    const path = sessionStorage.getItem(RETURN_KEY)
    sessionStorage.removeItem(RETURN_KEY)
    return path && path.startsWith('/oauth/consent?') ? path : null
  } catch {
    return null
  }
}
