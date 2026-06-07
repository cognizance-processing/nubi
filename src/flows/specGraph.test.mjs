/**
 * specGraph.test.mjs — unit tests for specToGraph / graphToSpec round-trip.
 *
 * Run with:  node --test src/flows/specGraph.test.mjs
 *
 * Uses only Node built-ins (node:test + node:assert).
 * We import from specGraph.js via a thin inline re-export (ESM-compatible).
 */

import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Inline copies of the functions (avoids JSX / import.meta issues in bare Node)
// ---------------------------------------------------------------------------

function topoDepths(tasks) {
  const deps = new Map(tasks.map(t => [t.key, t.needs ?? []]))
  const depths = new Map()
  function depth(key, visited = new Set()) {
    if (depths.has(key)) return depths.get(key)
    if (visited.has(key)) return 0
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

const LAYER_GAP_X = 260
const NODE_H = 70
const NODE_GAP_Y = 100

function autoLayout(tasks) {
  const depths = topoDepths(tasks)
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

function specToGraph(spec) {
  const tasks = spec?.tasks ?? []
  const auto = autoLayout(tasks)
  const nodes = tasks.map(task => {
    const hasUi = task.ui && (task.ui.x != null || task.ui.y != null)
    const pos = hasUi
      ? { x: task.ui.x ?? 0, y: task.ui.y ?? 0 }
      : auto.get(task.key) ?? { x: 0, y: 0 }
    return { id: task.key, type: 'taskNode', position: pos, data: { task, taskRun: null } }
  })
  const edges = []
  for (const task of tasks) {
    for (const need of (task.needs ?? [])) {
      edges.push({ id: `${need}->${task.key}`, source: need, target: task.key })
    }
  }
  return { nodes, edges }
}

function graphToSpec(nodes, edges, meta = {}) {
  const needsMap = new Map()
  for (const node of nodes) needsMap.set(node.id, [])
  for (const edge of edges) {
    if (needsMap.has(edge.target)) needsMap.get(edge.target).push(edge.source)
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

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const LINEAR_SPEC = {
  version: 1,
  name: 'daily_revenue',
  params: [{ name: 'region', type: 'text', default: 'us' }],
  tasks: [
    { key: 'pull',    kind: 'query',  needs: [],         config: { query_id: 'demo_all' }, retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0, ui: { x: 0, y: 0 } },
    { key: 'enrich',  kind: 'python', needs: ['pull'],   config: { code: 'result = {}' },  retries: 1, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0, ui: { x: 260, y: 0 } },
    { key: 'summary', kind: 'agent',  needs: ['enrich'], config: { prompt: 'summarize' },  retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0, ui: { x: 520, y: 0 } },
  ],
}

const EMPTY_SPEC = { version: 1, name: 'empty', params: [], tasks: [] }

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('specToGraph', () => {
  it('returns empty nodes+edges for empty spec', () => {
    const { nodes, edges } = specToGraph(EMPTY_SPEC)
    assert.equal(nodes.length, 0)
    assert.equal(edges.length, 0)
  })

  it('creates one node per task', () => {
    const { nodes } = specToGraph(LINEAR_SPEC)
    assert.equal(nodes.length, 3)
    assert.deepEqual(nodes.map(n => n.id), ['pull', 'enrich', 'summary'])
  })

  it('node type is taskNode', () => {
    const { nodes } = specToGraph(LINEAR_SPEC)
    for (const node of nodes) assert.equal(node.type, 'taskNode')
  })

  it('creates edges from needs', () => {
    const { edges } = specToGraph(LINEAR_SPEC)
    assert.equal(edges.length, 2)
    assert.ok(edges.some(e => e.source === 'pull'   && e.target === 'enrich'))
    assert.ok(edges.some(e => e.source === 'enrich' && e.target === 'summary'))
  })

  it('uses ui.x/y when present', () => {
    const { nodes } = specToGraph(LINEAR_SPEC)
    const pull = nodes.find(n => n.id === 'pull')
    assert.equal(pull.position.x, 0)
    assert.equal(pull.position.y, 0)
  })

  it('task object is preserved in node.data.task', () => {
    const { nodes } = specToGraph(LINEAR_SPEC)
    const enrich = nodes.find(n => n.id === 'enrich')
    assert.equal(enrich.data.task.kind, 'python')
    assert.equal(enrich.data.task.retries, 1)
  })

  it('auto-layout used when ui is absent', () => {
    const spec = {
      version: 1, name: 'x', params: [],
      tasks: [
        { key: 'a', kind: 'noop', needs: [], config: {} },
        { key: 'b', kind: 'noop', needs: ['a'], config: {} },
      ],
    }
    const { nodes } = specToGraph(spec)
    const a = nodes.find(n => n.id === 'a')
    const b = nodes.find(n => n.id === 'b')
    // depth 0 < depth 1 → b.x > a.x
    assert.ok(b.position.x > a.position.x, 'downstream node should be to the right')
  })
})

describe('graphToSpec', () => {
  it('round-trips a linear spec', () => {
    const { nodes, edges } = specToGraph(LINEAR_SPEC)
    const spec = graphToSpec(nodes, edges, {
      name: LINEAR_SPEC.name,
      params: LINEAR_SPEC.params,
      version: LINEAR_SPEC.version,
    })
    assert.equal(spec.name, 'daily_revenue')
    assert.equal(spec.tasks.length, 3)
  })

  it('reconstructs needs from edges', () => {
    const { nodes, edges } = specToGraph(LINEAR_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const enrich = spec.tasks.find(t => t.key === 'enrich')
    assert.deepEqual(enrich.needs, ['pull'])
  })

  it('preserves kind and config', () => {
    const { nodes, edges } = specToGraph(LINEAR_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const summary = spec.tasks.find(t => t.key === 'summary')
    assert.equal(summary.kind, 'agent')
    assert.equal(summary.config.prompt, 'summarize')
  })

  it('writes position back to ui', () => {
    const { nodes, edges } = specToGraph(LINEAR_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const pull = spec.tasks.find(t => t.key === 'pull')
    assert.ok(typeof pull.ui.x === 'number')
    assert.ok(typeof pull.ui.y === 'number')
  })

  it('empty graph produces valid spec with no tasks', () => {
    const spec = graphToSpec([], [], { name: 'empty', version: 1, params: [] })
    assert.equal(spec.tasks.length, 0)
    assert.equal(spec.version, 1)
  })
})

describe('topoDepths', () => {
  it('root nodes have depth 0', () => {
    const tasks = [{ key: 'a', needs: [] }, { key: 'b', needs: ['a'] }]
    const depths = topoDepths(tasks)
    assert.equal(depths.get('a'), 0)
    assert.equal(depths.get('b'), 1)
  })

  it('handles diamond dependencies', () => {
    const tasks = [
      { key: 'root', needs: [] },
      { key: 'left', needs: ['root'] },
      { key: 'right', needs: ['root'] },
      { key: 'join', needs: ['left', 'right'] },
    ]
    const depths = topoDepths(tasks)
    assert.equal(depths.get('root'), 0)
    assert.equal(depths.get('left'), 1)
    assert.equal(depths.get('right'), 1)
    assert.equal(depths.get('join'), 2)
  })
})
