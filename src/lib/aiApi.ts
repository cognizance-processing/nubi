/**
 * aiApi.js — thin transport layer for AI provider/model metadata + BYO keys.
 *
 * Contract (backend/app/routes/ai.py + backend/app/ee/billing/ai_keys_routes.py):
 *   GET    /ai/providers   fetchAiProviders — enabled providers + their models,
 *                          plus the caller's org BYO eligibility/status.
 *   POST   /ai/keys        setAiKey  {org_id, provider, api_key} — EE-only,
 *                          paid tiers only (org admin/owner or superadmin).
 *   DELETE /ai/keys        clearAiKey {org_id, provider} — EE-only.
 *
 * GET /ai/providers works in OSS builds too (core route) — `byo.can_byo` and
 * `byo.providers` simply default to false/{} when EE billing isn't loaded.
 * POST/DELETE /ai/keys are mounted only by EE billing; callers should gate
 * writes on `byo.can_byo` (never render them when it is false) so an OSS
 * deployment never attempts a call to a route that doesn't exist.
 *
 * Key handling: API keys are WRITE-ONLY. GET /ai/providers never returns key
 * material — only `byo.providers[providerId]` (a boolean "is one configured").
 */

import { get, post, getAccessToken } from './api.js'

const _backendUrl = import.meta.env.VITE_BACKEND_URL ?? ''
const BASE = (import.meta.env.DEV || !_backendUrl) ? '/api/v1' : _backendUrl + '/api/v1'

/**
 * @typedef {{
 *   id: string,
 *   display_name: string,
 *   default: boolean,
 *   input_cost_per_1m: number | null,   // USD per 1M input tokens (from litellm.model_cost)
 *   output_cost_per_1m: number | null,  // USD per 1M output tokens
 *   max_input_tokens: number | null,
 *   max_output_tokens: number | null,
 * }} AiModel
 *
 * @typedef {{
 *   id: string,
 *   display_name: string,
 *   configured: boolean,
 *   models: AiModel[],
 * }} AiProvider
 *
 * @typedef {{
 *   providers: AiProvider[],
 *   byo: { can_byo: boolean, providers: Record<string, boolean> },
 * }} AiProvidersResponse
 */

const EMPTY_RESPONSE = { providers: [], byo: { can_byo: false, providers: {} } }

/**
 * Fetch the enabled AI providers + models, and the caller's org BYO status.
 *
 * GET /api/v1/ai/providers
 *
 * Never throws — returns an empty provider list on any failure so model
 * pickers degrade gracefully (fall back to "Nubi Default").
 *
 * @returns {Promise<AiProvidersResponse>}
 */
export async function fetchAiProviders() {
  try {
    const data = await get('/ai/providers')
    return {
      providers: Array.isArray(data?.providers) ? data.providers : [],
      byo: {
        can_byo: Boolean(data?.byo?.can_byo),
        providers: data?.byo?.providers ?? {},
      },
    }
  } catch (err) {
    console.warn('[aiApi] fetchAiProviders failed; returning empty list:', err.message)
    return EMPTY_RESPONSE
  }
}

/**
 * Format one model's per-1M-token pricing (synced from litellm.model_cost) as a
 * compact muted label, e.g. `"$3.00 in · $15.00 out / 1M tokens"`. Returns
 * `null` when the model has no pricing (so callers can skip the line entirely).
 *
 * @param {{ input_cost_per_1m?: number|null, output_cost_per_1m?: number|null }} model
 * @returns {string | null}
 */
export function formatModelPrice(model) {
  const inCost = model?.input_cost_per_1m
  const outCost = model?.output_cost_per_1m
  if (inCost == null && outCost == null) return null
  const fmt = (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)
  return `${fmt(inCost)} in · ${fmt(outCost)} out / 1M tokens`
}

/**
 * Store (create or replace) the active org's BYO API key for `provider`.
 * Re-throws on failure so the settings form can surface the error (e.g. a
 * 402 `ai_key_requires_paid_tier` on a free-tier org).
 *
 * POST /api/v1/ai/keys
 *
 * @param {string} orgId
 * @param {string} provider  'anthropic' | 'openai' | 'gemini'
 * @param {string} apiKey
 * @returns {Promise<{ org_id: string, provider: string, configured: boolean }>}
 */
export function setAiKey(orgId, provider, apiKey) {
  return post('/ai/keys', { org_id: orgId, provider, api_key: apiKey })
}

/**
 * Clear the active org's BYO API key for `provider` (no-op if absent).
 * Re-throws on failure so the caller can surface it.
 *
 * DELETE /api/v1/ai/keys (JSON body — the generic `del()` helper in api.js
 * doesn't carry a body, so this issues the request directly, same pattern
 * as `lib/settings.js`'s `_delWithBody`.)
 *
 * @param {string} orgId
 * @param {string} provider
 * @returns {Promise<{ org_id: string, provider: string, configured: boolean }>}
 */
export async function clearAiKey(orgId, provider) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${BASE}/ai/keys`, {
    method: 'DELETE',
    headers,
    body: JSON.stringify({ org_id: orgId, provider }),
    credentials: 'include',
  })
  if (!response.ok) {
    let payload = null
    try { payload = await response.json() } catch { /* empty */ }
    const message =
      payload?.error?.message ??
      payload?.detail ??
      `Request failed: ${response.status} ${response.statusText}`
    const err: Error & { status?: number; payload?: any } = new Error(message)
    err.status = response.status
    err.payload = payload
    throw err
  }
  if (response.status === 204) return null
  return response.json()
}
