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
    const bound = boundParamFor(w.params, varName)

    let state: WiringState
    let paramName: string | null = bound
    if (bound) {
      state = 'connected'
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
