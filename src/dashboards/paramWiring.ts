/**
 * paramWiring.ts — the rules that connect a filter to a widget.
 *
 * A board wires a filter to a chart in three places: the filter names a
 * variable (`target_var`), the board declares that variable, and every widget
 * that should react binds one of its query's params to it
 * (`params: {region: {ref: 'region'}}`). All three used to be typed by hand,
 * with names that had to match exactly and nothing checking that they did.
 *
 * Everything here is pure so the editor can do that matching for the user and
 * the rules stay unit-testable:
 *
 *   - `candidateParam`  — which of a query's declared params should carry a
 *                         variable (exact name, else a normalised match, else
 *                         nothing — it never guesses between two candidates).
 *   - `autoBindParams`  — bind every unambiguous match at once, without ever
 *                         overwriting a binding someone made deliberately.
 *   - `wiringRows`      — the "controls these widgets" model: for one variable,
 *                         every widget on the board and why it is or is not
 *                         connected.
 *
 * The matching deliberately stops at the *name*. Nubi's queries are hand-written
 * SQL, so we cannot infer that a `region` filter belongs on the `store_region`
 * column the way a column-mapped tool can — but the param name is a real,
 * author-declared contract, and matching on it covers the case people actually
 * hit (they named the param after the thing it filters).
 */

/** One param as declared on a registered query. */
export interface ParamDecl {
  name: string
  type?: string
  required?: boolean
  default?: unknown
  options_query_id?: string | null
}

/** A widget's binding for one param: a variable reference or a literal. */
export type ParamBinding = { ref: string } | string | number | boolean | null

export type ParamMap = Record<string, ParamBinding>

/**
 * A widget's column-based filter bindings: {varName: 'columnName'}.
 *
 * Unlike `params` (which requires the query to already declare a matching
 * `{{param}}`), this connects a filter straight to a column — the backend
 * planner resolves it against every scope of the query at runtime (see
 * `app.connectors.planner.apply_dashboard_filters`), no SQL mutation
 * required. This is the path that unblocks a query with zero declared
 * params, which used to be a dead end short of hand-editing its SQL.
 */
export type FilterMap = Record<string, string>

/** The column in `filters` currently bound to `varName`, or null. */
export function boundFilterFor(filters: FilterMap | undefined, varName: string): string | null {
  if (!filters || !varName) return null
  const column = filters[varName]
  return typeof column === 'string' && column ? column : null
}

/** Bind one column to a variable, leaving every other filter binding untouched. */
export function bindFilter(filters: FilterMap | undefined, varName: string, column: string): FilterMap {
  return { ...(filters ?? {}), [varName]: column }
}

/** Remove whichever filter binding exists for `varName` (no-op when none). */
export function unbindFilterVar(filters: FilterMap | undefined, varName: string): FilterMap {
  const next = { ...(filters ?? {}) }
  delete next[varName]
  return next
}

/** Widget types that consume a query and can therefore react to a filter. */
export const WIRABLE_TYPES = ['chart', 'table', 'kpi', 'metric', 'pivot', 'stepper'] as const

/** True when a binding points at a variable rather than holding a literal. */
export function isRef(binding: unknown): binding is { ref: string } {
  return !!binding && typeof binding === 'object' && 'ref' in (binding as Record<string, unknown>)
}

/**
 * Fold a name to its comparable form: case and separators carry no meaning
 * when matching a variable to a param (`Region`, `region`, `region-name`).
 * Note this is deliberately NOT a fuzzy match — `region_id` folds to
 * `regionid`, which does not equal `region`, so it is never auto-bound.
 */
export function normalizeName(name: string): string {
  return String(name ?? '').toLowerCase().replace(/[\s_-]+/g, '')
}

/** The param in `params` currently bound to `varName`, or null. */
export function boundParamFor(params: ParamMap | undefined, varName: string): string | null {
  if (!params || !varName) return null
  for (const [paramName, binding] of Object.entries(params)) {
    if (isRef(binding) && binding.ref === varName) return paramName
  }
  return null
}

/** Every variable name this widget's params reference. */
export function referencedVars(params: ParamMap | undefined): string[] {
  if (!params) return []
  const out: string[] = []
  for (const binding of Object.values(params)) {
    if (isRef(binding) && binding.ref && !out.includes(binding.ref)) out.push(binding.ref)
  }
  return out
}

/**
 * Which declared param should carry `varName`.
 *
 * Exact name wins; otherwise a single normalised match wins; two or more
 * normalised matches are ambiguous and return null, because silently picking
 * one of them is how a board ends up wired to the wrong column.
 */
export function candidateParam(declared: ParamDecl[] | undefined, varName: string): string | null {
  if (!Array.isArray(declared) || !varName) return null
  const exact = declared.find(p => p?.name === varName)
  if (exact) return exact.name
  const target = normalizeName(varName)
  const near = declared.filter(p => p?.name && normalizeName(p.name) === target)
  return near.length === 1 ? near[0].name : null
}

/** Bind one param to a variable, leaving every other binding untouched. */
export function bindParam(params: ParamMap | undefined, paramName: string, varName: string): ParamMap {
  return { ...(params ?? {}), [paramName]: { ref: varName } }
}

/** Remove one param binding entirely. */
export function unbindParam(params: ParamMap | undefined, paramName: string): ParamMap {
  const next = { ...(params ?? {}) }
  delete next[paramName]
  return next
}

/** Remove whichever param is bound to `varName` (no-op when none is). */
export function unbindVar(params: ParamMap | undefined, varName: string): ParamMap {
  const paramName = boundParamFor(params, varName)
  return paramName ? unbindParam(params, paramName) : { ...(params ?? {}) }
}

/**
 * Bind every declared param that unambiguously matches a board variable.
 *
 * Existing bindings are never overwritten — someone who deliberately bound
 * `region` to a literal, or to a differently-named variable, keeps it. Returns
 * the new param map plus the list of params that were added, so the caller can
 * tell the user what just happened instead of changing the board silently.
 */
export function autoBindParams(
  params: ParamMap | undefined,
  declared: ParamDecl[] | undefined,
  variableNames: string[] | undefined,
): { params: ParamMap; added: Array<{ param: string; variable: string }> } {
  const next: ParamMap = { ...(params ?? {}) }
  const added: Array<{ param: string; variable: string }> = []
  const vars = Array.isArray(variableNames) ? variableNames.filter(Boolean) : []
  if (!Array.isArray(declared) || vars.length === 0) return { params: next, added }

  for (const varName of vars) {
    // Already wired to this variable through some param — leave it alone.
    if (boundParamFor(next, varName)) continue
    const paramName = candidateParam(declared, varName)
    if (!paramName) continue
    // Never clobber a binding the author already put on that param.
    if (paramName in next) continue
    next[paramName] = { ref: varName }
    added.push({ param: paramName, variable: varName })
  }
  return { params: next, added }
}

/** Why a widget is, or is not, connected to a variable. */
export type WiringState =
  | 'connected'   // a param is bound to this variable
  | 'available'   // its query declares a param we can bind
  | 'choose'      // its query has params, but none match by name
  | 'no-param'    // its query declares no params at all
  | 'unknown'     // the query's params have not loaded (or it has no query)

export interface WiringRow {
  id: string
  label: string
  type: string
  state: WiringState
  /** The param that is bound, or the one that would be bound on connect. */
  paramName: string | null
  /** Every param the widget's query declares — the picker's options. */
  options: string[]
  /** True when the binding points somewhere other than this variable. */
  boundElsewhere: string | null
  /**
   * The widget's bound query id ('' when none). Carried so a `no-param` row
   * can offer to ADD the parameter to that query rather than leaving the
   * author to go hand-edit its SQL.
   */
  queryId: string
  /**
   * Which mechanism a 'connected' row is actually wired through: a declared
   * `{{param}}` (`params`) or a direct column binding (`filters`, no SQL
   * mutation — see `FilterMap`). `null` for every other state.
   */
  boundVia: 'param' | 'filter' | null
  /** The column this variable is bound to via `widget.filters`, or null. */
  columnName: string | null
}

interface WiringInput {
  widgets: Array<Record<string, any>>
  varName: string
  /** query id → declared params. A missing entry means "not loaded yet". */
  paramsByQueryId: Map<string, ParamDecl[]> | Record<string, ParamDecl[]>
  /** Resolve a spec entry to its effective widget (library refs). */
  resolve?: (w: Record<string, any>) => Record<string, any>
  /** Human label for a widget — the editor already has one. */
  labelFor?: (w: Record<string, any>) => string
}

function lookupParams(
  source: WiringInput['paramsByQueryId'],
  queryId: string,
): ParamDecl[] | undefined {
  if (!queryId) return undefined
  if (source instanceof Map) return source.get(queryId)
  return source?.[queryId]
}

/**
 * The model behind "controls these widgets": one row per data widget on the
 * board, saying whether this variable reaches it and what would happen if you
 * ticked the box.
 *
 * Filters, text and section widgets are left out — they consume no query, so
 * "connect this filter to that heading" is not a thing a person can mean.
 */
export function wiringRows({
  widgets,
  varName,
  paramsByQueryId,
  resolve = w => w,
  labelFor,
}: WiringInput): WiringRow[] {
  const rows: WiringRow[] = []
  for (const entry of widgets ?? []) {
    const w = resolve(entry) ?? entry
    const type = w?.type ?? ''
    if (!(WIRABLE_TYPES as readonly string[]).includes(type)) continue

    const queryId = w.query_id ?? ''
    const declared = lookupParams(paramsByQueryId, queryId)
    const options = Array.isArray(declared) ? declared.map(p => p.name).filter(Boolean) : []
    const boundParam = boundParamFor(w.params, varName)
    const boundColumn = boundFilterFor(w.filters, varName)

    let state: WiringState
    let paramName: string | null = boundParam
    let boundVia: WiringRow['boundVia'] = null
    if (boundParam) {
      state = 'connected'
      boundVia = 'param'
    } else if (boundColumn) {
      state = 'connected'
      boundVia = 'filter'
    } else if (!queryId || declared === undefined) {
      state = 'unknown'
    } else if (options.length === 0) {
      state = 'no-param'
    } else {
      const candidate = candidateParam(declared, varName)
      if (candidate) { state = 'available'; paramName = candidate }
      else { state = 'choose'; paramName = null }
    }

    rows.push({
      id: w.id ?? entry.id,
      label: labelFor ? labelFor(w) : (w.title || w.id || 'Untitled'),
      type,
      state,
      paramName,
      options,
      boundElsewhere: null,
      queryId,
      boundVia,
      columnName: boundColumn,
    })
  }
  return rows
}

/** How many rows are actually receiving the variable. */
export function connectedCount(rows: WiringRow[]): number {
  return rows.filter(r => r.state === 'connected').length
}

/**
 * Declared params that no board variable can fill — the widget side of the
 * same gap. Used to offer "create a filter for this" instead of leaving the
 * author to notice a param is dangling.
 */
export function unfilledParams(
  declared: ParamDecl[] | undefined,
  params: ParamMap | undefined,
  variableNames: string[] | undefined,
): ParamDecl[] {
  if (!Array.isArray(declared)) return []
  const vars = new Set((variableNames ?? []).map(normalizeName))
  return declared.filter(p => {
    if (!p?.name) return false
    if (params && p.name in params) return false
    return !vars.has(normalizeName(p.name))
  })
}

/**
 * The variable name a filter labelled `label` should write to.
 *
 * Authors name the filter ("Region", "Store group"), not the variable, so the
 * variable follows the label until someone edits it directly. Digits are kept,
 * a leading digit is prefixed since `{{2024}}` is not a usable placeholder, and
 * anything else folds to underscores.
 */
export function varNameFromLabel(label: string): string {
  const slug = String(label ?? '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  if (!slug) return ''
  return /^[0-9]/.test(slug) ? `v_${slug}` : slug
}

// ---------------------------------------------------------------------------
// Naming guardrails — keep a filter's variable aligned with what the board's
// queries actually take, and keep connections alive when it is renamed.
// ---------------------------------------------------------------------------

/** Words people put around the real name in a filter label. Edges only, so
 *  "Date of birth" keeps its "of" while "Filter by region" sheds two words. */
const LEADING_FILLER = new Set(['the', 'a', 'an', 'select', 'choose', 'pick', 'filter', 'by', 'your'])
const TRAILING_FILLER = new Set(['filter', 'filters', 'dropdown', 'picker', 'selector'])

/**
 * The variable a filter labelled `label` should write to, given the names
 * this board already uses (`knownNames`: declared variables and the params its
 * queries take).
 *
 * `varNameFromLabel` turns "The region filter" into `the_region_filter`, which
 * matches nothing, so the filter silently connects to no widget. This strips
 * filler words from the label's edges and, when what remains is one of the
 * board's existing names (case/separators folded), uses that name verbatim so
 * the exact-match auto-wiring fires. It never guesses: two distinct known names
 * folding to the same thing fall back to the plain slug.
 */
export function suggestVarName(label: string, knownNames: string[] = []): string {
  const raw = varNameFromLabel(label)
  if (!raw) return ''
  const tokens = raw.split('_').filter(Boolean)
  while (tokens.length > 1 && LEADING_FILLER.has(tokens[0])) tokens.shift()
  while (tokens.length > 1 && TRAILING_FILLER.has(tokens[tokens.length - 1])) tokens.pop()
  const core = varNameFromLabel(tokens.join('_')) || raw

  const known = [...new Set((knownNames ?? []).filter(Boolean))]
  for (const candidate of [core, raw]) {
    const exact = known.find(n => n === candidate)
    if (exact) return exact
    const folded = normalizeName(candidate)
    const near = known.filter(n => normalizeName(n) === folded)
    if (near.length === 1) return near[0]
  }
  return core
}

/** One query param name and how many widgets on the board could take it. */
export interface ParamUsage { name: string; widgets: number }

/**
 * Every param declared by the queries on this board, with how many widgets
 * declare it — the names a filter could use and actually reach something.
 * Most-used first, then alphabetical so the order is stable.
 */
export function boardParamUsage({
  widgets,
  paramsByQueryId,
  resolve = w => w,
}: Omit<WiringInput, 'varName' | 'labelFor'>): ParamUsage[] {
  const counts = new Map<string, number>()
  for (const entry of widgets ?? []) {
    const w = resolve(entry) ?? entry
    if (!(WIRABLE_TYPES as readonly string[]).includes(w?.type ?? '')) continue
    const declared = lookupParams(paramsByQueryId, w.query_id ?? '')
    if (!Array.isArray(declared)) continue
    const seen = new Set<string>()
    for (const p of declared) {
      if (!p?.name || seen.has(p.name)) continue
      seen.add(p.name)
      counts.set(p.name, (counts.get(p.name) ?? 0) + 1)
    }
  }
  return [...counts.entries()]
    .map(([name, n]) => ({ name, widgets: n }))
    .sort((a, b) => b.widgets - a.widgets || a.name.localeCompare(b.name))
}

type Json = any

const isPlainObject = (v: unknown): v is Record<string, Json> =>
  !!v && typeof v === 'object' && !Array.isArray(v)

/** A binding object is exactly `{ref: name}` — a library-ref widget also has
 *  a `ref` key, but alongside id/overrides, so it is never mistaken for one. */
const isBindingTo = (v: unknown, name: string) =>
  isPlainObject(v) && v.ref === name && Object.keys(v).length === 1

/**
 * Rewrite every place `widget` READS variable `from` so it reads `to`:
 * `{ref}` param bindings, direct column bindings (`filters` keys) and
 * `props.variable` (date display). Walks nested widgets (stepper steps) too.
 * Places that WRITE a variable (target_var, setVar) are left alone — renaming
 * a filter must not hijack another producer. Returns the same object when
 * nothing changed, so callers can skip no-op patches.
 */
export function renameVariableReads(widget: Json, from: string, to: string): Json {
  if (!from || !to || from === to) return widget
  const walk = (node: Json): Json => {
    if (Array.isArray(node)) {
      let changed = false
      const out = node.map(item => { const n = walk(item); if (n !== item) changed = true; return n })
      return changed ? out : node
    }
    if (!isPlainObject(node)) return node
    if (isBindingTo(node, from)) return { ref: to }
    let changed = false
    const out: Record<string, Json> = {}
    for (const [key, value] of Object.entries(node)) {
      if (key === 'filters' && isPlainObject(value) && typeof value[from] === 'string') {
        const renamed: Record<string, Json> = {}
        for (const [k, v] of Object.entries(value)) renamed[k === from ? to : k] = v
        out[key] = renamed
        changed = true
      } else if (key === 'variable' && value === from) {
        out[key] = to
        changed = true
      } else {
        const next = walk(value)
        if (next !== value) changed = true
        out[key] = next
      }
    }
    return changed ? out : node
  }
  return walk(widget)
}

/** True when `widget` (or a widget nested in it) reads variable `name`. */
export function readsVariable(widget: Json, name: string): boolean {
  return !!name && renameVariableReads(widget, name, `${name}\u0000`) !== widget
}

const WRITER_KEYS = new Set(['target_var', 'setVar', 'var'])

/** True when `widget` (or a widget nested in it) WRITES variable `name` —
 *  a filter's target_var, a click's setVar/setVars, a drilldown target. */
export function writesVariable(widget: Json, name: string): boolean {
  if (!name) return false
  const walk = (node: Json): boolean => {
    if (Array.isArray(node)) return node.some(walk)
    if (!isPlainObject(node)) return false
    for (const [key, value] of Object.entries(node)) {
      if (WRITER_KEYS.has(key) && value === name) return true
      if (typeof value === 'object' && value !== null && walk(value)) return true
    }
    return false
  }
  return walk(widget)
}

/** varName → how many widgets read it. One pass, for canvas badges. */
export function readerCounts(
  widgets: Json[],
  varNames: string[],
  resolve: (w: Json) => Json = w => w,
): Map<string, number> {
  const out = new Map<string, number>()
  const names = [...new Set((varNames ?? []).filter(Boolean))]
  for (const n of names) out.set(n, 0)
  for (const entry of widgets ?? []) {
    const w = resolve(entry) ?? entry
    for (const n of names) if (readsVariable(w, n)) out.set(n, (out.get(n) ?? 0) + 1)
  }
  return out
}

/**
 * What renaming a filter's variable from `from` to `to` would do.
 *
 * `moves` is how many widgets read `from` and would be re-pointed. When some
 * OTHER widget also writes `from` (a second filter, a chart click) the
 * variable is shared, and moving its readers would break that producer —
 * `sharedWith` lists those writers and the caller should not move anything.
 */
export function planVariableRename(
  widgets: Json[],
  from: string,
  to: string,
  selfId: string,
  resolve: (w: Json) => Json = w => w,
): { moves: number; sharedWith: string[] } {
  if (!from || !to || from === to) return { moves: 0, sharedWith: [] }
  let moves = 0
  const sharedWith: string[] = []
  for (const entry of widgets ?? []) {
    if (entry?.id === selfId) continue
    const w = resolve(entry) ?? entry
    if (writesVariable(w, from)) sharedWith.push(w.id ?? entry.id)
    if (readsVariable(w, from)) moves += 1
  }
  return { moves, sharedWith }
}
