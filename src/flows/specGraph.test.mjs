/**
 * specGraph.test.mjs — unit tests for specToGraph / graphToSpec round-trip.
 *
 * Run with:  node --test src/flows/specGraph.test.mjs
 *
 * Uses only Node built-ins (node:test + node:assert).
 * We inline the implementation (avoids JSX / import.meta issues in bare Node).
 */

import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Inline copies of the implementation (kept in sync with specGraph.js)
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

function _branchLabel(whenExpr, index) {
  if (!whenExpr) return `condition_${index}`
  const inner = whenExpr.replace(/^\s*\{\{\s*/, '').replace(/\s*\}\}\s*$/, '').trim()
  return inner.length > 20 ? inner.slice(0, 18) + '…' : inner
}

function _runWhenLabel(expr) {
  if (!expr) return 'if'
  const inner = expr.replace(/^\s*\{\{\s*/, '').replace(/\s*\}\}\s*$/, '').trim()
  return inner.length > 24 ? 'if ' + inner.slice(0, 22) + '…' : 'if ' + inner
}

function deriveCellBadges(task) {
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

function inferredRefs(sql, siblingKeys) {
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

function specToGraph(spec) {
  const tasks = spec?.tasks ?? []
  const siblingKeys = new Set(tasks.map(t => t.key))
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
          expanded: false,
          bodySpec: task.config?.body ?? [],
        },
      }
    }

    if (task.kind === 'branch') {
      return {
        id: task.key,
        type: 'branchNode',
        position: pos,
        data: { task, taskRun: null },
      }
    }

    return { id: task.key, type: 'taskNode', position: pos, data: { task, taskRun: null, cellBadges: deriveCellBadges(task) } }
  })

  const edges = []
  for (const task of tasks) {
    const runWhen = typeof task.config?.run_when === 'string' && task.config.run_when.trim()
      ? task.config.run_when.trim()
      : null
    const condLabel = runWhen ? _runWhenLabel(runWhen) : null

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

    if (task.kind === 'branch') {
      const conditions = task.config?.conditions ?? []
      for (let i = 0; i < conditions.length; i++) {
        const cond = conditions[i]
        const label = _branchLabel(cond.when, i)
        for (const nextKey of (cond.next ?? [])) {
          const edgeId = `${task.key}->branch_cond${i}->${nextKey}`
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

    if (task.kind === 'query') {
      const refs = inferredRefs(task.config?.sql, siblingKeys)
      const explicitNeeds = new Set(task.needs ?? [])
      for (const ref of refs) {
        if (ref === task.key) continue
        if (explicitNeeds.has(ref)) continue
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

function graphToSpec(nodes, edges, meta = {}) {
  const skipEdgeIds = new Set(
    edges
      .filter(e => e.data != null && ('branchCondIndex' in e.data || e.data.inferred))
      .map(e => e.id)
  )

  const needsMap = new Map()
  for (const node of nodes) needsMap.set(node.id, [])
  for (const edge of edges) {
    if (skipEdgeIds.has(edge.id)) continue
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

// A spec with a map node (fan-out over regions).
const MAP_SPEC = {
  version: 1,
  name: 'regional_pipeline',
  params: [],
  tasks: [
    {
      key: 'get_regions',
      kind: 'query',
      needs: [],
      config: { sql: 'SELECT DISTINCT region FROM sales' },
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
      ui: { x: 0, y: 200 },
    },
    {
      key: 'process_each_region',
      kind: 'map',
      needs: ['get_regions'],
      config: {
        item_expr: '{{ inputs.get_regions.rows }}',
        item_var: 'region',
        max_concurrency: 4,
        max_map_size: 1000,
        collect_key: 'transform',
        body: [
          {
            key: 'fetch_data',
            kind: 'query',
            needs: [],
            config: { sql: "SELECT * FROM sales WHERE region = '{{ item.region_code }}'" },
            retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
            ui: { x: 0, y: 0 },
          },
          {
            key: 'transform',
            kind: 'python',
            needs: ['fetch_data'],
            config: { code: 'result = {k: v*2 for k, v in inputs["fetch_data"]["rows"][0].items()}' },
            retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
            ui: { x: 260, y: 0 },
          },
        ],
      },
      retries: 0, retry_backoff_s: 30, timeout_s: 0, cache_ttl_s: 0,
      ui: { x: 320, y: 200 },
    },
    {
      key: 'aggregate',
      kind: 'materialize',
      needs: ['process_each_region'],
      config: { combine_sql: 'SELECT * FROM results' },
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
      ui: { x: 640, y: 200 },
    },
  ],
}

// A spec with a branch node (conditional routing).
const BRANCH_SPEC = {
  version: 1,
  name: 'score_router',
  params: [],
  tasks: [
    {
      key: 'classify',
      kind: 'python',
      needs: [],
      config: { code: 'result = {"label": "high"}' },
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
      ui: { x: 0, y: 200 },
    },
    {
      key: 'route',
      kind: 'branch',
      needs: ['classify'],
      config: {
        conditions: [
          { when: "{{ inputs.classify.label == 'high_value' }}", next: ['enrich'] },
          { when: "{{ inputs.classify.label == 'low_value' }}",  next: ['archive'] },
        ],
        default: ['log_task'],
      },
      retries: 0, retry_backoff_s: 30, timeout_s: 30, cache_ttl_s: 0,
      ui: { x: 320, y: 200 },
    },
    {
      key: 'enrich',
      kind: 'python',
      needs: ['route'],
      config: { code: 'result = {"enriched": True}' },
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
      ui: { x: 640, y: 100 },
    },
    {
      key: 'archive',
      kind: 'python',
      needs: ['route'],
      config: { code: 'result = {"archived": True}' },
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
      ui: { x: 640, y: 300 },
    },
    {
      key: 'log_task',
      kind: 'noop',
      needs: ['route'],
      config: {},
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0,
      ui: { x: 640, y: 500 },
    },
  ],
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Strip ui coords and compare specs for semantic equality
 * (ui is canvas-only; round-trip preserves position but we want to check
 * the routing structure rather than pixel values).
 */
function stripUi(spec) {
  return {
    ...spec,
    tasks: spec.tasks.map(t => {
      const { ui: _ui, ...rest } = t
      return rest
    }),
  }
}

// ---------------------------------------------------------------------------
// Tests — existing (linear + empty)
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

  it('node type is taskNode for regular tasks', () => {
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

// ---------------------------------------------------------------------------
// Tests — map node
// ---------------------------------------------------------------------------

describe('specToGraph — map node', () => {
  it('emits a mapNode type for map tasks', () => {
    const { nodes } = specToGraph(MAP_SPEC)
    const mapNode = nodes.find(n => n.id === 'process_each_region')
    assert.ok(mapNode, 'map node should exist')
    assert.equal(mapNode.type, 'mapNode')
  })

  it('map node starts collapsed (expanded: false)', () => {
    const { nodes } = specToGraph(MAP_SPEC)
    const mapNode = nodes.find(n => n.id === 'process_each_region')
    assert.equal(mapNode.data.expanded, false)
  })

  it('map node data.bodySpec matches config.body', () => {
    const { nodes } = specToGraph(MAP_SPEC)
    const mapNode = nodes.find(n => n.id === 'process_each_region')
    assert.ok(Array.isArray(mapNode.data.bodySpec), 'bodySpec should be an array')
    assert.equal(mapNode.data.bodySpec.length, 2)
    assert.equal(mapNode.data.bodySpec[0].key, 'fetch_data')
    assert.equal(mapNode.data.bodySpec[1].key, 'transform')
  })

  it('map node data.bodySpec body tasks have correct ui coords', () => {
    const { nodes } = specToGraph(MAP_SPEC)
    const mapNode = nodes.find(n => n.id === 'process_each_region')
    // Body task ui coords are stored relative to the map node origin.
    assert.deepEqual(mapNode.data.bodySpec[0].ui, { x: 0, y: 0 })
    assert.deepEqual(mapNode.data.bodySpec[1].ui, { x: 260, y: 0 })
  })

  it('map node has correct needs edges (standard upstream edges)', () => {
    const { edges } = specToGraph(MAP_SPEC)
    // get_regions → process_each_region (standard needs edge)
    assert.ok(edges.some(e => e.source === 'get_regions' && e.target === 'process_each_region'),
      'needs edge from get_regions to map node expected')
  })

  it('emits downstream edge from map node to aggregate', () => {
    const { edges } = specToGraph(MAP_SPEC)
    assert.ok(edges.some(e => e.source === 'process_each_region' && e.target === 'aggregate'),
      'edge from map node to downstream aggregate task expected')
  })

  it('non-map tasks keep taskNode type in mixed spec', () => {
    const { nodes } = specToGraph(MAP_SPEC)
    const get = nodes.find(n => n.id === 'get_regions')
    const agg = nodes.find(n => n.id === 'aggregate')
    assert.equal(get.type, 'taskNode')
    assert.equal(agg.type, 'taskNode')
  })

  it('correct total node count for map spec', () => {
    // MAP_SPEC has 3 top-level tasks (get_regions, process_each_region, aggregate).
    // Body tasks do NOT become top-level nodes.
    const { nodes } = specToGraph(MAP_SPEC)
    assert.equal(nodes.length, 3)
  })
})

describe('graphToSpec — map round-trip', () => {
  it('map round-trip preserves task count', () => {
    const { nodes, edges } = specToGraph(MAP_SPEC)
    const spec = graphToSpec(nodes, edges, {
      version: MAP_SPEC.version,
      name: MAP_SPEC.name,
      params: MAP_SPEC.params,
    })
    assert.equal(spec.tasks.length, 3)
  })

  it('map round-trip preserves kind', () => {
    const { nodes, edges } = specToGraph(MAP_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const mapTask = spec.tasks.find(t => t.key === 'process_each_region')
    assert.equal(mapTask.kind, 'map')
  })

  it('map round-trip preserves config.body verbatim', () => {
    const { nodes, edges } = specToGraph(MAP_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const mapTask = spec.tasks.find(t => t.key === 'process_each_region')
    assert.ok(Array.isArray(mapTask.config.body), 'config.body must be an array')
    assert.equal(mapTask.config.body.length, 2)
    // Body task structure preserved
    assert.equal(mapTask.config.body[0].key, 'fetch_data')
    assert.equal(mapTask.config.body[0].kind, 'query')
    assert.equal(mapTask.config.body[1].key, 'transform')
    assert.equal(mapTask.config.body[1].kind, 'python')
  })

  it('map round-trip preserves config.item_expr', () => {
    const { nodes, edges } = specToGraph(MAP_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const mapTask = spec.tasks.find(t => t.key === 'process_each_region')
    assert.equal(mapTask.config.item_expr, '{{ inputs.get_regions.rows }}')
  })

  it('map round-trip preserves config.collect_key', () => {
    const { nodes, edges } = specToGraph(MAP_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const mapTask = spec.tasks.find(t => t.key === 'process_each_region')
    assert.equal(mapTask.config.collect_key, 'transform')
  })

  it('map round-trip preserves needs', () => {
    const { nodes, edges } = specToGraph(MAP_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const mapTask = spec.tasks.find(t => t.key === 'process_each_region')
    assert.deepEqual(mapTask.needs, ['get_regions'])
  })

  it('map round-trip is idempotent (specToGraph → graphToSpec → specToGraph)', () => {
    // First pass
    const { nodes: n1, edges: e1 } = specToGraph(MAP_SPEC)
    const spec2 = graphToSpec(n1, e1, {
      version: MAP_SPEC.version, name: MAP_SPEC.name, params: MAP_SPEC.params,
    })
    // Second pass
    const { nodes: n2, edges: e2 } = specToGraph(spec2)
    const spec3 = graphToSpec(n2, e2, {
      version: spec2.version, name: spec2.name, params: spec2.params,
    })
    // The round-trip must be stable: spec2 and spec3 should be structurally equal.
    assert.deepEqual(stripUi(spec2), stripUi(spec3))
  })

  it('map body task ui coords survive round-trip', () => {
    const { nodes, edges } = specToGraph(MAP_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const mapTask = spec.tasks.find(t => t.key === 'process_each_region')
    assert.deepEqual(mapTask.config.body[0].ui, { x: 0, y: 0 })
    assert.deepEqual(mapTask.config.body[1].ui, { x: 260, y: 0 })
  })
})

// ---------------------------------------------------------------------------
// Tests — branch node
// ---------------------------------------------------------------------------

describe('specToGraph — branch node', () => {
  it('emits a branchNode type for branch tasks', () => {
    const { nodes } = specToGraph(BRANCH_SPEC)
    const branchNode = nodes.find(n => n.id === 'route')
    assert.ok(branchNode, 'branch node should exist')
    assert.equal(branchNode.type, 'branchNode')
  })

  it('branch node preserves full task in data.task', () => {
    const { nodes } = specToGraph(BRANCH_SPEC)
    const branchNode = nodes.find(n => n.id === 'route')
    assert.equal(branchNode.data.task.kind, 'branch')
    assert.ok(Array.isArray(branchNode.data.task.config.conditions))
    assert.equal(branchNode.data.task.config.conditions.length, 2)
  })

  it('branch node emits incoming needs edges from classify', () => {
    const { edges } = specToGraph(BRANCH_SPEC)
    assert.ok(edges.some(e => e.source === 'classify' && e.target === 'route'),
      'needs edge from classify to route expected')
  })

  it('branch node emits labeled outgoing edges for each condition', () => {
    const { edges } = specToGraph(BRANCH_SPEC)
    // condition_0 → enrich
    const cond0Edge = edges.find(e =>
      e.source === 'route' && e.target === 'enrich' && e.data?.branchCondIndex === 0
    )
    assert.ok(cond0Edge, 'condition 0 edge to enrich expected')
    // condition_1 → archive
    const cond1Edge = edges.find(e =>
      e.source === 'route' && e.target === 'archive' && e.data?.branchCondIndex === 1
    )
    assert.ok(cond1Edge, 'condition 1 edge to archive expected')
  })

  it('branch node emits default edge with branchCondIndex: -1', () => {
    const { edges } = specToGraph(BRANCH_SPEC)
    const defaultEdge = edges.find(e =>
      e.source === 'route' && e.target === 'log_task' && e.data?.branchCondIndex === -1
    )
    assert.ok(defaultEdge, 'default branch edge to log_task expected')
    assert.equal(defaultEdge.label, 'default')
  })

  it('branch condition edges have a label derived from when expression', () => {
    const { edges } = specToGraph(BRANCH_SPEC)
    const cond0Edge = edges.find(e =>
      e.source === 'route' && e.target === 'enrich' && e.data?.branchCondIndex === 0
    )
    // The when expression is "{{ inputs.classify.label == 'high_value' }}"
    // After stripping {{ }}: "inputs.classify.label == 'high_value'" (37 chars)
    // Expected to be truncated to 18 chars + ellipsis
    assert.ok(typeof cond0Edge.label === 'string', 'edge label must be a string')
    assert.ok(cond0Edge.label.length > 0, 'edge label must not be empty')
  })

  it('correct total node count for branch spec (body tasks not expanded)', () => {
    const { nodes } = specToGraph(BRANCH_SPEC)
    // BRANCH_SPEC has 5 top-level tasks.
    assert.equal(nodes.length, 5)
  })

  it('non-branch tasks remain taskNode type in mixed spec', () => {
    const { nodes } = specToGraph(BRANCH_SPEC)
    const classify = nodes.find(n => n.id === 'classify')
    const enrich = nodes.find(n => n.id === 'enrich')
    assert.equal(classify.type, 'taskNode')
    assert.equal(enrich.type, 'taskNode')
  })
})

describe('graphToSpec — branch round-trip', () => {
  it('branch round-trip preserves task count', () => {
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {
      version: BRANCH_SPEC.version,
      name: BRANCH_SPEC.name,
      params: BRANCH_SPEC.params,
    })
    assert.equal(spec.tasks.length, 5)
  })

  it('branch round-trip preserves kind', () => {
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const branchTask = spec.tasks.find(t => t.key === 'route')
    assert.equal(branchTask.kind, 'branch')
  })

  it('branch round-trip preserves config.conditions verbatim', () => {
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const branchTask = spec.tasks.find(t => t.key === 'route')
    assert.ok(Array.isArray(branchTask.config.conditions))
    assert.equal(branchTask.config.conditions.length, 2)
    assert.equal(branchTask.config.conditions[0].next[0], 'enrich')
    assert.equal(branchTask.config.conditions[1].next[0], 'archive')
  })

  it('branch round-trip preserves config.default verbatim', () => {
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const branchTask = spec.tasks.find(t => t.key === 'route')
    assert.deepEqual(branchTask.config.default, ['log_task'])
  })

  it('branch round-trip preserves branch node needs', () => {
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const branchTask = spec.tasks.find(t => t.key === 'route')
    assert.deepEqual(branchTask.needs, ['classify'])
  })

  it('branch round-trip: downstream tasks preserve their needs (branch key)', () => {
    // Branch-labeled edges (visual-only) must NOT contribute duplicate needs.
    // The 'enrich' task has needs: ['route'] from the spec.
    // After round-trip it must still be exactly ['route'], not duplicated.
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const enrich = spec.tasks.find(t => t.key === 'enrich')
    assert.deepEqual(enrich.needs, ['route'])
  })

  it('branch round-trip: archive needs are preserved without duplication', () => {
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const archive = spec.tasks.find(t => t.key === 'archive')
    assert.deepEqual(archive.needs, ['route'])
  })

  it('branch round-trip: log_task needs are preserved without duplication', () => {
    const { nodes, edges } = specToGraph(BRANCH_SPEC)
    const spec = graphToSpec(nodes, edges, {})
    const log = spec.tasks.find(t => t.key === 'log_task')
    assert.deepEqual(log.needs, ['route'])
  })

  it('branch round-trip is idempotent (specToGraph → graphToSpec → specToGraph)', () => {
    // First pass
    const { nodes: n1, edges: e1 } = specToGraph(BRANCH_SPEC)
    const spec2 = graphToSpec(n1, e1, {
      version: BRANCH_SPEC.version, name: BRANCH_SPEC.name, params: BRANCH_SPEC.params,
    })
    // Second pass
    const { nodes: n2, edges: e2 } = specToGraph(spec2)
    const spec3 = graphToSpec(n2, e2, {
      version: spec2.version, name: spec2.name, params: spec2.params,
    })
    assert.deepEqual(stripUi(spec2), stripUi(spec3))
  })
})

// ---------------------------------------------------------------------------
// Tests — _branchLabel helper
// ---------------------------------------------------------------------------

describe('_branchLabel', () => {
  it('returns condition_N when no whenExpr', () => {
    assert.equal(_branchLabel('', 0), 'condition_0')
    assert.equal(_branchLabel(null, 2), 'condition_2')
  })

  it('strips {{ }} template delimiters', () => {
    const label = _branchLabel('{{ inputs.x == 1 }}', 0)
    assert.equal(label, 'inputs.x == 1')
  })

  it('truncates long expressions to 18 chars + ellipsis', () => {
    const long = '{{ inputs.classify.label == "high_value" }}'
    const label = _branchLabel(long, 0)
    assert.ok(label.endsWith('…'), 'truncated label should end with ellipsis')
    assert.ok(label.length <= 19, 'truncated label should be at most 19 chars')
  })

  it('short expressions are not truncated', () => {
    const label = _branchLabel('{{ x > 5 }}', 0)
    assert.equal(label, 'x > 5')
  })
})

// ---------------------------------------------------------------------------
// Tests — inferred SQL dependency edges (SQLMesh-style)
// ---------------------------------------------------------------------------

// Two query cells: `second` selects FROM `first` (a sibling key) with no
// explicit needs. It also references `demo`, a real warehouse table that is NOT
// a sibling key and must be ignored.
const INFERRED_SPEC = {
  version: 1,
  name: 'inferred',
  params: [],
  tasks: [
    {
      key: 'first', kind: 'query', needs: [],
      config: { sql: 'SELECT id, value FROM demo' },
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0, ui: { x: 0, y: 0 },
    },
    {
      key: 'second', kind: 'query', needs: [],
      config: { sql: 'SELECT * FROM first JOIN demo USING (id)' },
      retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0, ui: { x: 260, y: 0 },
    },
  ],
}

describe('inferredRefs', () => {
  it('matches FROM/JOIN identifiers that are sibling keys', () => {
    const refs = inferredRefs('SELECT * FROM first JOIN second', new Set(['first', 'second']))
    assert.deepEqual([...refs].sort(), ['first', 'second'])
  })

  it('ignores non-sibling tables', () => {
    const refs = inferredRefs('SELECT * FROM demo', new Set(['first']))
    assert.deepEqual(refs, [])
  })

  it('returns [] for empty sql or empty sibling set', () => {
    assert.deepEqual(inferredRefs('', new Set(['first'])), [])
    assert.deepEqual(inferredRefs('SELECT * FROM first', new Set()), [])
  })
})

describe('specToGraph — inferred SQL edges', () => {
  it('adds a dashed inferred edge for SELECT * FROM other_cell', () => {
    const { edges } = specToGraph(INFERRED_SPEC)
    const inferred = edges.find(e => e.source === 'first' && e.target === 'second')
    assert.ok(inferred, 'expected an inferred edge from first to second')
    assert.equal(inferred.data?.inferred, true)
    assert.ok(inferred.style?.strokeDasharray, 'inferred edge should be dashed')
  })

  it('non-sibling table (demo) yields no edge', () => {
    const { edges } = specToGraph(INFERRED_SPEC)
    assert.ok(!edges.some(e => e.source === 'demo'), 'demo must not produce an edge')
    assert.ok(!edges.some(e => e.target === 'demo'), 'demo must not produce an edge')
  })

  it('does not duplicate an edge already covered by explicit needs', () => {
    const spec = {
      version: 1, name: 'x', params: [],
      tasks: [
        { key: 'first', kind: 'query', needs: [], config: { sql: 'SELECT 1' } },
        { key: 'second', kind: 'query', needs: ['first'], config: { sql: 'SELECT * FROM first' } },
      ],
    }
    const { edges } = specToGraph(spec)
    const fToS = edges.filter(e => e.source === 'first' && e.target === 'second')
    assert.equal(fToS.length, 1, 'should be exactly one edge, not an explicit + inferred duplicate')
    assert.ok(!fToS[0].data?.inferred, 'the single edge should be the explicit one')
  })
})

describe('graphToSpec — inferred edges excluded from needs', () => {
  it('inferred edge is NOT written into needs on round-trip', () => {
    const { nodes, edges } = specToGraph(INFERRED_SPEC)
    const spec = graphToSpec(nodes, edges, {
      version: INFERRED_SPEC.version, name: INFERRED_SPEC.name, params: INFERRED_SPEC.params,
    })
    const second = spec.tasks.find(t => t.key === 'second')
    assert.deepEqual(second.needs, [], 'inferred dep must not persist into needs')
  })

  it('round-trip is idempotent: edge re-derived from config.sql', () => {
    const { nodes: n1, edges: e1 } = specToGraph(INFERRED_SPEC)
    const spec2 = graphToSpec(n1, e1, {
      version: INFERRED_SPEC.version, name: INFERRED_SPEC.name, params: INFERRED_SPEC.params,
    })
    const { edges: e2 } = specToGraph(spec2)
    const inferred = e2.find(e => e.source === 'first' && e.target === 'second')
    assert.ok(inferred?.data?.inferred, 'inferred edge re-derived after round-trip')
  })
})

// ---------------------------------------------------------------------------
// v4 cell-config: cellBadges derivation + run_when conditional edges
// ---------------------------------------------------------------------------

describe('deriveCellBadges', () => {
  it('returns nulls when no config blocks are set', () => {
    const b = deriveCellBadges({ config: { sql: 'SELECT 1' } })
    assert.equal(b.materialized, null)
    assert.equal(b.forEach, null)
    assert.equal(b.runWhen, null)
  })

  it('materialized view ⇒ no badge (only full/incremental)', () => {
    const b = deriveCellBadges({ config: { materialized: { kind: 'view' } } })
    assert.equal(b.materialized, null)
  })

  it('materialized incremental ⇒ { kind, target }', () => {
    const b = deriveCellBadges({ config: { materialized: { kind: 'incremental', target: 'orders/daily' } } })
    assert.deepEqual(b.materialized, { kind: 'incremental', target: 'orders/daily' })
  })

  it('for_each ⇒ { items, var } with default var', () => {
    const b = deriveCellBadges({ config: { for_each: { items: '{{ inputs.r.rows }}' } } })
    assert.deepEqual(b.forEach, { items: '{{ inputs.r.rows }}', var: 'item' })
  })

  it('empty for_each items ⇒ no badge', () => {
    assert.equal(deriveCellBadges({ config: { for_each: { items: '' } } }).forEach, null)
  })

  it('run_when ⇒ trimmed string; blank ⇒ null', () => {
    assert.equal(deriveCellBadges({ config: { run_when: "  inputs.x == 1 " } }).runWhen, 'inputs.x == 1')
    assert.equal(deriveCellBadges({ config: { run_when: '   ' } }).runWhen, null)
  })
})

const RUN_WHEN_SPEC = {
  version: 1,
  name: 'gated',
  params: [],
  tasks: [
    { key: 'classify', kind: 'python', needs: [], config: { code: 'result = {}' }, retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0, ui: { x: 0, y: 0 } },
    { key: 'act', kind: 'python', needs: ['classify'], config: { code: 'result = {}', run_when: "inputs.classify.label == 'high'" }, retries: 0, retry_backoff_s: 30, timeout_s: 60, cache_ttl_s: 0, ui: { x: 260, y: 0 } },
  ],
}

describe('specToGraph — run_when conditional edges', () => {
  it('node carries cellBadges.runWhen', () => {
    const { nodes } = specToGraph(RUN_WHEN_SPEC)
    const act = nodes.find(n => n.id === 'act')
    assert.equal(act.data.cellBadges.runWhen, "inputs.classify.label == 'high'")
  })

  it('incoming edge to a run_when cell is marked conditional + dashed + labeled', () => {
    const { edges } = specToGraph(RUN_WHEN_SPEC)
    const e = edges.find(ed => ed.source === 'classify' && ed.target === 'act')
    assert.ok(e, 'edge exists')
    assert.equal(e.data?.conditional, true)
    assert.equal(e.style.strokeDasharray, '5 3')
    assert.ok(e.label.startsWith('if '))
  })
})

describe('graphToSpec — run_when conditional edge survives into needs', () => {
  it('conditional edge round-trips into needs (NOT skipped like branch/inferred)', () => {
    const { nodes, edges } = specToGraph(RUN_WHEN_SPEC)
    const spec = graphToSpec(nodes, edges, { version: 1, name: 'gated', params: [] })
    const act = spec.tasks.find(t => t.key === 'act')
    assert.deepEqual(act.needs, ['classify'], 'run_when dep is a real need and must persist')
  })
})
