/**
 * specGraph.js — pure converters between FlowSpec and React Flow graph.
 *
 * specToGraph(spec) -> { nodes, edges }
 *   Node id = task.key
 *   Edges built from task.needs
 *   Node position from task.ui.{x,y}; falls back to a simple layered layout
 *   (topological depth → horizontal layers, nodes in a layer spread vertically).
 *
 *   map nodes  → type: 'mapNode', data.expanded=false, data.bodySpec=config.body
 *   branch nodes → type: 'branchNode'; outgoing edges are labeled per condition
 *
 * graphToSpec(nodes, edges, meta) -> spec
 *   meta = { version?, name?, params? } — merged into the returned spec.
 *   edges back-fill task.needs; node positions written to task.ui.
 *   config.body (map) and config.conditions (branch) pass through verbatim
 *   from node.data.task.config — the needs/edges for branch routing are
 *   visual only; the authoritative routing lives in config.conditions[i].next.
 */

// ---------------------------------------------------------------------------
// Topological sort helpers (no external deps)
// ---------------------------------------------------------------------------

/**
 * Build a map of key → depth (longest path from root) given a tasks array.
 * @param {Array<{key: string, needs?: string[]}>} tasks
 * @returns {Map<string, number>}
 */
function topoDepths(tasks) {
  const deps = new Map(tasks.map(t => [t.key, t.needs ?? []]))
  const depths = new Map()

  function depth(key, visited = new Set()) {
    if (depths.has(key)) return depths.get(key)
    if (visited.has(key)) return 0 // cycle guard
    visited.add(key)
    const needs = deps.get(key) ?? []
    const d = needs.length === 0
      ? 0
      : 1 + Math.max(...needs.map(n => depth(n, new Set(visited))))
    depths.set(key, d)
    return d
  }

  for (const t of tasks) depth(t.key)
  return depths
}

// ---------------------------------------------------------------------------
// Auto-layout (layered / sugiyama-ish, zero deps)
// ---------------------------------------------------------------------------

const NODE_W = 200
const NODE_H = 70
const LAYER_GAP_X = 260  // horizontal gap between depth columns
const NODE_GAP_Y = 100   // vertical gap between nodes in same column

/**
 * Compute positions for tasks that have no ui coords.
 * @param {Array} tasks
 * @returns {Map<string, {x: number, y: number}>}
 */
function autoLayout(tasks) {
  const depths = topoDepths(tasks)
  // Group by depth
  const byDepth = new Map()
  for (const [key, d] of depths) {
    if (!byDepth.has(d)) byDepth.set(d, [])
    byDepth.get(d).push(key)
  }

  const positions = new Map()
  for (const [d, keys] of byDepth) {
    const x = d * LAYER_GAP_X + 60
    const totalH = keys.length * NODE_H + (keys.length - 1) * (NODE_GAP_Y - NODE_H)
    const startY = -totalH / 2
    keys.forEach((key, i) => {
      positions.set(key, { x, y: startY + i * NODE_GAP_Y + 200 })
    })
  }
  return positions
}

// ---------------------------------------------------------------------------
// specToGraph
// ---------------------------------------------------------------------------

/**
 * Convert a FlowSpec into a React Flow { nodes, edges } pair.
 *
 * - Regular tasks → type: 'taskNode'
 * - map tasks     → type: 'mapNode';    data.expanded=false, data.bodySpec=config.body
 * - branch tasks  → type: 'branchNode'; outgoing edges carry condition labels
 *
 * @param {object} spec  — FlowSpec (version 1)
 * @returns {{ nodes: Array, edges: Array }}
 */
export function specToGraph(spec) {
  const tasks = spec?.tasks ?? []

  // Sibling task keys — used to infer SQL FROM/JOIN dependencies (see below).
  const siblingKeys = new Set(tasks.map(t => t.key))

  // Pre-compute auto-layout positions (used as fallback)
  const auto = autoLayout(tasks)

  const nodes = tasks.map(task => {
    const hasUi = task.ui && (task.ui.x != null || task.ui.y != null)
    const pos = hasUi
      ? { x: task.ui.x ?? 0, y: task.ui.y ?? 0 }
      : auto.get(task.key) ?? { x: 0, y: 0 }

    if (task.kind === 'map') {
      return {
        id: task.key,
        type: 'mapNode',
        position: pos,
        data: {
          task,
          taskRun: null,
          // Collapsed by default; FlowBuilder may expand on user action.
          expanded: false,
          // Preserve the full body sub-spec for drill-in and round-trip.
          bodySpec: task.config?.body ?? [],
        },
      }
    }

    if (task.kind === 'branch') {
      return {
        id: task.key,
        type: 'branchNode',
        position: pos,
        data: {
          task,
          taskRun: null,
        },
      }
    }

    return {
      id: task.key,
      type: 'taskNode',
      position: pos,
      data: {
        task,        // full task object (key, kind, config, needs, retries, etc.)
        taskRun: null, // populated during run view
        // v4 cell-config badges derived from config so TaskNode doesn't re-parse.
        cellBadges: deriveCellBadges(task),
      },
    }
  })

  const edges = []
  for (const task of tasks) {
    // A non-empty run_when gates this cell: render its INCOMING dependency edges
    // as conditional (dashed + truncated-expr label). These are REAL needs —
    // graphToSpec still writes them back into needs (unlike branch/inferred
    // edges); data.conditional is styling-only and NOT added to the skip set.
    const runWhen = typeof task.config?.run_when === 'string' && task.config.run_when.trim()
      ? task.config.run_when.trim()
      : null
    const condLabel = runWhen ? _runWhenLabel(runWhen) : null

    // Standard upstream dependency edges (needs → task).
    for (const need of (task.needs ?? [])) {
      edges.push({
        id: `${need}->${task.key}`,
        source: need,
        target: task.key,
        type: 'smoothstep',
        animated: false,
        ...(runWhen
          ? { label: condLabel, data: { conditional: true }, style: { strokeWidth: 1.5, strokeDasharray: '5 3', stroke: '#f59e0b' } }
          : { style: { strokeWidth: 1.5 } }),
      })
    }

    // Branch nodes: emit labeled outgoing edges to each condition's next targets.
    // These edges are VISUAL ONLY — the authoritative routing lives in
    // config.conditions[i].next and is written back verbatim by graphToSpec.
    if (task.kind === 'branch') {
      const conditions = task.config?.conditions ?? []
      for (let i = 0; i < conditions.length; i++) {
        const cond = conditions[i]
        const label = _branchLabel(cond.when, i)
        for (const nextKey of (cond.next ?? [])) {
          const edgeId = `${task.key}->branch_cond${i}->${nextKey}`
          // Only add if not already emitted via needs (prevents duplicate edges
          // when downstream task correctly lists the branch key in its needs).
          if (!edges.some(e => e.id === edgeId || (e.source === task.key && e.target === nextKey && e.label === label))) {
            edges.push({
              id: edgeId,
              source: task.key,
              target: nextKey,
              type: 'smoothstep',
              animated: false,
              label,
              data: { branchCondIndex: i },
              style: { strokeWidth: 1.5, strokeDasharray: '5 3' },
            })
          }
        }
      }
      // Default branch edge.
      const defaultNext = task.config?.default ?? []
      for (const nextKey of defaultNext) {
        const edgeId = `${task.key}->branch_default->${nextKey}`
        if (!edges.some(e => e.id === edgeId || (e.source === task.key && e.target === nextKey && e.label === 'default'))) {
          edges.push({
            id: edgeId,
            source: task.key,
            target: nextKey,
            type: 'smoothstep',
            animated: false,
            label: 'default',
            data: { branchCondIndex: -1 },
            style: { strokeWidth: 1.5, strokeDasharray: '5 3' },
          })
        }
      }
    }

    // Inferred SQL dependency edges (SQLMesh-style). For a query task with raw
    // SQL, derive dashed edges from any sibling task whose key appears as a
    // FROM/JOIN identifier. These are render-time only: data.inferred marks them
    // so they are NEVER written back into needs (graphToSpec + FlowBuilder skip
    // them, mirroring the visual-only branch edges). They are re-derived from
    // config.sql on every specToGraph, so the round-trip is lossless.
    if (task.kind === 'query') {
      const refs = inferredRefs(task.config?.sql, siblingKeys)
      const explicitNeeds = new Set(task.needs ?? [])
      for (const ref of refs) {
        if (ref === task.key) continue          // no self-edge
        if (explicitNeeds.has(ref)) continue     // already an explicit edge
        if (edges.some(e => e.source === ref && e.target === task.key)) continue
        edges.push({
          id: `${ref}=>${task.key}`,
          source: ref,
          target: task.key,
          type: 'smoothstep',
          animated: false,
          data: { inferred: true },
          style: { strokeWidth: 1.5, strokeDasharray: '4 3', stroke: '#94a3b8' },
        })
      }
    }
  }

  return { nodes, edges }
}

/**
 * Derive a short human-readable label for a branch condition edge.
 * Truncates the `when` expression to 20 chars.
 *
 * @param {string} whenExpr
 * @param {number} index
 * @returns {string}
 */
function _branchLabel(whenExpr, index) {
  if (!whenExpr) return `condition_${index}`
  // Strip outer {{ }} if present, then trim and truncate.
  const inner = whenExpr.replace(/^\s*\{\{\s*/, '').replace(/\s*\}\}\s*$/, '').trim()
  return inner.length > 20 ? inner.slice(0, 18) + '…' : inner
}

/**
 * Truncated label for a run_when conditional edge (strips outer {{ }}).
 * @param {string} expr
 * @returns {string}
 */
function _runWhenLabel(expr) {
  if (!expr) return 'if'
  const inner = expr.replace(/^\s*\{\{\s*/, '').replace(/\s*\}\}\s*$/, '').trim()
  return inner.length > 24 ? 'if ' + inner.slice(0, 22) + '…' : 'if ' + inner
}

// ---------------------------------------------------------------------------
// Cell-config badge derivation (v4 "cells, not kinds")
// ---------------------------------------------------------------------------

/**
 * Derive the canvas badge descriptor for a SQL/Python cell from its config
 * blocks. Returns { materialized, forEach, runWhen } where each is null when
 * the corresponding block is absent/inert. TaskNode renders from this so it
 * never re-parses raw config.
 *
 * @param {{ config?: object }} task
 * @returns {{ materialized: object|null, forEach: object|null, runWhen: string|null }}
 */
export function deriveCellBadges(task) {
  const config = task?.config ?? {}

  const mat = config.materialized
  const materialized = mat && mat.kind && mat.kind !== 'view'
    ? { kind: mat.kind, target: mat.target ?? null }
    : null

  const fe = config.for_each
  const forEach = fe && (fe.items != null && fe.items !== '')
    ? { items: fe.items, var: fe.var ?? 'item' }
    : null

  const runWhen = typeof config.run_when === 'string' && config.run_when.trim()
    ? config.run_when.trim()
    : null

  return { materialized, forEach, runWhen }
}

// ---------------------------------------------------------------------------
// Inferred SQL dependencies (SQLMesh-style)
// ---------------------------------------------------------------------------

/**
 * Naive sibling-ref scan over raw SQL: matches FROM/JOIN <identifier> and keeps
 * only identifiers that are sibling task keys. This is the render-time mirror of
 * the authoritative backend parser (sqlglot in app/flows/deps.py); the backend
 * is canonical for run ordering, this only drives canvas edge rendering.
 *
 * @param {string} sql
 * @param {Set<string>} siblingKeys
 * @returns {string[]} matched sibling keys (deduped)
 */
export function inferredRefs(sql, siblingKeys) {
  if (!sql || !siblingKeys || siblingKeys.size === 0) return []
  const refs = new Set()
  const re = /\b(?:from|join)\s+["'`]?([A-Za-z_][\w]*)["'`]?/gi
  let m
  while ((m = re.exec(sql))) {
    const id = m[1]
    if (siblingKeys.has(id)) refs.add(id)
  }
  return [...refs]
}

// ---------------------------------------------------------------------------
// graphToSpec
// ---------------------------------------------------------------------------

/**
 * Convert a React Flow graph + metadata back into a FlowSpec object.
 *
 * Special handling:
 *
 * - map nodes:    config.body sub-spec is preserved verbatim from
 *   node.data.task.config.body (the UI does not decompose it into child nodes
 *   at the parent graph level; expanded body view is managed inside the node).
 *
 * - branch nodes: config.conditions[].next lists are authoritative for routing.
 *   Canvas branch-labeled edges are VISUAL ONLY — they are not used to derive
 *   needs for downstream tasks.  The branch node's own needs are reconstructed
 *   only from standard (non-branch-labeled) incoming edges.
 *
 * @param {Array} nodes    — React Flow nodes (id, position, data.task)
 * @param {Array} edges    — React Flow edges (source, target)
 * @param {object} meta    — { version?, name?, params? }
 * @returns {object}  FlowSpec
 */
export function graphToSpec(nodes, edges, meta = {}) {
  // Identify branch-labeled outgoing edges (visual-only; excluded from needs).
  // These are edges emitted by specToGraph with data.branchCondIndex defined.
  // Visual-only edges that must NOT be written back into needs:
  //  - branch routing edges (data.branchCondIndex) — authoritative routing lives
  //    in config.conditions[i].next
  //  - inferred SQL dependency edges (data.inferred) — re-derived from config.sql
  //    on every specToGraph, never persisted to needs
  const skipEdgeIds = new Set(
    edges
      .filter(e => e.data != null && ('branchCondIndex' in e.data || e.data.inferred))
      .map(e => e.id)
  )

  // Build needs map from non-visual edges only.
  const needsMap = new Map()
  for (const node of nodes) needsMap.set(node.id, [])
  for (const edge of edges) {
    if (skipEdgeIds.has(edge.id)) continue  // skip visual branch/inferred edges
    if (needsMap.has(edge.target)) {
      needsMap.get(edge.target).push(edge.source)
    }
  }

  const tasks = nodes.map(node => {
    const base = node.data?.task ?? {}
    return {
      key: node.id,
      kind: base.kind ?? 'noop',
      needs: needsMap.get(node.id) ?? [],
      // config passes through verbatim — preserves config.body (map) and
      // config.conditions (branch) without any transformation.
      config: base.config ?? {},
      retries: base.retries ?? 0,
      retry_backoff_s: base.retry_backoff_s ?? 30,
      timeout_s: base.timeout_s ?? 60,
      cache_ttl_s: base.cache_ttl_s ?? 0,
      ui: { x: Math.round(node.position?.x ?? 0), y: Math.round(node.position?.y ?? 0) },
    }
  })

  return {
    version: meta.version ?? 1,
    name: meta.name ?? 'untitled',
    params: meta.params ?? [],
    tasks,
  }
}
