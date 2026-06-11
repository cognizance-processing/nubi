/**
 * variables.js — API client for the Nubi flow VARIABLES store.
 *
 * Variables are a PERSISTENT, org-scoped (and optionally project-scoped)
 * key/value store. Unlike secrets, their values ARE readable — they are the
 * long-term home for values referenced from flow cells as `{{ vars.NAME }}`
 * (and written by python cells via `set_var`).
 *
 * Endpoints (backend/app/routes/variables.py):
 *   GET    /variables               listVariables  → [{ key, value, ... }]
 *   GET    /variables/{key}         getVariable    → { key, value, ... } | null
 *   PUT    /variables/{key}         setVariable    { value } (upsert, writer role)
 *   DELETE /variables/{key}         deleteVariable
 *
 * Org/project scope is applied server-side from the X-Org-Id / X-Project-Id
 * headers the api client attaches automatically (see api.js).
 *
 * `value` on the wire is arbitrary JSON. The store has no first-class `type`
 * column, so `type` here is a UI-only hint: `setVariable` COERCES the value to
 * the requested type before persisting, and `inferType` derives a type back
 * from a stored value for display. This keeps `{{ vars.NAME }}` substitution
 * clean (the raw value is stored as-is — never wrapped in an envelope).
 *
 * Every function degrades gracefully: reads catch transport errors and return
 * safe empty values; writes surface the error so the caller can show it.
 */

import { get, put, del } from './api.js'

const BASE = '/variables'

/** The variable types the UI offers. */
export const VARIABLE_TYPES = ['string', 'number', 'boolean', 'json']

/**
 * Infer a UI type hint from a stored (already-parsed JSON) value.
 *
 * @param {any} value
 * @returns {'string'|'number'|'boolean'|'json'}
 */
export function inferType(value) {
  if (typeof value === 'number') return 'number'
  if (typeof value === 'boolean') return 'boolean'
  if (value !== null && typeof value === 'object') return 'json'
  return 'string'
}

/**
 * Coerce a raw input value into the JSON shape implied by `type`.
 *
 * Strings are passed through for 'string'; numbers/booleans are parsed; 'json'
 * attempts JSON.parse and falls back to the raw string if it is not valid JSON.
 *
 * @param {any} value
 * @param {'string'|'number'|'boolean'|'json'} [type]
 * @returns {any}
 */
export function coerceValue(value, type) {
  switch (type) {
    case 'number': {
      if (typeof value === 'number') return value
      const n = Number(value)
      return Number.isFinite(n) ? n : 0
    }
    case 'boolean': {
      if (typeof value === 'boolean') return value
      return value === true || value === 'true' || value === '1'
    }
    case 'json': {
      if (typeof value !== 'string') return value
      try {
        return JSON.parse(value)
      } catch {
        return value // keep the raw string rather than losing the input
      }
    }
    case 'string':
    default:
      return value == null ? '' : String(value)
  }
}

/**
 * Normalize a backend variable row to a consistent shape with a derived `type`.
 * The backend uses `key`; we expose both `name` and `key` for convenience.
 *
 * @param {object} row
 * @returns {{ name: string, key: string, value: any, type: string, updated_at?: string }}
 */
function normalize(row) {
  const key = row?.key ?? row?.name ?? ''
  const value = row?.value
  return {
    ...row,
    name: key,
    key,
    value,
    type: inferType(value),
  }
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

/**
 * List variables for the active org/project scope.
 *
 * @returns {Promise<Array<{ name: string, key: string, value: any, type: string }>>}
 *   Returns [] on any failure so callers can render an empty state.
 */
export async function listVariables() {
  try {
    const data = await get(BASE)
    let rows = []
    if (Array.isArray(data)) rows = data
    else if (Array.isArray(data?.variables)) rows = data.variables
    else if (Array.isArray(data?.items)) rows = data.items
    return rows.map(normalize)
  } catch (err) {
    console.warn('[variables] listVariables failed:', err.message)
    return []
  }
}

// ---------------------------------------------------------------------------
// Get one
// ---------------------------------------------------------------------------

/**
 * Fetch a single variable by name/key.
 *
 * @param {string} name
 * @returns {Promise<{ name: string, key: string, value: any, type: string } | null>}
 *   Returns null if missing or on any failure.
 */
export async function getVariable(name) {
  try {
    const row = await get(`${BASE}/${encodeURIComponent(name)}`)
    return row ? normalize(row) : null
  } catch (err) {
    console.warn('[variables] getVariable failed:', err.message)
    return null
  }
}

// ---------------------------------------------------------------------------
// Set / upsert
// ---------------------------------------------------------------------------

/**
 * Create or update (upsert) a variable. The value is coerced to the JSON shape
 * implied by `type` before it is persisted.
 *
 * @param {{ name: string, value: any, type?: 'string'|'number'|'boolean'|'json' }} params
 * @returns {Promise<{ name: string, key: string, value: any, type: string }>}
 *   Throws on failure so the caller can surface it.
 */
export async function setVariable({ name, value, type } = {}) {
  if (!name) throw new Error('Variable name is required.')
  const coerced = coerceValue(value, type)
  try {
    const row = await put(`${BASE}/${encodeURIComponent(name)}`, { value: coerced })
    return row ? normalize(row) : normalize({ key: name, value: coerced })
  } catch (err) {
    console.warn('[variables] setVariable failed:', err.message)
    throw err
  }
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

/**
 * Delete a variable by name/key.
 *
 * @param {string} name
 * @returns {Promise<boolean>} true on success; throws on failure.
 */
export async function deleteVariable(name) {
  try {
    await del(`${BASE}/${encodeURIComponent(name)}`)
    return true
  } catch (err) {
    console.warn('[variables] deleteVariable failed:', err.message)
    throw err
  }
}
