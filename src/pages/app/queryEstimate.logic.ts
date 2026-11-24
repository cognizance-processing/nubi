/**
 * Query size estimate — the pure rules behind the BigQuery-style
 * "This query will process 4.71 GiB when run." line under the query editor.
 *
 * The line asks POST /query/estimate, which resolves the SAME plan as a Run
 * (auth, scope, RLS, named params) and dry-runs it without executing it.
 * These helpers decide WHAT to ask about, following what is being written:
 *   - a highlighted part of the SQL runs (and is estimated) on its own;
 *   - a saved query runs (and is estimated) by id with the filter values on
 *     screen; after Update it is checked again;
 *   - unsaved changes to a saved query are estimated as typed when they are
 *     plain SQL (Run itself still uses the saved copy until Update). Filter
 *     tags can only be filled in for the saved copy, so then the line asks
 *     for Update instead;
 *   - an unsaved query runs (and is estimated) as the editor text.
 *
 * Kept free of React so it runs under the repo's node --test convention.
 */

export type EstimateScope = 'selection' | 'query' | 'edited'

export type EstimatePlan =
  | { kind: 'request'; scope: EstimateScope; key: string; body: Record<string, unknown> }
  | { kind: 'blocked'; scope: 'selection'; reason: 'filter-tags' }
  | { kind: 'blocked'; scope: 'query'; reason: 'needs-save' }
  | { kind: 'blocked'; scope: 'edited'; reason: 'needs-update' }
  | null

export type EstimateState =
  | { status: 'idle' }
  | { status: 'checking' }
  | { status: 'ok'; bytes: number }
  | { status: 'unsupported' }
  | { status: 'failed'; detail?: string }

export interface EstimateMessage {
  tone: 'ok' | 'busy' | 'muted' | 'warn'
  text: string
  title?: string
}

// Template tags the server can only fill in for a SAVED query: any {% … %}
// block or {# … #} comment, or a {{ … }} that isn't an org/project
// {{ vars.* }} variable (the only kind unsaved SQL may use).
const FILTER_TAG = /\{[%#]|\{\{(?!\s*vars\.)/

/** True when the SQL uses filter/template tags that need a saved query. */
export function usesFilterTags(sql: string | null | undefined): boolean {
  return FILTER_TAG.test(sql ?? '')
}

/**
 * The highlighted text that should run on its own, or '' when Run should run
 * the whole query: nothing highlighted, only whitespace highlighted, or the
 * whole query highlighted (Ctrl+A) — that last one is simply "the query".
 */
export function pickSelection(selected: string | null | undefined, sql: string | null | undefined): string {
  const s = (selected ?? '').trim()
  if (!s) return ''
  if (s === (sql ?? '').trim()) return ''
  return s
}

export const SELECTION_FILTER_TAGS_RUN_ERROR =
  'The highlighted part uses filter values ({{ }} or {% %} tags), so it can’t run on its own. ' +
  'Highlight a part without them, or clear the highlight to run the whole query.'

export interface EstimateInputs {
  /** Output of pickSelection: '' when the whole query runs. */
  selection: string
  /** The editor text. */
  sql: string
  /** A saved query's SQL as last saved; null for an unsaved query. */
  savedSql: string | null
  /** Set only for a saved query (Run then runs it by id). */
  queryId: string | null
  /** Filter values exactly as Run sends them (blanks already left out). */
  namedParams: Record<string, unknown>
  /** Any filter value set on screen (an unsaved query can't use them). */
  hasParamValues: boolean
  datastoreId: string
  /** Goes up on every save / restore, so a saved query is checked again after Update. */
  savedVersion?: number
}

// The key is what a check is remembered by: the request body plus, for a
// saved query asked about by id, which save of it this is.
function request(scope: EstimateScope, body: Record<string, unknown>, version = 0): EstimatePlan {
  return { kind: 'request', scope, key: JSON.stringify({ b: body, v: version }), body }
}

/** The request body inside a plan key, so the key alone can drive a check. */
export function bodyFromKey(key: string): Record<string, unknown> {
  return JSON.parse(key).b
}

/** What to estimate for what's on screen, or null for nothing. */
export function planQueryEstimate(i: EstimateInputs): EstimatePlan {
  const ds = i.datastoreId ? { datastore_id: i.datastoreId } : {}
  if (i.selection) {
    if (usesFilterTags(i.selection)) return { kind: 'blocked', scope: 'selection', reason: 'filter-tags' }
    return request('selection', { sql: i.selection, ...ds })
  }
  if (i.queryId) {
    if (i.savedSql != null && i.sql !== i.savedSql) {
      if (!i.sql.trim()) return null
      if (usesFilterTags(i.sql)) return { kind: 'blocked', scope: 'edited', reason: 'needs-update' }
      return request('edited', { sql: i.sql, ...ds })
    }
    const np = Object.keys(i.namedParams).length > 0 ? { named_params: i.namedParams } : {}
    return request('query', { query_id: i.queryId, ...np, ...ds }, i.savedVersion ?? 0)
  }
  if (!i.sql.trim()) return null
  if (i.hasParamValues || usesFilterTags(i.sql)) return { kind: 'blocked', scope: 'query', reason: 'needs-save' }
  return request('query', { sql: i.sql, ...ds })
}

/** Turn a POST /query/estimate response into a display state. */
export function readEstimateResponse(res: unknown): EstimateState {
  const r = (res ?? {}) as { supported?: boolean; est_bytes_scanned?: unknown; connector_type?: string }
  if (r.supported && typeof r.est_bytes_scanned === 'number' && Number.isFinite(r.est_bytes_scanned)) {
    return { status: 'ok', bytes: r.est_bytes_scanned }
  }
  // BigQuery can always dry-run, so "unsupported" from it means the dry run
  // itself failed (usually the SQL has an error). Other databases that can't
  // say how much they'd read just show nothing.
  if (r.connector_type === 'bigquery') return { status: 'failed' }
  return { status: 'unsupported' }
}

const UNITS = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB']

/**
 * Bytes in the same units as the BigQuery console ("623.1 KiB"): 1 KiB is
 * 1,024 bytes. Two decimals below 100, one from 100, trailing zeros dropped.
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${Math.round(bytes)} B`
  let v = bytes / 1024
  let i = 0
  while (v >= 1024 && i < UNITS.length - 1) { v /= 1024; i++ }
  let s = v.toFixed(v >= 100 ? 1 : 2)
  if (Number(s) >= 1024 && i < UNITS.length - 1) { v /= 1024; i++; s = v.toFixed(2) }
  return `${s.replace(/\.?0+$/, '')} ${UNITS[i]}`
}

/** The line to show for a plan + state, or null to show nothing. */
export function estimateMessage(plan: EstimatePlan, state: EstimateState): EstimateMessage | null {
  if (!plan) return null
  if (plan.kind === 'blocked') {
    if (plan.scope === 'selection') {
      return { tone: 'warn', text: 'The highlighted part uses filter values, so it can only run as part of the whole query.' }
    }
    if (plan.scope === 'edited') {
      return { tone: 'muted', text: 'Press Update to see how much data your changes will process.' }
    }
    return { tone: 'muted', text: 'Save this query to see how much data it will process. Filter values only work in a saved query.' }
  }
  if (state.status === 'checking') {
    return { tone: 'busy', text: 'Checking the size…' }
  }
  if (state.status === 'ok') {
    const size = formatBytes(state.bytes)
    const title = `${state.bytes.toLocaleString()} bytes`
    if (plan.scope === 'selection') return { tone: 'ok', text: `The highlighted part will process ${size} when run.`, title }
    if (plan.scope === 'edited') return { tone: 'ok', text: `With your unsaved changes, this query would process ${size}.`, title }
    return { tone: 'ok', text: `This query will process ${size} when run.`, title }
  }
  if (state.status === 'failed') {
    const text = plan.scope === 'selection'
      ? 'Couldn’t work out the size of the highlighted part. It may not be a complete query on its own.'
      : plan.scope === 'edited'
        ? 'Couldn’t work out how much data your changes would process. Check them for errors.'
        : 'Couldn’t work out how much data this query will process. Check it for errors.'
    return { tone: 'muted', text, title: state.detail }
  }
  return null
}
