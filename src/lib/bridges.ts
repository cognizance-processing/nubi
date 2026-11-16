/**
 * bridges.js — API client for Bridge v2 management (Settings → Bridges).
 *
 * Mirrors backend/app/routes/bridges.py:
 *   GET    /bridges                          — list org bridges
 *   POST   /bridges                          { name, config }
 *   GET    /bridges/{id}                      — single bridge
 *   DELETE /bridges/{id}
 *   POST   /bridges/{id}/tokens               { name }        → mints raw token ONCE
 *   GET    /bridges/{id}/tokens               — listing-safe rows (never raw)
 *   POST   /bridges/{id}/tokens/{tid}/rotate                  → mints raw token ONCE
 *   DELETE /bridges/{id}/tokens/{tid}                         — revoke
 *
 * `get/post/del` (from api.js) prepend /api/v1 and attach the auth + active-org
 * headers. List reads degrade gracefully to [] (e.g. the backend route is not
 * deployed yet → 404, or the caller is unauthenticated). Mutations and token
 * reads let errors propagate so the UI can surface 403 / 404 / validation
 * messages — minting/rotating/revoking are owner/admin-only, so a 403 is a
 * meaningful, surfaceable outcome rather than something to silently swallow.
 *
 * SECURITY: the raw `nubi_br_…` token returned by mint/rotate is shown to the
 * user exactly once and never persisted or logged here — callers must treat the
 * `token` field as ephemeral.
 */

import { get, post, del } from './api.js'

// ---------------------------------------------------------------------------
// Agent install snippet
// ---------------------------------------------------------------------------

/**
 * Best-effort WebSocket control-plane URL for the bridge agent snippet.
 *
 * Prefers `VITE_BACKEND_URL` (the absolute API origin — same source api.js
 * uses to build request URLs) since the agent runs as a standalone process
 * and can't rely on the browser-only `/api/v1` dev proxy. Falls back to the
 * page's own origin, which is only correct when the frontend and backend
 * share a host (true for most self-hosted single-box setups, not for a
 * split frontend/API deploy) — the snippet is copy-editable, so a wrong
 * guess just means the customer edits one line.
 */
export function controlPlaneUrl() {
  const backend = import.meta.env.VITE_BACKEND_URL
  const origin = backend && backend.length > 0 ? backend : window.location.origin
  return origin.replace(/^http/, 'ws') + '/api/v1'
}

/**
 * Build the install/run snippet for the query-traffic bridge agent.
 *
 * IMPORTANT: this is `python -m app.bridges.agent` (backend/app/bridges/agent.py),
 * NOT `nubi bridge start` (the CLI's `cli/nubi_cli/bridge_agent.py`). The two
 * are easy to confuse because both authenticate with the same `nubi_br_…`
 * token, but they serve different traffic:
 *   - `python -m app.bridges.agent` — the binary TCP-mux tunnel that proxies
 *     database connector queries (`network_mode: "bridge"`). This is what a
 *     connector needs.
 *   - `nubi bridge start` — the CLI's file-ingest control channel only. Its
 *     WebSocket reader explicitly discards binary tunnel frames
 *     (`WebsocketControlChannel.recv` in bridge_agent.py), so running it for
 *     a database connector leaves the bridge showing "online" while every
 *     query through it hangs ~10s and fails with `bridge_not_connected` /
 *     a proxy timeout — a confusing, silent failure. See docs/bridges.md.
 */
export function agentInstallSnippet(bridgeId, token) {
  return [
    `BRIDGE_ID=${bridgeId} \\`,
    `BRIDGE_TOKEN=${token} \\`,
    `CONTROL_PLANE_URL=${controlPlaneUrl()} \\`,
    `  python -m app.bridges.agent`,
  ].join('\n')
}

// ---------------------------------------------------------------------------
// Bridge CRUD
// ---------------------------------------------------------------------------

/**
 * List the active org's bridges. Returns [] on any failure (route not deployed,
 * unauthenticated, etc.) so the page degrades gracefully.
 *
 * @returns {Promise<Array<{ id: string, name: string, status: string, last_seen_at: string|null, config?: object, created_at?: string }>>}
 */
export async function listBridges() {
  try {
    const data = await get('/bridges')
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.bridges)) return data.bridges
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch (cause) {
    console.warn('[bridges] listBridges failed; returning []:', cause.message)
    return []
  }
}

/**
 * Create a bridge for the active org.
 *
 * The backend create body is `{ name, config }` — `config` is a free-form dict
 * (defaults to `{}` server-side). The bridge agent's credential is the minted
 * bridge token, NOT anything in `config`, so a plain name is all that's needed
 * to stand one up; we pass an empty config.
 *
 * @param {string} name
 * @param {object} [config]
 * @returns {Promise<{ id: string, name: string, status: string, last_seen_at: string|null }>}
 */
export function createBridge(name, config = {}) {
  return post('/bridges', { name, config })
}

/**
 * Delete a bridge (owner/admin-equivalent writer guard; org-scoped).
 * @param {string} bridgeId
 * @returns {Promise<null>}
 */
export function deleteBridge(bridgeId) {
  return del(`/bridges/${bridgeId}`)
}

// ---------------------------------------------------------------------------
// Bridge tokens (owner/admin-only)
// ---------------------------------------------------------------------------

/**
 * List a bridge's tokens (listing-safe: last_four / created / rotated / revoked
 * state — never the raw value). Returns [] on any failure so a viewer or a
 * not-yet-deployed backend degrades gracefully.
 *
 * @param {string} bridgeId
 * @returns {Promise<Array<{ id: string, bridge_id: string, name: string, last_four: string|null, created_at: string|null, last_used_at: string|null, grace_until: string|null, revoked_at: string|null }>>}
 */
export async function listBridgeTokens(bridgeId) {
  try {
    const data = await get(`/bridges/${bridgeId}/tokens`)
    if (Array.isArray(data?.bridge_tokens)) return data.bridge_tokens
    if (Array.isArray(data)) return data
    return []
  } catch (cause) {
    console.warn('[bridges] listBridgeTokens failed; returning []:', cause.message)
    return []
  }
}

/**
 * Mint a new bridge token. The raw `nubi_br_…` token is returned EXACTLY ONCE in
 * the `token` field — show it once, never persist it. Owner/admin only.
 *
 * @param {string} bridgeId
 * @param {string} [name]
 * @returns {Promise<{ token: string, bridge_token: object }>}
 */
export function mintBridgeToken(bridgeId, name = 'bridge token') {
  return post(`/bridges/${bridgeId}/tokens`, { name })
}

/**
 * Rotate a bridge token: mints a replacement and grace-windows the old one so a
 * live agent can swap without a tunnel drop. The new raw token is returned once.
 * Owner/admin only.
 *
 * @param {string} bridgeId
 * @param {string} tokenId
 * @returns {Promise<{ token: string, bridge_token: object }>}
 */
export function rotateBridgeToken(bridgeId, tokenId) {
  return post(`/bridges/${bridgeId}/tokens/${tokenId}/rotate`)
}

/**
 * Revoke a bridge token immediately (drops the live tunnel). Owner/admin only.
 * @param {string} bridgeId
 * @param {string} tokenId
 * @returns {Promise<null>}
 */
export function revokeBridgeToken(bridgeId, tokenId) {
  return del(`/bridges/${bridgeId}/tokens/${tokenId}`)
}
