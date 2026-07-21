/**
 * filterGraph.test.mjs — Unit tests for the reactive cascading-filter
 * dependency graph (buildFilterGraph / dirtySubgraph / staleOptionWidgetIds).
 *
 * Pure module — no React, no JSDOM. Runs with bare `node --test`.
 *
 *   node --test src/dashboards/filterGraph.test.mjs
 *   # or via the project script:
 *   npm run test:dash
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  buildFilterGraph,
  dirtySubgraph,
  staleOptionWidgetIds,
  prettyNodeId,
  FilterGraphCycleError,
} from './filterGraph.js'

// ---------------------------------------------------------------------------
// prettyNodeId
// ---------------------------------------------------------------------------

test('prettyNodeId renders each node kind', () => {
  assert.equal(prettyNodeId('var:country'), "var 'country'")
  assert.equal(prettyNodeId('opt:cityFilter'), "options-query of widget 'cityFilter'")
  assert.equal(prettyNodeId('wq:revenueKpi'), "query of widget 'revenueKpi'")
  assert.equal(prettyNodeId('unknown'), 'unknown')
  assert.equal(prettyNodeId(42), '42')
})

// ---------------------------------------------------------------------------
// buildFilterGraph — empty / malformed specs
// ---------------------------------------------------------------------------

test('empty spec produces an empty-but-valid graph', () => {
  const g = buildFilterGraph({})
  assert.equal(g.nodes.size, 0)
  assert.deepEqual(g.order, [])
  assert.equal(g.varToOptionQueries.size, 0)
})

test('null spec is tolerated (treated as empty)', () => {
  const g = buildFilterGraph(null)
  assert.equal(g.nodes.size, 0)
})

test('declared variables become variable nodes', () => {
  const g = buildFilterGraph({
    variables: [{ name: 'country', type: 'string' }, { name: 'city', type: 'string' }],
  })
  assert.ok(g.nodes.has('var:country'))
  assert.ok(g.nodes.has('var:city'))
  assert.equal(g.nodes.get('var:country').kind, 'variable')
})

test('widgets without ids are skipped', () => {
  const g = buildFilterGraph({
    widgets: [{ type: 'filter', target_var: 'x' }, { id: '', type: 'kpi' }],
  })
  // No widget node created (no valid id); only the var from the filter target.
  assert.equal([...g.nodes.keys()].some((id) => id.startsWith('opt:')), false)
})

// ---------------------------------------------------------------------------
// buildFilterGraph — widget-query edges (params {ref})
// ---------------------------------------------------------------------------

test('widget params {ref} create var → widget-query edges', () => {
  const g = buildFilterGraph({
    variables: [{ name: 'country', type: 'string' }],
    widgets: [
      { id: 'kpi1', type: 'kpi', params: { c: { ref: 'country' } } },
    ],
  })
  assert.ok(g.nodes.has('wq:kpi1'))
  assert.equal(g.nodes.get('wq:kpi1').kind, 'widget-query')
  assert.ok(g.edges.get('var:country').has('wq:kpi1'))
})

test('widget with literal-only params creates no widget-query node', () => {
  const g = buildFilterGraph({
    widgets: [{ id: 'kpi1', type: 'kpi', params: { limit: 10 } }],
  })
  assert.equal(g.nodes.has('wq:kpi1'), false)
})

test('{{vars.x}} template strings are recognised as dependencies', () => {
  const g = buildFilterGraph({
    widgets: [{ id: 'kpi1', type: 'kpi', params: { sql: 'where c = {{vars.country}}' } }],
  })
  assert.ok(g.nodes.has('wq:kpi1'))
  assert.ok(g.edges.get('var:country').has('wq:kpi1'))
})

// ---------------------------------------------------------------------------
// buildFilterGraph — option-query / cascade edges
// ---------------------------------------------------------------------------

function cascadeSpec() {
  // country (filter) → city options depend on country → city (filter) →
  // revenue kpi depends on city.
  return {
    variables: [
      { name: 'country', type: 'string' },
      { name: 'city', type: 'string' },
    ],
    widgets: [
      {
        id: 'countryFilter',
        type: 'filter',
        target_var: 'country',
        props: { options_query_id: 'q_countries' },
      },
      {
        id: 'cityFilter',
        type: 'filter',
        target_var: 'city',
        props: {
          options_query_id: 'q_cities',
          options_params: { country: { ref: 'country' } },
        },
      },
      {
        id: 'revenueKpi',
        type: 'kpi',
        params: { city: { ref: 'city' } },
      },
    ],
  }
}

test('filter with option-query produces opt node + opt → var edge', () => {
  const g = buildFilterGraph(cascadeSpec())
  assert.ok(g.nodes.has('opt:countryFilter'))
  assert.ok(g.edges.get('opt:countryFilter').has('var:country'))
  assert.equal(g.nodes.get('opt:countryFilter').writesVar, 'country')
})

test('option-query reading another var creates the cascade edge var → opt', () => {
  const g = buildFilterGraph(cascadeSpec())
  // country drives the city options-query
  assert.ok(g.edges.get('var:country').has('opt:cityFilter'))
  assert.deepEqual(g.varToOptionQueries.get('country'), ['opt:cityFilter'])
})

test('filter without an option-query produces no opt node but still ensures var', () => {
  const g = buildFilterGraph({
    widgets: [{ id: 'f', type: 'filter', target_var: 'status' }],
  })
  assert.equal(g.nodes.has('opt:f'), false)
  assert.ok(g.nodes.has('var:status'))
})

test('self-population (options read own target_var) is not a cascade edge', () => {
  const g = buildFilterGraph({
    widgets: [
      {
        id: 'f',
        type: 'filter',
        target_var: 'country',
        props: { options_query_id: 'q', options_params: { c: { ref: 'country' } } },
      },
    ],
  })
  // var:country → opt:f would be a self-feeding loop; it must be skipped.
  assert.equal(g.edges.get('var:country').has('opt:f'), false)
})

// ---------------------------------------------------------------------------
// buildFilterGraph — topological order
// ---------------------------------------------------------------------------

test('topological order places country before city option-query before city var', () => {
  const g = buildFilterGraph(cascadeSpec())
  const idx = (id) => g.order.indexOf(id)
  assert.ok(idx('var:country') < idx('opt:cityFilter'))
  assert.ok(idx('opt:cityFilter') < idx('var:city'))
  assert.ok(idx('var:city') < idx('wq:revenueKpi'))
})

// ---------------------------------------------------------------------------
// buildFilterGraph — mutual cross-filter cycles are auto-broken, not rejected
// ---------------------------------------------------------------------------

test('a mutual cross-filter cycle builds without throwing (auto-broken)', () => {
  // A's options depend on B, B's options depend on A — legacy CA&S-style
  // cross-filtering. This used to throw FilterGraphCycleError; it must now
  // resolve, because refreshing options is not the same as changing a value.
  const spec = {
    widgets: [
      {
        id: 'A',
        type: 'filter',
        target_var: 'a',
        props: { options_query_id: 'qa', options_params: { x: { ref: 'b' } } },
      },
      {
        id: 'B',
        type: 'filter',
        target_var: 'b',
        props: { options_query_id: 'qb', options_params: { x: { ref: 'a' } } },
      },
    ],
  }
  const g = buildFilterGraph(spec)
  assert.equal(g.order.length, g.nodes.size)

  // Reference edges (option-refresh fan-out) survive for BOTH directions —
  // changing either filter still refreshes the other's option list.
  assert.deepEqual(g.varToOptionQueries.get('a'), ['opt:B'])
  assert.deepEqual(g.varToOptionQueries.get('b'), ['opt:A'])

  // Exactly one of the two cascade edges (opt → var) had to be dropped to
  // break the cycle; the other survives untouched.
  assert.equal(g.meshBrokenOptIds.size, 1)
  const brokenId = [...g.meshBrokenOptIds][0]
  assert.ok(brokenId === 'opt:A' || brokenId === 'opt:B')
  const survivorId = brokenId === 'opt:A' ? 'opt:B' : 'opt:A'
  const survivorVar = survivorId === 'opt:A' ? 'var:a' : 'var:b'
  assert.ok(g.edges.get(survivorId).has(survivorVar))
})

test('a real 23-filter cross-filter mesh (CA&S-shaped) resolves without throwing', () => {
  // Every filter's option-query reads every OTHER filter's var — the actual
  // shape found in legacy CA&S DIGITAL boards (23 BasicAutoCompleteFilter
  // dropdowns, each guarded on ~16-18 sibling vars). One big SCC.
  const names = [
    'Buying_Group', 'Sub_Buying_Group', 'Brand', 'Sub_Channel', 'Town',
    'Storage_Type', 'Supplier', 'Province', 'Channel', 'Sub_Brand',
    'Division', 'Region', 'Country', 'Store_Classification',
    'Product_Category', 'Product_Sub_Category', 'OPCO2', 'Product',
    'Currency', 'Exchange_Rate', 'Month_Year',
  ]
  const widgets = names.map((name) => ({
    id: `f_${name}`,
    type: 'filter',
    target_var: name,
    props: {
      options_query_id: `q_${name}`,
      options_params: Object.fromEntries(
        names.filter((n) => n !== name).map((n) => [n, { ref: n }]),
      ),
    },
  }))

  const g = buildFilterGraph({ widgets })
  assert.equal(g.order.length, g.nodes.size)
  // Full mesh: only the last-resolved filter keeps its cascade edge.
  assert.equal(g.meshBrokenOptIds.size, names.length - 1)
  // Every filter's reference edges are intact regardless of mesh-breaking —
  // changing any one still refreshes every sibling's options.
  for (const name of names) {
    const opts = g.varToOptionQueries.get(name) ?? []
    assert.equal(opts.length, names.length - 1)
  }
})

test('a spec with no cross-filter references produces an empty meshBrokenOptIds', () => {
  // Sanity check that mesh-breaking is a no-op off the happy path: a
  // trivial, non-cyclic spec must not report any broken cascade edges.
  const g = buildFilterGraph({ widgets: [{ id: 'f', type: 'filter', target_var: 'x' }] })
  assert.equal(g.meshBrokenOptIds.size, 0)
})

test('the linear country → city cascade is unaffected by mesh-breaking', () => {
  // Regression guard: mesh-breaking must only ever engage when a cycle is
  // actually detected. A non-cyclic cascade graph keeps every cascade edge.
  const g = buildFilterGraph(cascadeSpec())
  assert.equal(g.meshBrokenOptIds.size, 0)
  assert.ok(g.edges.get('opt:cityFilter').has('var:city'))
})

// ---------------------------------------------------------------------------
// dirtySubgraph
// ---------------------------------------------------------------------------

test('changing country marks city options + downstream widgets dirty in firing order', () => {
  const g = buildFilterGraph(cascadeSpec())
  const dirty = dirtySubgraph(g, 'country')
  const ids = dirty.map((n) => n.id)

  // The changed var itself is excluded.
  assert.equal(ids.includes('var:country'), false)
  // city options refire, city var settles, revenue kpi refires.
  assert.ok(ids.includes('opt:cityFilter'))
  assert.ok(ids.includes('var:city'))
  assert.ok(ids.includes('wq:revenueKpi'))
  // Firing order: options before the var it writes, before the consuming query.
  assert.ok(ids.indexOf('opt:cityFilter') < ids.indexOf('var:city'))
  assert.ok(ids.indexOf('var:city') < ids.indexOf('wq:revenueKpi'))
})

test('changing a leaf variable only refires its direct consumers', () => {
  const g = buildFilterGraph(cascadeSpec())
  const dirty = dirtySubgraph(g, 'city')
  const ids = dirty.map((n) => n.id)
  assert.deepEqual(ids, ['wq:revenueKpi'])
})

test('dirtySubgraph for an unknown variable returns []', () => {
  const g = buildFilterGraph(cascadeSpec())
  assert.deepEqual(dirtySubgraph(g, 'nonexistent'), [])
})

test('dirtySubgraph with missing graph / var returns []', () => {
  assert.deepEqual(dirtySubgraph(null, 'x'), [])
  assert.deepEqual(dirtySubgraph({ nodes: new Map(), edges: new Map(), order: [] }, ''), [])
})

// ---------------------------------------------------------------------------
// staleOptionWidgetIds
// ---------------------------------------------------------------------------

test('staleOptionWidgetIds extracts option-query widget ids, de-duped, order-preserving', () => {
  const g = buildFilterGraph(cascadeSpec())
  const dirty = dirtySubgraph(g, 'country')
  assert.deepEqual(staleOptionWidgetIds(dirty), ['cityFilter'])
})

test('staleOptionWidgetIds tolerates null/empty input', () => {
  assert.deepEqual(staleOptionWidgetIds(null), [])
  assert.deepEqual(staleOptionWidgetIds([]), [])
})

test('staleOptionWidgetIds ignores non-option-query nodes', () => {
  const nodes = [
    { kind: 'widget-query', widgetId: 'w1' },
    { kind: 'variable', name: 'v' },
    { kind: 'option-query', widgetId: 'f1' },
    { kind: 'option-query', widgetId: 'f1' }, // duplicate
  ]
  assert.deepEqual(staleOptionWidgetIds(nodes), ['f1'])
})
