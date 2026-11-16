/**
 * filterGraph.js — reactive cascading-filter dependency graph for dashboards.
 *
 * Builds a static dependency graph from a dashboard spec so that changing one
 * variable (e.g. `country`) knows exactly which downstream filter-option-queries
 * (e.g. the `city` filter's options) and widget-queries must refire — and in
 * what order.
 *
 * Decision: MUTUAL CROSS-FILTER CYCLES ARE AUTO-BROKEN, not rejected (revised —
 * see below). A circular filter dependency (A's options depend on B, B's options
 * depend on A) is a legitimate, common pattern (legacy CA&S-style boards ship
 * 20+ dropdowns that all cross-filter each other's option lists) and has no
 * well-defined single evaluation order — but it doesn't need one, because
 * REFRESHING A FILTER'S OPTIONS DOES NOT CHANGE ITS SELECTED VALUE. Only a
 * user action (or an option-list refresh that invalidates the current
 * selection) changes a variable's value, and neither of those is itself
 * triggered by another filter's option-query running.
 *
 * So the graph distinguishes two edge roles that used to be conflated:
 *   - REFERENCE edges (var → option-query, tracked in `varToOptionQueries`):
 *     "when X changes, refresh W's option list". These may freely form cycles
 *     — they're a fan-out lookup, never topologically sorted.
 *   - CASCADE edges (option-query → var, in `edges`/`order`): "W's options
 *     settled, so the variable it writes is now orderable relative to its
 *     consumers". These MUST stay acyclic — they feed `order`/`dirtySubgraph`.
 *
 * By construction in this module, option-query nodes are the only nodes with
 * an outgoing var edge, so every possible cycle strictly alternates
 * var → option-query → var → option-query → … — i.e. every cycle IS a mutual
 * cross-filter reference. `buildFilterGraph` breaks these automatically: when
 * a cycle remains after Kahn's sort, it drops the CASCADE edge (not the
 * reference edge) of one option-query on the cycle and retries, repeating
 * until the graph is acyclic. Reference edges are never touched, so cascading
 * option refresh (`varToOptionQueries`) still works for every filter — only
 * the "this filter's value should be treated as changed too" propagation is
 * dropped for the filters that would otherwise deadlock the ordering.
 * Broken cascade edges are recorded in `meshBrokenOptIds` (Set of `opt:<id>`)
 * for callers that want to know which filters are mesh members.
 *
 * If a cycle somehow remains after every option-query cascade edge on it has
 * been tried (shouldn't happen given the node/edge model above, but the loop
 * is capped rather than infinite), `buildFilterGraph` throws
 * `FilterGraphCycleError` as a last resort — so a genuinely unresolvable graph
 * still fails loudly instead of silently producing a bad order.
 *
 * Node kinds
 * ----------
 *   { kind: 'variable',     id: 'var:<name>',       name }
 *   { kind: 'option-query', id: 'opt:<widgetId>',   widgetId, writesVar }
 *   { kind: 'widget-query', id: 'wq:<widgetId>',    widgetId }
 *
 * Edges (directed, dependency → dependent / "fires after")
 * -------------------------------------------------------
 *   variable(X) ──▶ option-query(W)   when W's option-query reads X
 *                                      (via options_params {ref:'X'} or {{vars.X}})
 *   variable(X) ──▶ widget-query(W)   when W's params read X (via {ref:'X'})
 *   option-query(W) ──▶ variable(V)   when filter W writes target_var V
 *
 * The variable→option-query→variable chain is what produces a *cascade*:
 * country (var) → city-options (option-query) → city (var) → ... .
 *
 * Pure module — no React. The store layer (VariableStore.jsx) consumes the graph.
 */

/** Error thrown when a dependency cycle is detected at graph build. */
export class FilterGraphCycleError extends Error {
  cycle: string[]

  /** @param cycle ordered node ids forming the cycle (closed loop). */
  constructor(cycle: string[]) {
    const pretty = (cycle || []).map(prettyNodeId).join(' → ')
    super(`Filter dependency cycle detected (rejected): ${pretty}`)
    this.name = 'FilterGraphCycleError'
    this.cycle = cycle || []
  }
}

const VAR = (name) => `var:${name}`
const OPT = (widgetId) => `opt:${widgetId}`
const WQ = (widgetId) => `wq:${widgetId}`

/** Human-readable rendering of a node id for error messages. */
export function prettyNodeId(id) {
  if (typeof id !== 'string') return String(id)
  if (id.startsWith('var:')) return `var '${id.slice(4)}'`
  if (id.startsWith('opt:')) return `options-query of widget '${id.slice(4)}'`
  if (id.startsWith('wq:')) return `query of widget '${id.slice(3)}'`
  return id
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

// Match {{vars.NAME}} / {{ vars.NAME }} occurrences inside a string. The variable
// name token mirrors the rest of the spec (identifier-ish: letters, digits, _, -).
const VARS_TEMPLATE_RE = /\{\{\s*vars\.([A-Za-z0-9_-]+)\s*\}\}/g

/**
 * Collect every variable name a value depends on. Recurses through arrays/objects
 * so nested `options_params` shapes are covered. Two ref forms are recognised:
 *   - `{ ref: 'name', ... }`              → depends on `name`
 *   - any string containing `{{vars.x}}`  → depends on `x` (one or many)
 *
 * @param {unknown} value
 * @param {Set<string>} out  accumulating set of variable names
 */
function collectVarRefs(value, out) {
  if (value == null) return
  if (typeof value === 'string') {
    let m
    VARS_TEMPLATE_RE.lastIndex = 0
    while ((m = VARS_TEMPLATE_RE.exec(value)) !== null) out.add(m[1])
    return
  }
  if (Array.isArray(value)) {
    for (const item of value) collectVarRefs(item, out)
    return
  }
  if (isPlainObject(value)) {
    // A {ref:'x'} marker contributes a dependency on x. The `input:true` search
    // marker (live search text) is NOT a variable dependency.
    if (typeof value.ref === 'string' && value.ref) out.add(value.ref)
    for (const v of Object.values(value)) collectVarRefs(v, out)
  }
}

/**
 * Does this widget actually have an option-query that can refire?
 * (Mirrors useFilterOptions: an options_query_id or a search_query_id.)
 */
function hasOptionQuery(widget) {
  const p = isPlainObject(widget?.props) ? widget.props : {}
  return Boolean(
    widget?.options_query_id || p.options_query_id ||
    widget?.search_query_id || p.search_query_id,
  )
}

/** Pull the options_params object off a widget (props.options_params is canonical). */
function optionsParamsOf(widget) {
  const p = isPlainObject(widget?.props) ? widget.props : {}
  return p.options_params ?? widget?.options_params
}

/**
 * Build the static filter dependency graph from a dashboard spec.
 *
 * @param {object} spec  dashboard spec ({ variables?: [...], widgets?: [...] })
 * @returns {{
 *   nodes: Map<string, {kind:string,id:string,name?:string,widgetId?:string,writesVar?:string}>,
 *   edges: Map<string, Set<string>>,        // adjacency: node id → dependent node ids
 *   varToOptionQueries: Map<string,string[]>, // var name → option-query node ids that read it
 *   order: string[],                          // a valid full topological order
 *   meshBrokenOptIds: Set<string>,            // opt:<widgetId> nodes whose cascade edge
 *                                              // (option-query → var) was dropped to break a
 *                                              // mutual cross-filter cycle. Their reference
 *                                              // edges (varToOptionQueries) are untouched — the
 *                                              // filter's options still refresh normally, it just
 *                                              // no longer implies its VALUE changed for ordering
 *                                              // purposes. Empty for graphs with no cross-filter
 *                                              // mesh.
 * }}
 * @throws {FilterGraphCycleError} only if a cycle survives auto-breaking — see module docstring.
 */
export function buildFilterGraph(spec) {
  const nodes = new Map()
  const edges = new Map() // id → Set<id> (dependency points at its dependents)

  const ensureNode = (node) => {
    if (!nodes.has(node.id)) nodes.set(node.id, node)
    if (!edges.has(node.id)) edges.set(node.id, new Set())
    return nodes.get(node.id)
  }
  const addEdge = (fromId, toId) => {
    if (fromId === toId) return
    ensureEdgeSet(fromId).add(toId)
    if (!edges.has(toId)) edges.set(toId, new Set())
  }
  const ensureEdgeSet = (id) => {
    let s = edges.get(id)
    if (!s) { s = new Set(); edges.set(id, s) }
    return s
  }

  const variables = Array.isArray(spec?.variables) ? spec.variables : []
  const widgets = Array.isArray(spec?.widgets) ? spec.widgets : []

  // 1. Variable nodes. Declared variables + any var that a widget writes/reads
  //    (so an undeclared-but-referenced var still participates rather than
  //    silently dropping a cascade edge).
  const declaredVarNames = new Set()
  for (const v of variables) {
    if (v && typeof v.name === 'string' && v.name) declaredVarNames.add(v.name)
  }
  const ensureVar = (name) => ensureNode({ kind: 'variable', id: VAR(name), name })
  for (const name of declaredVarNames) ensureVar(name)

  const varToOptionQueries = new Map()
  const addVarOpt = (varName, optId) => {
    let arr = varToOptionQueries.get(varName)
    if (!arr) { arr = []; varToOptionQueries.set(varName, arr) }
    if (!arr.includes(optId)) arr.push(optId)
  }

  // 2. Per-widget nodes + edges.
  for (const w of widgets) {
    if (!isPlainObject(w) || typeof w.id !== 'string' || !w.id) continue

    // 2a. Filter widgets write a target_var and may own an option-query.
    if (w.type === 'filter' && typeof w.target_var === 'string' && w.target_var) {
      ensureVar(w.target_var)

      if (hasOptionQuery(w)) {
        const optId = OPT(w.id)
        ensureNode({ kind: 'option-query', id: optId, widgetId: w.id, writesVar: w.target_var })

        // option-query result feeds the variable it populates options for.
        // (Refiring the city options happens BEFORE city's value is usable.)
        addEdge(optId, VAR(w.target_var))
        ensureEdgeSet(VAR(w.target_var)) // make sure var node exists in edge map

        // Edges from any variable the option-query reads → this option-query.
        const refs = new Set()
        collectVarRefs(optionsParamsOf(w), refs)
        for (const refName of refs) {
          if (refName === w.target_var) continue // self-population is not a cascade edge
          ensureVar(refName)
          addEdge(VAR(refName), optId)
          addVarOpt(refName, optId)
        }
      }
    }

    // 2b. Any widget with a data query reads variables via params {ref}.
    //     These are terminal (widget-query) sinks in the cascade.
    if (w.params != null && isPlainObject(w.params)) {
      const refs = new Set()
      collectVarRefs(w.params, refs)
      if (refs.size > 0) {
        const wqId = WQ(w.id)
        ensureNode({ kind: 'widget-query', id: wqId, widgetId: w.id })
        for (const refName of refs) {
          ensureVar(refName)
          addEdge(VAR(refName), wqId)
        }
      }
    }
  }

  // 3. Cycle resolution (Kahn + auto-break). Mutual cross-filter references
  //    are expected (see module docstring) and are broken automatically by
  //    dropping cascade edges, not rejected outright.
  const { order, meshBrokenOptIds } = resolveGraphOrder(nodes, edges)

  return { nodes, edges, varToOptionQueries, order, meshBrokenOptIds }
}

/**
 * Kahn topological sort attempt. Never throws — reports success/failure so
 * the caller can break a cycle and retry.
 *
 * @returns {{ok:true, order:string[]} | {ok:false, cycle:string[]}}
 */
function tryTopoSort(nodes, edges) {
  const indeg = new Map()
  for (const id of nodes.keys()) indeg.set(id, 0)
  for (const id of edges.keys()) if (!indeg.has(id)) indeg.set(id, 0)
  for (const [, deps] of edges) {
    for (const to of deps) indeg.set(to, (indeg.get(to) ?? 0) + 1)
  }

  // Stable queue (sorted) → deterministic order for tests.
  const queue = [...indeg.keys()].filter((id) => indeg.get(id) === 0).sort()
  const order = []
  while (queue.length) {
    const id = queue.shift()
    order.push(id)
    const deps = edges.get(id)
    if (!deps) continue
    const unlocked = []
    for (const to of deps) {
      const d = indeg.get(to) - 1
      indeg.set(to, d)
      if (d === 0) unlocked.push(to)
    }
    if (unlocked.length) {
      // Sort only this freshly-unlocked batch (stable, lexicographic) and append.
      // Previously the WHOLE queue was re-sorted after every unlock — O(n² log n)
      // for an n-node graph. Sorting each batch once keeps output deterministic
      // (a valid, stable topological order) at O(n log n) overall. The exact
      // ordering is batch-grouped rather than a global priority queue, but it is
      // still deterministic — which is all `order` (consumed in topological /
      // firing order by dirtySubgraph) requires.
      unlocked.sort()
      queue.push(...unlocked)
    }
  }

  if (order.length !== indeg.size) {
    // Cycle remains: find one concrete loop among nodes with indeg > 0.
    const stuck = new Set([...indeg.keys()].filter((id) => indeg.get(id) > 0))
    const cycle = findOneCycle(stuck, edges)
    return { ok: false, cycle }
  }
  return { ok: true, order }
}

/**
 * Resolve a full firing order, auto-breaking mutual cross-filter cycles.
 *
 * Every cycle in this graph strictly alternates var → option-query → var
 * (option-query nodes are the only ones with an outgoing var edge — see
 * module docstring), so any remaining cycle can always be broken by dropping
 * one option-query's CASCADE edge (its `writesVar` edge, not its reference
 * edges). Repeats until acyclic; each break removes exactly one edge, so this
 * terminates in at most `nodes.size` iterations. Mutates `edges` in place.
 *
 * @returns {{order:string[], meshBrokenOptIds:Set<string>}}
 * @throws {FilterGraphCycleError} only if a cycle survives with no
 *   option-query node to break (not reachable via the public API today, but
 *   kept as a loud failure instead of an infinite loop / silent bad order).
 */
function resolveGraphOrder(nodes, edges) {
  const meshBrokenOptIds = new Set()
  const maxIterations = nodes.size + 5

  for (let i = 0; i < maxIterations; i++) {
    const result = tryTopoSort(nodes, edges)
    if (result.ok) return { order: result.order, meshBrokenOptIds }

    const optIdOnCycle = result.cycle.find((id) => id.startsWith('opt:'))
    const targetVar = optIdOnCycle ? nodes.get(optIdOnCycle)?.writesVar : undefined
    const removed = optIdOnCycle && targetVar
      ? edges.get(optIdOnCycle)?.delete(VAR(targetVar))
      : false

    if (!removed) throw new FilterGraphCycleError(result.cycle)
    meshBrokenOptIds.add(optIdOnCycle)
  }

  throw new FilterGraphCycleError(['<cross-filter mesh did not converge>'])
}

/** DFS over the stuck subgraph to extract one concrete cycle (closed loop). */
function findOneCycle(stuck, edges) {
  const onStack = new Set()
  const visited = new Set()
  const stack = []

  const dfs = (id) => {
    visited.add(id)
    onStack.add(id)
    stack.push(id)
    for (const to of edges.get(id) ?? []) {
      if (!stuck.has(to)) continue
      if (onStack.has(to)) {
        // Found a back-edge: slice the loop from `to` to current, close it.
        const start = stack.indexOf(to)
        return [...stack.slice(start), to]
      }
      if (!visited.has(to)) {
        const found = dfs(to)
        if (found) return found
      }
    }
    onStack.delete(id)
    stack.pop()
    return null
  }

  for (const id of [...stuck].sort()) {
    if (!visited.has(id)) {
      const found = dfs(id)
      if (found) return found
    }
  }
  return [...stuck] // fallback — shouldn't happen, but never return empty
}

/**
 * Given a built graph and a variable that just changed, return the downstream
 * nodes that must refire, in valid topological (firing) order.
 *
 * Only nodes reachable from var:<changedVar> are included; the changed variable
 * node itself is excluded (its value is already set). Option-query nodes in the
 * result are the filter-option-queries to mark stale + refire; widget-query
 * nodes are the data widgets to re-run once their inputs settle.
 *
 * @param {ReturnType<typeof buildFilterGraph>} graph
 * @param {string} changedVar
 * @returns {Array<{id:string,kind:string,widgetId?:string,name?:string,writesVar?:string}>}
 */
export function dirtySubgraph(graph, changedVar) {
  if (!graph || !changedVar) return []
  const startId = VAR(changedVar)
  if (!graph.nodes.has(startId) && !graph.edges.has(startId)) return []

  // BFS/DFS reachability from the changed var.
  const reachable = new Set()
  const stack = [startId]
  while (stack.length) {
    const id = stack.pop()
    for (const to of graph.edges.get(id) ?? []) {
      if (!reachable.has(to)) { reachable.add(to); stack.push(to) }
    }
  }
  reachable.delete(startId)

  // Emit in the graph's global topological order (a valid firing order, and a
  // valid sub-order for any subset of it). Filters refetch options before the
  // variables they write and before downstream widget-queries consume them.
  return graph.order
    .filter((id) => reachable.has(id))
    .map((id) => graph.nodes.get(id))
    .filter(Boolean)
}

/**
 * Convenience: from a dirty-subgraph result, the widget ids whose option-queries
 * must refire (cascading option refresh). Order-preserving + de-duplicated.
 *
 * @param {ReturnType<typeof dirtySubgraph>} dirtyNodes
 * @returns {string[]}
 */
export function staleOptionWidgetIds(dirtyNodes) {
  const out = []
  for (const n of dirtyNodes || []) {
    if (n && n.kind === 'option-query' && n.widgetId && !out.includes(n.widgetId)) {
      out.push(n.widgetId)
    }
  }
  return out
}
