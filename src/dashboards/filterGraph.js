/**
 * filterGraph.js — reactive cascading-filter dependency graph for dashboards.
 *
 * MANAGED_LAKEHOUSE.md §W4-G. Builds a static dependency graph from a dashboard
 * spec so that changing one variable (e.g. `country`) knows exactly which
 * downstream filter-option-queries (e.g. the `city` filter's options) and
 * widget-queries must refire — and in what order.
 *
 * Decision: CYCLES ARE REJECTED at graph-build time (not auto-broken). A circular
 * filter dependency (A's options depend on B, B's options depend on A) has no
 * well-defined evaluation order, so `buildFilterGraph` throws a `FilterGraphCycleError`
 * naming the cycle.
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
  /** @param {string[]} cycle ordered node ids forming the cycle (closed loop). */
  constructor(cycle) {
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
 * }}
 * @throws {FilterGraphCycleError} if the dependency graph contains a cycle.
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

  // 3. Cycle detection (Kahn). Reject — do NOT auto-break.
  const order = topoSortOrThrow(nodes, edges)

  return { nodes, edges, varToOptionQueries, order }
}

/**
 * Kahn topological sort. Returns a full order on success; on a remaining cycle
 * it locates one concrete cycle (DFS over the unresolved subgraph) and throws
 * a FilterGraphCycleError naming it.
 */
function topoSortOrThrow(nodes, edges) {
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
    throw new FilterGraphCycleError(cycle)
  }
  return order
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
