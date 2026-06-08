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
      },
    }
  })

  const edges = []
  for (const task of tasks) {
    // Standard upstream dependency edges (needs → task).
    for (const need of (task.needs ?? [])) {
      edges.push({
        id: `${need}->${task.key}`,
        source: need,
        target: task.key,
        type: 'smoothstep',
        animated: false,
        style: { strokeWidth: 1.5 },
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
  const branchEdgeIds = new Set(
    edges
      .filter(e => e.data != null && 'branchCondIndex' in e.data)
      .map(e => e.id)
  )

  // Build needs map from non-branch edges only.
  const needsMap = new Map()
  for (const node of nodes) needsMap.set(node.id, [])
  for (const edge of edges) {
    if (branchEdgeIds.has(edge.id)) continue  // skip visual branch edges
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
