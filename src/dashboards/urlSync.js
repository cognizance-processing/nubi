/**
 * urlSync.js — pure URL ↔ dashboard-variable sync helpers (M14-C).
 *
 * Extracted from DashboardViewPage so the logic is (a) shared by the read path
 * (URL → store seed on mount) and the write path (filter change → URL), and
 * (b) unit-testable with bare `node --test` (no JSX transpile needed).
 *
 * Contract:
 *   - Only variable names declared in the spec participate in URL sync — never
 *     pollute the store with unrelated query params (?utm_source=…), and never
 *     write arbitrary names to the URL.
 *   - URL values are strings (the store/SpecRenderer casts as needed).
 *   - Empty / null / undefined values DELETE the param (so a cleared filter
 *     produces a clean, shareable URL rather than `?region=`).
 */

/**
 * Extract declared variable values from a URLSearchParams.
 *
 * @param {URLSearchParams} searchParams
 * @param {string[]} knownVarNames — names declared in spec.variables.
 * @returns {Record<string, string>} only the present, declared params.
 */
export function extractVarsFromURL(searchParams, knownVarNames) {
  const values = {}
  for (const name of knownVarNames || []) {
    const val = searchParams.get(name)
    if (val !== null) {
      values[name] = val
    }
  }
  return values
}

/**
 * Return a NEW URLSearchParams with a single variable applied (set or deleted),
 * honoring the sync rules: locked/undeclared names are left untouched, and an
 * empty value deletes the param.
 *
 * @param {URLSearchParams} prev — the current params (not mutated).
 * @param {string} name
 * @param {unknown} value
 * @param {object} [opts]
 * @param {string[]} [opts.knownVarNames] — only these names may be written.
 * @param {Record<string, unknown>} [opts.lockedParams] — embed-locked names that
 *   must NEVER be written back to the URL (the token is the source of truth).
 * @returns {URLSearchParams} a new params object (caller passes to setSearchParams).
 */
export function applyVarToSearchParams(prev, name, value, opts = {}) {
  const { knownVarNames, lockedParams } = opts
  const next = new URLSearchParams(prev)

  // Never write embed-locked params back to the URL.
  if (lockedParams && Object.prototype.hasOwnProperty.call(lockedParams, name)) {
    return next
  }
  // Opt-in: only sync names declared in the spec.
  if (knownVarNames && !knownVarNames.includes(name)) {
    return next
  }

  if (value === undefined || value === null || value === '') {
    next.delete(name)
  } else {
    next.set(name, String(value))
  }
  return next
}
