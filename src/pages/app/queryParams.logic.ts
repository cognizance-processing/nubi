/**
 * queryParams.logic.js — pure, side-effect-free logic behind the query
 * editor's parameter panel: SQL placeholder extraction, the auto-param
 * reconciliation rule, and the "Add filter parameter" snippet builder.
 *
 * Extracted so it can be unit-tested with `node --test` (no React / jsdom),
 * matching this directory's existing metricBlock.logic.js convention. The
 * QueryWorkspace component owns React state + rendering; every function
 * here is deterministic given its inputs.
 *
 * Provenance (`param.auto`)
 * -------------------------
 * A param this module's `reconcileAutoParams` adds because it found a new
 * `{{name}}` / `{% if name %}` in the SQL is flagged `auto: true`. Only an
 * `auto` param is ever removed when its placeholder disappears from the SQL
 * — a param the presenter typed into the panel directly, or added via
 * "Add filter parameter" (`auto: false`), survives edits to the SQL text
 * that don't happen to touch its own placeholder.
 */

// ---------------------------------------------------------------------------
// Placeholder extraction
// ---------------------------------------------------------------------------

/**
 * Every param name a SQL body references, across both the old bare-token
 * form and the real Jinja2 vocabulary this codebase's queries actually use:
 *   {{ name }}              — bare substitution (the legacy/simple case)
 *   {{ name | inclause }}   — filtered output (any `| filter` chain)
 *   {{ name.from }}         — dotted attribute access (a daterange param)
 *   {% if name %}           — a guard with no output token of its own
 *   {% elif name %}
 * A name-only match (no full expression parser) is enough here: this drives
 * which params the auto-sync panel offers, not what actually executes —
 * the backend's Jinja2 engine (backend/app/connectors/template.py) is the
 * real authority on whether the SQL is valid.
 *
 * @param {string} sql
 * @returns {string[]} distinct param names, in first-appearance order
 */
export function extractPlaceholders(sql) {
  const found = new Set()
  const exprRe = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)/g
  let m
  while ((m = exprRe.exec(sql ?? '')) !== null) found.add(m[1])
  const guardRe = /\{%-?\s*(?:if|elif)\s+([A-Za-z_][A-Za-z0-9_]*)/g
  while ((m = guardRe.exec(sql ?? '')) !== null) found.add(m[1])
  return Array.from(found)
}

// ---------------------------------------------------------------------------
// Auto-param reconciliation
// ---------------------------------------------------------------------------

/**
 * Reconcile a query's declared params against what its SQL currently
 * references. Pure: same inputs → same output, no dependence on call order.
 *
 *   - a declared param whose placeholder is gone AND was auto-added → dropped
 *   - every other declared param (still referenced, or never auto) → kept as-is
 *   - a name found in the SQL with no declared param yet → added, `auto: true`
 *
 * Returns the SAME array reference when nothing changed, so a caller using
 * this inside a React state setter can bail out with the `prev` identity
 * (`setParams(prev => reconcileAutoParams(prev, sql))`) without an
 * unnecessary re-render.
 *
 * @param {Array<{name:string, auto?:boolean}>} prevParams
 * @param {string} sql
 * @returns {Array<{name:string, type:string, default:any, required:boolean, auto?:boolean}>}
 */
export function reconcileAutoParams(prevParams, sql) {
  const prev = prevParams ?? []
  const found = new Set(extractPlaceholders(sql))
  const kept = prev.filter(p => !p.auto || found.has(p.name))
  const existingNames = new Set(kept.map(p => p.name))
  const newOnes = Array.from(found).filter(n => !existingNames.has(n))
  if (newOnes.length === 0 && kept.length === prev.length) return prev
  return [
    ...kept,
    ...newOnes.map(n => ({ name: n, type: 'text', default: null, required: false, auto: true })),
  ]
}

// ---------------------------------------------------------------------------
// "Add filter parameter" — snippet builder
// ---------------------------------------------------------------------------

export const FILTER_PARAM_TYPES = [
  { value: 'single', label: 'Single value', paramType: 'text' },
  { value: 'multiselect', label: 'Multi-select', paramType: 'multiselect' },
  { value: 'daterange', label: 'Date range', paramType: 'daterange' },
]

/** Default value a freshly-declared param of this UI type should carry. */
export function defaultForFilterParamType(uiType) {
  if (uiType === 'multiselect') return []
  return null
}

const VALID_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

/**
 * Validate a candidate param name for "Add filter parameter": a real
 * identifier, not already declared on this query.
 *
 * @param {string} name
 * @param {string[]} existingNames
 * @returns {string | null} an error message, or null when valid
 */
export function validateNewParamName(name, existingNames) {
  const trimmed = (name ?? '').trim()
  if (!trimmed) return 'Name is required.'
  if (!VALID_NAME_RE.test(trimmed)) {
    return 'Use letters, numbers, underscores — starting with a letter or underscore.'
  }
  if ((existingNames ?? []).includes(trimmed)) {
    return `A param named "${trimmed}" already exists.`
  }
  return null
}

/**
 * Build the guarded Jinja snippet + param descriptor for "Add filter
 * parameter". The column name is left as a literal `<column>` placeholder
 * in the snippet — the editor selects every occurrence (Monaco multi-cursor)
 * so the presenter types the real column once.
 *
 * Mirrors the exact idiom every hand-written query on this board already
 * uses (`{% if country_filter %} and (country_desc) IN
 * {{ country_filter | inclause }} {% endif %}`) — an unset multiselect
 * defaults to `[]`, which is falsy in Jinja, so the guard is skipped and the
 * query means "all" until a value is bound. Never uses `| sqlsafe` — every
 * bound value stays a real placeholder, never interpolated into the SQL text.
 *
 * @param {{name: string, uiType: 'single'|'multiselect'|'daterange'}} args
 * @returns {{snippet: string, param: {name, type, default, required, auto}}}
 */
export function buildFilterParamSnippet({ name, uiType }) {
  const paramType = FILTER_PARAM_TYPES.find(t => t.value === uiType)?.paramType ?? 'text'
  let snippet
  if (uiType === 'multiselect') {
    snippet = `{% if ${name} %} AND <column> IN {{ ${name} | inclause }} {% endif %}`
  } else if (uiType === 'daterange') {
    snippet =
      `{% if ${name} %} AND <column> >= {{ ${name}.from }} ` +
      `AND <column> < {{ ${name}.to }} {% endif %}`
  } else {
    snippet = `{% if ${name} %} AND <column> = {{ ${name} }} {% endif %}`
  }
  return {
    snippet,
    param: {
      name,
      type: paramType,
      default: defaultForFilterParamType(uiType),
      required: false,
      auto: false,
    },
  }
}
