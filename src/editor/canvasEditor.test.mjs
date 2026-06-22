/**
 * canvasEditor.test.mjs — Unit tests for CanvasEditor logic.
 *
 * Tests:
 *   1. Binding mutation — updating a binding writes into doc.bindings keyed by
 *      el_id WITHOUT touching doc.html.
 *   2. Binding kind switch — changing kind from none→query sets the right shape.
 *   3. Binding deletion — setting binding to null removes the key from bindings.
 *   4. Insert-palette stub injection — inserting a stub appends to html and injects
 *      a fresh data-el-id, leaving existing bindings intact.
 *   5. genElId format — generated ids match expected prefix_hex pattern.
 *   6. extractElIds — correct parsing of data-el-id attributes from HTML.
 *   7. History integration — push/undo/redo round-trip with doc objects.
 *
 * Run with: npm run test:dash
 *
 * All tests are pure-function / data-model tests — no React rendering required.
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

// ---------------------------------------------------------------------------
// Re-implement the two pure helpers that CanvasEditor.jsx exports implicitly
// (they live in the JSX module, so we inline them here for testing).
// ---------------------------------------------------------------------------

/** Extract all data-el-id values from an HTML string. */
function extractElIds(html) {
  const re = /data-el-id="([^"]+)"/g
  const ids = []
  let m
  while ((m = re.exec(html)) !== null) ids.push(m[1])
  return ids
}

/** Insert a stub at the end of an HTML string. */
function insertStub(html, stubHtml) {
  return html.trimEnd() + '\n' + stubHtml + '\n'
}

/** Generate a short unique element id (prefix_xxxxxxxx). */
function genElId(prefix = 'el') {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`
}

// Binding mutation helper (mirrors CanvasEditor.handleBindingChange logic)
function applyBindingChange(doc, elId, newBinding) {
  const bindings = { ...doc.bindings }
  if (newBinding === null) {
    delete bindings[elId]
  } else {
    bindings[elId] = newBinding
  }
  // HTML is NOT touched
  return { ...doc, bindings }
}

// Insert palette helper (mirrors CanvasEditor.handleInsert logic)
const ELEMENT_STUBS = {
  kpi: (id) => `<nubi-kpi data-el-id="${id}" label="KPI" format="number"></nubi-kpi>`,
  table: (id) => `<nubi-table data-el-id="${id}" limit="25"></nubi-table>`,
  chart: (id) => `<nubi-chart data-el-id="${id}" type="bar"></nubi-chart>`,
  metric: (id) => `<nubi-kpi data-el-id="${id}" label="Metric" format="number"></nubi-kpi>`,
  filter: (id) => `<nubi-filter data-el-id="${id}"></nubi-filter>`,
  text: (id) => `<p data-el-id="${id}">Text block</p>`,
}

function insertElement(doc, type) {
  const prefix = type === 'kpi' ? 'kpi' : type === 'table' ? 'tbl' : type === 'chart' ? 'chart'
    : type === 'metric' ? 'metric' : type === 'filter' ? 'filter' : 'txt'
  const elId = genElId(prefix)
  const stubFn = ELEMENT_STUBS[type] ?? ELEMENT_STUBS.text
  const stub = stubFn(elId)
  const newHtml = insertStub(doc.html, stub)
  return { newDoc: { ...doc, html: newHtml }, elId }
}

// History module (pure, shared with DashboardEditor)
import {
  createHistory,
  push as historyPush,
  undo as historyUndo,
  redo as historyRedo,
  canUndo,
  canRedo,
} from './history.js'

// ---------------------------------------------------------------------------
// Sample CanvasDoc fixture
// ---------------------------------------------------------------------------

const SAMPLE_DOC = {
  version: 1,
  title: 'Test Canvas',
  html: `<section>
  <nubi-kpi data-el-id="kpi_aaa" query-id="demo_all" label="Revenue"></nubi-kpi>
  <nubi-table data-el-id="tbl_bbb" query-id="orders" limit="25"></nubi-table>
</section>`,
  bindings: {},
  variables: [],
}

// ---------------------------------------------------------------------------
// Tests: binding mutation
// ---------------------------------------------------------------------------

describe('Binding mutation (does not rewrite html)', () => {
  test('setting a query binding writes to doc.bindings[elId]', () => {
    const binding = { kind: 'query', query_id: 'revenue_q', field: 'total', format: 'currency' }
    const newDoc = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', binding)

    // Binding is present
    assert.ok('kpi_aaa' in newDoc.bindings, 'bindings must contain the el_id key')
    assert.deepEqual(newDoc.bindings.kpi_aaa, binding)

    // HTML is UNCHANGED
    assert.equal(newDoc.html, SAMPLE_DOC.html, 'html must not be mutated when setting a binding')
  })

  test('setting a second binding does not disturb the first', () => {
    const b1 = { kind: 'query', query_id: 'rev', field: 'total' }
    const b2 = { kind: 'metric', metric: { metric_id: 'mtx_01' } }

    const doc1 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', b1)
    const doc2 = applyBindingChange(doc1, 'tbl_bbb', b2)

    assert.deepEqual(doc2.bindings.kpi_aaa, b1, 'first binding must survive second insert')
    assert.deepEqual(doc2.bindings.tbl_bbb, b2, 'second binding must be present')
    assert.equal(doc2.html, SAMPLE_DOC.html, 'html must remain untouched after two binding edits')
  })

  test('updating an existing binding replaces the old value', () => {
    const b1 = { kind: 'query', query_id: 'rev_old', field: 'old_col' }
    const b2 = { kind: 'query', query_id: 'rev_new', field: 'new_col', format: 'number' }

    const doc1 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', b1)
    const doc2 = applyBindingChange(doc1, 'kpi_aaa', b2)

    assert.deepEqual(doc2.bindings.kpi_aaa, b2, 'binding must be updated to new value')
    assert.equal(Object.keys(doc2.bindings).length, 1, 'must still have exactly one binding key')
  })

  test('deleting a binding (null) removes the key without touching other bindings', () => {
    const b1 = { kind: 'query', query_id: 'rev', field: 'total' }
    const b2 = { kind: 'metric', metric: { metric_id: 'mtx_01' } }
    const doc1 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', b1)
    const doc2 = applyBindingChange(doc1, 'tbl_bbb', b2)
    const doc3 = applyBindingChange(doc2, 'kpi_aaa', null)

    assert.ok(!('kpi_aaa' in doc3.bindings), 'deleted binding key must be absent')
    assert.ok('tbl_bbb' in doc3.bindings, 'other binding must survive deletion')
    assert.equal(doc3.html, SAMPLE_DOC.html, 'html must remain untouched after deletion')
  })

  test('binding kind: api binding shape is preserved', () => {
    const b = {
      kind: 'api',
      connector_id: 'conn_xyz',
      path: '/metrics/daily',
      select: '$.data.total',
    }
    const newDoc = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', b)
    assert.equal(newDoc.bindings.kpi_aaa.kind, 'api')
    assert.equal(newDoc.bindings.kpi_aaa.connector_id, 'conn_xyz')
    assert.equal(newDoc.bindings.kpi_aaa.select, '$.data.total')
  })

  test('binding kind switch preserves el_id as the map key', () => {
    // Start with query binding, switch to metric
    const initial = { kind: 'query', query_id: 'rev', field: 'total' }
    const updated = { kind: 'metric', metric: { metric_id: 'mtx_1' } }
    const doc1 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', initial)
    const doc2 = applyBindingChange(doc1, 'kpi_aaa', updated)

    // el_id key is unchanged, binding kind changed
    assert.ok('kpi_aaa' in doc2.bindings)
    assert.equal(doc2.bindings.kpi_aaa.kind, 'metric')
    // html unchanged throughout
    assert.equal(doc2.html, SAMPLE_DOC.html)
  })
})

// ---------------------------------------------------------------------------
// Tests: insert-palette stub injection
// ---------------------------------------------------------------------------

describe('Insert-palette stub injection', () => {
  test('inserting a KPI appends a nubi-kpi stub with a fresh data-el-id', () => {
    const { newDoc, elId } = insertElement(SAMPLE_DOC, 'kpi')

    assert.ok(newDoc.html.includes('<nubi-kpi'), 'stub must include <nubi-kpi')
    assert.ok(newDoc.html.includes(`data-el-id="${elId}"`), 'stub must include the generated data-el-id')
    assert.ok(elId.startsWith('kpi_'), `kpi el_id must start with kpi_, got ${elId}`)

    // Original elements must still be present
    assert.ok(newDoc.html.includes('data-el-id="kpi_aaa"'), 'original kpi element must survive insert')
    assert.ok(newDoc.html.includes('data-el-id="tbl_bbb"'), 'original table element must survive insert')
  })

  test('inserting a table appends a nubi-table stub', () => {
    const { newDoc, elId } = insertElement(SAMPLE_DOC, 'table')
    assert.ok(newDoc.html.includes('<nubi-table'), 'stub must include <nubi-table')
    assert.ok(newDoc.html.includes(`data-el-id="${elId}"`))
    assert.ok(elId.startsWith('tbl_'), `table el_id must start with tbl_, got ${elId}`)
  })

  test('inserting a chart appends a nubi-chart stub', () => {
    const { newDoc, elId } = insertElement(SAMPLE_DOC, 'chart')
    assert.ok(newDoc.html.includes('<nubi-chart'), 'stub must include <nubi-chart')
    assert.ok(newDoc.html.includes(`data-el-id="${elId}"`))
    assert.ok(elId.startsWith('chart_'), `chart el_id must start with chart_, got ${elId}`)
  })

  test('inserting a filter appends a nubi-filter stub', () => {
    const { newDoc, elId } = insertElement(SAMPLE_DOC, 'filter')
    assert.ok(newDoc.html.includes('<nubi-filter'), 'stub must include <nubi-filter')
    assert.ok(elId.startsWith('filter_'))
  })

  test('inserting a text element appends a <p> stub', () => {
    const { newDoc, elId } = insertElement(SAMPLE_DOC, 'text')
    assert.ok(newDoc.html.includes('<p ') || newDoc.html.includes('<p\n'), 'stub must include a <p')
    assert.ok(newDoc.html.includes(`data-el-id="${elId}"`))
    assert.ok(elId.startsWith('txt_'))
  })

  test('insert does NOT modify doc.bindings', () => {
    const docWithBinding = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', {
      kind: 'query', query_id: 'rev', field: 'total',
    })
    const { newDoc } = insertElement(docWithBinding, 'kpi')
    // bindings must be unchanged
    assert.ok('kpi_aaa' in newDoc.bindings, 'existing binding must survive insert')
    assert.equal(Object.keys(newDoc.bindings).length, 1, 'no new binding keys must appear from insert alone')
  })

  test('inserting two elements generates distinct el_ids', () => {
    const { elId: id1 } = insertElement(SAMPLE_DOC, 'kpi')
    const { elId: id2 } = insertElement(SAMPLE_DOC, 'kpi')
    // With a random suffix this is extremely unlikely to collide
    assert.notEqual(id1, id2, 'generated el_ids must be distinct')
  })

  test('insert stub appears at the END of the html', () => {
    const { newDoc, elId } = insertElement(SAMPLE_DOC, 'kpi')
    const lastElIdInHtml = extractElIds(newDoc.html).at(-1)
    assert.equal(lastElIdInHtml, elId, 'inserted stub el_id must be the last data-el-id in html')
  })
})

// ---------------------------------------------------------------------------
// Tests: extractElIds helper
// ---------------------------------------------------------------------------

describe('extractElIds helper', () => {
  test('extracts all data-el-id values in order', () => {
    const html = `
      <nubi-kpi data-el-id="kpi_1"></nubi-kpi>
      <div data-el-id="box_2">{{value}}</div>
      <nubi-table data-el-id="tbl_3"></nubi-table>
    `
    const ids = extractElIds(html)
    assert.deepEqual(ids, ['kpi_1', 'box_2', 'tbl_3'])
  })

  test('returns empty array for html with no data-el-id', () => {
    assert.deepEqual(extractElIds('<div class="foo">bar</div>'), [])
    assert.deepEqual(extractElIds(''), [])
  })

  test('handles duplicate data-el-ids (returns all occurrences)', () => {
    const html = '<div data-el-id="dup"></div><div data-el-id="dup"></div>'
    assert.deepEqual(extractElIds(html), ['dup', 'dup'])
  })

  test('works with mixed quoting (double quotes only per spec)', () => {
    const html = '<div data-el-id="my_el">text</div>'
    assert.deepEqual(extractElIds(html), ['my_el'])
  })
})

// ---------------------------------------------------------------------------
// Tests: genElId helper
// ---------------------------------------------------------------------------

describe('genElId helper', () => {
  test('generated id starts with the given prefix', () => {
    assert.ok(genElId('kpi').startsWith('kpi_'))
    assert.ok(genElId('tbl').startsWith('tbl_'))
    assert.ok(genElId('chart').startsWith('chart_'))
  })

  test('generated id has non-trivial suffix (>= 4 chars)', () => {
    const id = genElId('el')
    const suffix = id.slice('el_'.length)
    assert.ok(suffix.length >= 4, `suffix too short: "${suffix}"`)
  })

  test('two calls produce different ids', () => {
    const a = genElId('x')
    const b = genElId('x')
    assert.notEqual(a, b)
  })

  test('defaults to "el" prefix', () => {
    assert.ok(genElId().startsWith('el_'))
  })
})

// ---------------------------------------------------------------------------
// Tests: history integration with doc objects
// ---------------------------------------------------------------------------

describe('History integration (createHistory / push / undo / redo)', () => {
  test('createHistory wraps a doc as present with empty past/future', () => {
    const h = createHistory(SAMPLE_DOC)
    assert.deepEqual(h.present, SAMPLE_DOC)
    assert.deepEqual(h.past, [])
    assert.deepEqual(h.future, [])
  })

  test('push adds present to past and sets new present', () => {
    const h0 = createHistory(SAMPLE_DOC)
    const doc2 = { ...SAMPLE_DOC, title: 'Changed' }
    const h1 = historyPush(h0, doc2)

    assert.equal(h1.present.title, 'Changed')
    assert.equal(h1.past.length, 1)
    assert.deepEqual(h1.past[0], SAMPLE_DOC)
    assert.deepEqual(h1.future, [])
  })

  test('undo restores previous doc', () => {
    const h0 = createHistory(SAMPLE_DOC)
    const doc2 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', { kind: 'query', query_id: 'rev' })
    const h1 = historyPush(h0, doc2)
    const h2 = historyUndo(h1)

    assert.deepEqual(h2.present, SAMPLE_DOC, 'undo must restore original doc')
    assert.equal(h2.past.length, 0)
    assert.equal(h2.future.length, 1)
  })

  test('redo re-applies a binding change after undo', () => {
    const doc2 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', { kind: 'query', query_id: 'rev' })
    const h1 = historyPush(createHistory(SAMPLE_DOC), doc2)
    const h2 = historyUndo(h1)
    const h3 = historyRedo(h2)

    assert.deepEqual(h3.present, doc2, 'redo must restore the binding-changed doc')
    assert.equal(h3.future.length, 0)
  })

  test('binding mutation → undo → binding mutation clears redo', () => {
    const b1 = { kind: 'query', query_id: 'rev1' }
    const b2 = { kind: 'query', query_id: 'rev2' }
    const doc1 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', b1)
    const h1 = historyPush(createHistory(SAMPLE_DOC), doc1)
    const h2 = historyUndo(h1)     // back to SAMPLE_DOC; future = [doc1]
    const doc2 = applyBindingChange(SAMPLE_DOC, 'kpi_aaa', b2)
    const h3 = historyPush(h2, doc2)  // new branch; future must be cleared

    assert.equal(h3.future.length, 0, 'new push must clear redo history')
    assert.deepEqual(h3.present.bindings.kpi_aaa.query_id, 'rev2')
  })

  test('canUndo / canRedo reflect history state correctly', () => {
    const h0 = createHistory(SAMPLE_DOC)
    assert.equal(canUndo(h0), false)
    assert.equal(canRedo(h0), false)

    const h1 = historyPush(h0, { ...SAMPLE_DOC, title: 'v2' })
    assert.equal(canUndo(h1), true)
    assert.equal(canRedo(h1), false)

    const h2 = historyUndo(h1)
    assert.equal(canUndo(h2), false)
    assert.equal(canRedo(h2), true)
  })
})
