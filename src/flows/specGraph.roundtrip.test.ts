/**
 * specGraph.roundtrip.test.ts — a canvas save (specToGraph → graphToSpec) must
 * not drop spec fields the canvas doesn't own. Imports the REAL module (the
 * older specGraph.test.ts tests an inlined copy). Run: npm run test:dash
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { specToGraph, graphToSpec } from './specGraph.js'

const spec = {
  version: 1,
  name: 'nightly',
  params: [{ name: 'day', type: 'date' }],
  view: 'notebook',
  runtime_config: { auto_rebuild_downstream: true },
  tasks: [
    { key: 'pull', kind: 'query', needs: [], config: { sql: 'select 1' }, ui: { x: 10, y: 20 },
      cell_type: 'sql', execution_mode: 'durable', freshness_sla_s: 3600, cpu_cores: 2, mem_mb: 512, stochastic: true },
    { key: 'load', kind: 'noop', needs: ['pull'], config: {}, ui: { x: 10, y: 120 } },
  ],
}

test('top-level keys outside the canvas survive a canvas save', () => {
  const { nodes, edges } = specToGraph(spec)
  const { tasks: _t, ...meta } = spec
  const out = graphToSpec(nodes, edges, meta)
  assert.equal(out.view, 'notebook')
  assert.deepEqual(out.runtime_config, { auto_rebuild_downstream: true })
  assert.deepEqual(out.params, spec.params)
})

test('per-task notebook fields survive a canvas save', () => {
  const { nodes, edges } = specToGraph(spec)
  const out = graphToSpec(nodes, edges, { name: spec.name })
  const pull = out.tasks.find((t) => t.key === 'pull')
  for (const k of ['cell_type', 'execution_mode', 'freshness_sla_s', 'cpu_cores', 'mem_mb', 'stochastic']) {
    assert.deepEqual(pull[k], spec.tasks[0][k], k)
  }
  assert.deepEqual(out.tasks.find((t) => t.key === 'load').needs, ['pull'])
})
