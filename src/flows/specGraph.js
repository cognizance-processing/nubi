/**
 * specGraph.js — pure converters between FlowSpec and React Flow graph.
 *
 * specToGraph(spec) -> { nodes, edges }
 *   Node id = task.key
 *   Edges built from task.needs
 *   Node position from task.ui.{x,y}; falls back to a simple layered layout
 *   (topological depth → horizontal layers, nodes in a layer spread vertically).
 *
 * graphToSpec(nodes, edges, meta) -> spec
 *   meta = { version?, name?, params? } — merged into the returned spec.
 *   edges back-fill task.needs; node positions written to task.ui.
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
  }

  return { nodes, edges }
}

// ---------------------------------------------------------------------------
// graphToSpec
// ---------------------------------------------------------------------------

/**
 * Convert a React Flow graph + metadata back into a FlowSpec object.
 *
 * @param {Array} nodes    — React Flow nodes (id, position, data.task)
 * @param {Array} edges    — React Flow edges (source, target)
 * @param {object} meta    — { version?, name?, params? }
 * @returns {object}  FlowSpec
 */
export function graphToSpec(nodes, edges, meta = {}) {
  // Build needs map from edges
  const needsMap = new Map()
  for (const node of nodes) needsMap.set(node.id, [])
  for (const edge of edges) {
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
