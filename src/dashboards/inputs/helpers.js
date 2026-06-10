/**
 * helpers.js — pure value-shape + preset helpers for the dashboard input
 * primitives (Track F).
 *
 * These are intentionally framework-free (no React) so they can be unit tested
 * in isolation and reused by both the input primitives and, eventually, the
 * VariableStore `pick` bindings.
 *
 * Multiselect variable value shape
 * --------------------------------
 *   ["a", "b"]                                 legacy plain array → include
 *   { mode: "include", values: ["a", "b"] }
 *   { mode: "exclude", values: ["a", "b"] }    "all but those selected"
 *   { mode: "all" }                            explicit no-constraint
 *
 * `undefined` / `null` / empty array / empty include all collapse to "all".
 */

// ---------------------------------------------------------------------------
// Multiselect value shape
// ---------------------------------------------------------------------------

/** Normalise any supported multiselect value to { mode, values }. */
export function normMulti(value) {
  if (value == null) return { mode: 'all', values: [] }
  if (Array.isArray(value)) {
    const values = value.map(String)
    return values.length === 0 ? { mode: 'all', values: [] } : { mode: 'include', values }
  }
  if (typeof value === 'object') {
    const mode = value.mode === 'exclude' ? 'exclude'
      : value.mode === 'all' ? 'all'
      : value.mode === 'include' ? 'include'
      : 'include'
    const values = Array.isArray(value.values) ? value.values.map(String) : []
    if (mode === 'all') return { mode: 'all', values: [] }
    if (values.length === 0 && mode === 'include') return { mode: 'all', values: [] }
    return { mode, values }
  }
  // Scalar — treat as a single include value.
  return { mode: 'include', values: [String(value)] }
}

/** True when the value represents "all but the selected" semantics. */
export function isExclude(value) {
  return normMulti(value).mode === 'exclude'
}

/** The selected values array (strings) regardless of include/exclude. */
export function valuesOf(value) {
  return normMulti(value).values
}

/** 'all' | 'include' | 'exclude'. */
export function modeOf(value) {
  return normMulti(value).mode
}

/**
 * Build a multiselect value from a mode + values pair, collapsing to the most
 * backward-compatible representation:
 *   - include with values  → plain array (legacy-compatible)
 *   - include with none     → [] (empty array == all, legacy-compatible)
 *   - exclude               → { mode:'exclude', values }
 *   - all                   → []
 */
export function makeMulti(mode, values) {
  const v = Array.isArray(values) ? values.map(String) : []
  if (mode === 'exclude') {
    return v.length === 0 ? [] : { mode: 'exclude', values: v }
  }
  // include / all → plain array (empty == all). Keeps legacy consumers working.
  return v
}

// ---------------------------------------------------------------------------
// Date range presets
// ---------------------------------------------------------------------------

/** Format a Date as a local YYYY-MM-DD string (no timezone shift). */
export function toISODate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Default preset rail order. */
export const DEFAULT_PRESETS = [
  'today',
  'yesterday',
  'last_7d',
  'last_30d',
  'last_90d',
  'mtd',
  'qtd',
  'ytd',
  'custom',
]

export const PRESET_LABELS = {
  today: 'Today',
  yesterday: 'Yesterday',
  last_7d: 'Last 7 days',
  last_30d: 'Last 30 days',
  last_90d: 'Last 90 days',
  mtd: 'Month to date',
  qtd: 'Quarter to date',
  ytd: 'Year to date',
  custom: 'Custom',
}

/**
 * Resolve a relative preset key to a concrete { from, to } pair.
 *
 * Stored dashboards keep the relative key (`{ preset: "last_30d" }`) so they
 * stay relative; this resolves to dates at param-resolution time.
 *
 * @param {string} preset
 * @param {Date}   [now]  injectable clock for tests (defaults to new Date()).
 * @returns {{from: string, to: string} | null}  null for "custom"/unknown.
 */
export function resolvePreset(preset, now = new Date()) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const end = toISODate(today)

  const minus = (days) => {
    const d = new Date(today)
    d.setDate(d.getDate() - days)
    return toISODate(d)
  }

  switch (preset) {
    case 'today':
      return { from: end, to: end }
    case 'yesterday': {
      const y = minus(1)
      return { from: y, to: y }
    }
    case 'last_7d':
      return { from: minus(6), to: end }
    case 'last_30d':
      return { from: minus(29), to: end }
    case 'last_90d':
      return { from: minus(89), to: end }
    case 'mtd':
      return { from: toISODate(new Date(today.getFullYear(), today.getMonth(), 1)), to: end }
    case 'qtd': {
      const q = Math.floor(today.getMonth() / 3) * 3
      return { from: toISODate(new Date(today.getFullYear(), q, 1)), to: end }
    }
    case 'ytd':
      return { from: toISODate(new Date(today.getFullYear(), 0, 1)), to: end }
    case 'custom':
    default:
      return null
  }
}

/**
 * Resolve a daterange variable value to a concrete { from, to }.
 * Accepts { preset } (relative), { from, to } (absolute), or null.
 */
export function resolveDateRange(value, now = new Date()) {
  if (!value) return { from: '', to: '' }
  if (typeof value === 'object' && value.preset && value.preset !== 'custom') {
    const r = resolvePreset(value.preset, now)
    if (r) return r
  }
  return { from: value.from ?? '', to: value.to ?? '' }
}
