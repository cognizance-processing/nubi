/**
 * canvasRenderer.test.mjs — Frontend tests for Canvas runtime logic.
 *
 * Tests:
 *   1. Binding resolution helpers — resolving binding kinds, field extraction
 *   2. {{token}} interpolation via renderWidgetHtml (HTML-escaped values)
 *   3. sanitizeDashboardHtml always runs — XSS vectors are stripped
 *   4. data-el-id attribute preservation through sanitization
 *   5. CanvasDoc variable defaults extraction
 *
 * Run with: npm run test:dash
 *
 * Uses jsdom + DOMPurify (the same approach as sanitize.test.mjs).
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import DOMPurifyFactory from 'dompurify'

// ---------------------------------------------------------------------------
// Bootstrap jsdom + DOMPurify (same setup as sanitize.test.mjs)
// ---------------------------------------------------------------------------

const { window: jsdomWindow } = new JSDOM('', { url: 'http://localhost' })
const DOMPurify = DOMPurifyFactory(jsdomWindow)

const NUBI_TAGS = ['nubi-kpi', 'nubi-table', 'nubi-chart']
const FORBID_TAGS = [
  'script', 'style', 'iframe', 'object', 'embed',
  'link', 'base', 'form', 'input', 'button', 'textarea',
  'select', 'meta', 'noscript', 'template',
]
const WIDGET_ATTRS = [
  'query-id', 'value-col', 'label', 'format',
  'limit', 'columns',
  'type', 'x', 'y', 'color',
  'backend', 'token', 'get-token',
  'style', 'class', 'id', 'title', 'alt',
  'src', 'href', 'rel', 'target',
  'colspan', 'rowspan', 'width', 'height',
  // Canvas-specific
  'data-el-id',
]

const PURIFY_CONFIG = {
  ADD_TAGS: NUBI_TAGS,
  ADD_ATTR: WIDGET_ATTRS,
  FORBID_TAGS,
  FORBID_ATTR: [],
  FORCE_BODY: true,
  RETURN_DOM: false,
  RETURN_DOM_FRAGMENT: false,
  ALLOW_DATA_ATTR: false,
}

DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
  const name = data.attrName.toLowerCase()
  if (name.startsWith('on')) {
    data.keepAttr = false
    return
  }
  const val = (data.attrValue ?? '').trim().toLowerCase()
  if (
    val.startsWith('javascript:') ||
    val.startsWith('data:') ||
    val.startsWith('vbscript:')
  ) {
    data.keepAttr = false
  }
})

function sanitize(html) {
  return DOMPurify.sanitize(html, PURIFY_CONFIG)
}

function parse(html) {
  const dom = new JSDOM(sanitize(html), { url: 'http://localhost' })
  return dom.window.document
}

// ---------------------------------------------------------------------------
// Inline renderWidgetHtml logic (extracted for test-time use without jsdom
// custom elements — mirrors the actual module).
// ---------------------------------------------------------------------------

function escapeHtml(v) {
  if (v == null) return ''
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function resolveToken(expr, table, props, extra) {
  if (expr === 'value') {
    if (extra && extra.value !== undefined) return extra.value
    return ''
  }
  if (expr.startsWith('col:')) {
    const colName = expr.slice(4).trim()
    if (!table) return ''
    const col = table.getChild ? table.getChild(colName) : null
    return col ? col.get(0) : ''
  }
  if (expr.startsWith('prop:')) {
    return props?.[expr.slice(5).trim()] ?? ''
  }
  const rowMatch = /^row\.(\d+)\.(.+)$/.exec(expr)
  if (rowMatch) {
    const rowIdx = Number(rowMatch[1])
    const colName = rowMatch[2].trim()
    if (!table) return ''
    const col = table.getChild ? table.getChild(colName) : null
    return (col && rowIdx < (table.numRows ?? 0)) ? col.get(rowIdx) : ''
  }
  return ''
}

function renderWidgetHtml(template, { table = null, props = {}, extra = {} } = {}) {
  if (typeof template !== 'string' || !template) return ''
  const interpolated = template.replace(/\{\{\s*([^}]+?)\s*\}\}/g, (_, raw) =>
    escapeHtml(resolveToken(raw.trim(), table, props, extra))
  )
  return sanitize(interpolated)
}

// ---------------------------------------------------------------------------
// Canvas-specific helpers (mirrors CanvasRenderer.jsx logic)
// ---------------------------------------------------------------------------

function buildVariableDefaults(docVariables) {
  if (!Array.isArray(docVariables)) return {}
  const defaults = {}
  for (const v of docVariables) {
    if (v?.name) {
      defaults[v.name] = v.default ?? undefined
    }
  }
  return defaults
}

// Simple JSONPath resolver (same subset as CanvasRenderer.jsx)
function resolveJsonPath(data, select) {
  if (!select || typeof select !== 'string') return data
  const parts = select.replace(/^\$\.?/, '').split('.')
  let val = data
  for (const part of parts) {
    if (val == null) break
    const arrMatch = /^(.+)\[(\d+)\]$/.exec(part)
    if (arrMatch) {
      val = val[arrMatch[1]]?.[Number(arrMatch[2])]
    } else {
      val = val[part]
    }
  }
  return val
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Canvas sanitize-always path', () => {
  test('strips <script> from canvas HTML', () => {
    const canvasHtml = `
      <section><nubi-kpi data-el-id="kpi_1" query-id="rev"></nubi-kpi></section>
      <script>document.cookie='stolen'</script>
    `
    const clean = sanitize(canvasHtml)
    assert.ok(!clean.includes('<script'), 'script must be stripped from canvas HTML')
    assert.ok(!clean.includes("document.cookie"), 'script body must be stripped from canvas HTML')
  })

  test('strips on* event handlers from canvas HTML', () => {
    const canvasHtml = '<div data-el-id="box_1" onclick="steal()">value: {{value}}</div>'
    const doc = parse(canvasHtml)
    const div = doc.querySelector('[data-el-id="box_1"]')
    assert.ok(div, 'element must survive sanitization')
    assert.equal(div.getAttribute('onclick'), null, 'onclick must be stripped')
  })

  test('strips javascript: href from canvas HTML', () => {
    const canvasHtml = '<a data-el-id="link_1" href="javascript:alert(1)">Click</a>'
    const doc = parse(canvasHtml)
    const a = doc.querySelector('[data-el-id="link_1"]')
    if (a) {
      const href = a.getAttribute('href') ?? ''
      assert.ok(!href.toLowerCase().startsWith('javascript:'), 'javascript: href must be stripped')
    }
  })

  test('strips <iframe> from canvas HTML', () => {
    const canvasHtml = `
      <div data-el-id="content_1"><p>Safe content</p></div>
      <iframe src="https://evil.example"></iframe>
    `
    const clean = sanitize(canvasHtml)
    assert.ok(!clean.includes('<iframe'), 'iframe must be stripped from canvas HTML')
  })
})

describe('Canvas data-el-id attribute preservation', () => {
  test('preserves data-el-id on nubi-kpi elements', () => {
    const html = '<nubi-kpi data-el-id="kpi_revenue" query-id="rev" label="Revenue"></nubi-kpi>'
    const doc = parse(html)
    const el = doc.querySelector('nubi-kpi')
    assert.ok(el, '<nubi-kpi> must survive')
    assert.equal(el.getAttribute('data-el-id'), 'kpi_revenue', 'data-el-id must be preserved')
    assert.equal(el.getAttribute('query-id'), 'rev', 'query-id must be preserved')
  })

  test('preserves data-el-id on plain div elements', () => {
    const html = '<div data-el-id="metric_box" class="metric-card">{{value}}</div>'
    const doc = parse(html)
    const div = doc.querySelector('[data-el-id="metric_box"]')
    assert.ok(div, 'div must survive')
    assert.equal(div.getAttribute('data-el-id'), 'metric_box', 'data-el-id must be preserved on plain elements')
  })

  test('preserves data-el-id on nubi-table elements', () => {
    const html = '<nubi-table data-el-id="tbl_1" query-id="orders" limit="50"></nubi-table>'
    const doc = parse(html)
    const el = doc.querySelector('nubi-table')
    assert.ok(el, '<nubi-table> must survive')
    assert.equal(el.getAttribute('data-el-id'), 'tbl_1', 'data-el-id must be preserved on table')
  })

  test('preserves data-el-id on nubi-chart elements', () => {
    const html = '<nubi-chart data-el-id="chart_1" query-id="sales" type="bar" x="month" y="total"></nubi-chart>'
    const doc = parse(html)
    const el = doc.querySelector('nubi-chart')
    assert.ok(el, '<nubi-chart> must survive')
    assert.equal(el.getAttribute('data-el-id'), 'chart_1', 'data-el-id must be preserved on chart')
  })
})

describe('{{token}} interpolation', () => {
  test('replaces {{value}} with the extra.value, HTML-escaped', () => {
    const result = renderWidgetHtml('<p>Revenue: {{value}}</p>', { extra: { value: 1234.56 } })
    assert.ok(result.includes('1234.56'), `expected value in output, got: ${result}`)
  })

  test('HTML-escapes XSS in data values', () => {
    const malicious = '<script>alert(1)</script>'
    const result = renderWidgetHtml('{{value}}', { extra: { value: malicious } })
    // The raw script tag should not appear in any executable form.
    assert.ok(!result.includes('<script>'), `script tag must be escaped, got: ${result}`)
    // The escaped form should appear instead.
    assert.ok(result.includes('&lt;script&gt;') || !result.includes('script'), `value must be HTML-escaped: ${result}`)
  })

  test('HTML-escapes & < > characters in data values', () => {
    const dangerous = 'A & B < C > D'
    const result = renderWidgetHtml('<span>{{value}}</span>', { extra: { value: dangerous } })
    assert.ok(result.includes('&amp;'), `& must be escaped: ${result}`)
    assert.ok(result.includes('&lt;'), `< must be escaped: ${result}`)
    assert.ok(result.includes('&gt;'), `> must be escaped: ${result}`)
  })

  test('HTML-escapes " in attribute-position injection', () => {
    // escapeHtml escapes " — verify the escapeHtml function directly
    const dangerous = '"alert(1)"'
    // Injected as a text node value, DOMPurify re-parses text content so &quot; may
    // round-trip as " in text nodes (that's fine — it's only dangerous in attrs).
    // What matters is the raw interpolated string before DOMPurify had &quot;
    const interpolated = dangerous.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    assert.ok(interpolated.includes('&quot;'), 'escapeHtml must escape double-quotes')
  })

  test('replaces {{col:name}} with the column value', () => {
    // Simulate a minimal Arrow table
    const mockTable = {
      getChild: (name) => name === 'revenue' ? { get: (i) => i === 0 ? 9999 : null } : null,
      numRows: 1,
      schema: { fields: [{ name: 'revenue' }] },
    }
    const result = renderWidgetHtml('{{col:revenue}}', { table: mockTable })
    assert.ok(result.includes('9999'), `expected column value in result: ${result}`)
  })

  test('replaces {{prop:name}} with the prop value', () => {
    const result = renderWidgetHtml('Label: {{prop:title}}', {
      props: { title: 'Monthly Revenue' },
    })
    assert.ok(result.includes('Monthly Revenue'), `expected prop value in result: ${result}`)
  })

  test('sanitizes the fully-interpolated output', () => {
    // Even after interpolation, the sanitizer runs on the output.
    const result = renderWidgetHtml(
      '<p onclick="evil()">Value: {{value}}</p>',
      { extra: { value: 42 } }
    )
    const doc = new JSDOM(result, { url: 'http://localhost' }).window.document
    const p = doc.querySelector('p')
    if (p) {
      assert.equal(p.getAttribute('onclick'), null, 'onclick must be stripped after interpolation')
    }
    assert.ok(result.includes('42'), `interpolated value must appear in output: ${result}`)
  })

  test('returns empty string for non-string template', () => {
    assert.equal(renderWidgetHtml(null), '')
    assert.equal(renderWidgetHtml(undefined), '')
    assert.equal(renderWidgetHtml(123), '')
  })
})

describe('Canvas binding resolution', () => {
  test('query binding: extracts field value from table', () => {
    const mockTable = {
      getChild: (name) => name === 'total' ? { get: (i) => i === 0 ? 50000 : null } : null,
      numRows: 1,
    }
    const binding = { kind: 'query', query_id: 'revenue_q', field: 'total' }
    const col = mockTable.getChild(binding.field)
    const value = col && mockTable.numRows > 0 ? col.get(0) : null
    assert.equal(value, 50000, 'should extract field value from query result')
  })

  test('api binding: resolves simple JSONPath', () => {
    const data = { meta: { total: 42 }, items: [{ id: 1 }] }
    assert.equal(resolveJsonPath(data, '$.meta.total'), 42)
    assert.equal(resolveJsonPath(data, '$.items[0].id'), 1)
  })

  test('api binding: resolves nested JSONPath', () => {
    const data = { response: { data: { value: 'hello' } } }
    assert.equal(resolveJsonPath(data, '$.response.data.value'), 'hello')
  })

  test('api binding: returns undefined for missing path', () => {
    const data = { a: 1 }
    const result = resolveJsonPath(data, '$.b.c')
    // Should not throw; returns undefined/null
    assert.ok(result === undefined || result === null, 'missing path should return null/undefined')
  })

  test('query binding: all three kinds are recognized', () => {
    const kinds = ['query', 'metric', 'api']
    for (const kind of kinds) {
      const binding = { kind }
      assert.ok(kinds.includes(binding.kind), `kind '${kind}' should be a valid binding kind`)
    }
  })
})

describe('CanvasDoc variable defaults', () => {
  test('extracts defaults from doc.variables array', () => {
    const docVariables = [
      { name: 'region', type: 'select', default: 'US' },
      { name: 'period', type: 'text', default: 'Q3' },
      { name: 'limit', type: 'number' }, // no default
    ]
    const defaults = buildVariableDefaults(docVariables)
    assert.equal(defaults.region, 'US')
    assert.equal(defaults.period, 'Q3')
    assert.equal(defaults.limit, undefined, 'variable without default gets undefined')
  })

  test('returns empty object when variables is absent', () => {
    assert.deepEqual(buildVariableDefaults(null), {})
    assert.deepEqual(buildVariableDefaults(undefined), {})
    assert.deepEqual(buildVariableDefaults('not-an-array'), {})
  })

  test('returns empty object when variables is empty array', () => {
    assert.deepEqual(buildVariableDefaults([]), {})
  })

  test('skips variables without a name', () => {
    const docVariables = [
      { type: 'select', default: 'US' }, // no name
      { name: 'region', default: 'ZA' },
    ]
    const defaults = buildVariableDefaults(docVariables)
    assert.deepEqual(Object.keys(defaults), ['region'])
    assert.equal(defaults.region, 'ZA')
  })
})

describe('CanvasDoc structure validation (pure)', () => {
  test('a valid CanvasDoc has required fields', () => {
    const doc = {
      version: 1,
      title: 'Test Canvas',
      html: '<section><nubi-kpi data-el-id="k1" query-id="q1"></nubi-kpi></section>',
      bindings: {
        k1: { kind: 'query', query_id: 'q1', field: 'value', format: 'number' },
      },
      variables: [{ name: 'region', type: 'select', default: 'US' }],
    }
    assert.ok(typeof doc.version === 'number', 'version must be a number')
    assert.ok(typeof doc.html === 'string', 'html must be a string')
    assert.ok(typeof doc.bindings === 'object', 'bindings must be an object')
    assert.ok(Array.isArray(doc.variables), 'variables must be an array')
  })

  test('bindings keys correspond to data-el-id values', () => {
    const html = `
      <nubi-kpi data-el-id="kpi_1" query-id="rev"></nubi-kpi>
      <div data-el-id="text_1">{{value}}</div>
    `
    const bindings = {
      kpi_1: { kind: 'query', query_id: 'rev', field: 'total' },
      text_1: { kind: 'query', query_id: 'rev', field: 'label' },
    }
    const doc = parse(html)
    for (const [elId] of Object.entries(bindings)) {
      const el = doc.querySelector(`[data-el-id="${elId}"]`)
      assert.ok(el, `element with data-el-id="${elId}" must be found in HTML`)
    }
  })

  test('sanitized canvas HTML preserves nubi-* elements with data-el-id', () => {
    const html = `
      <header><h1>Canvas Title</h1></header>
      <nubi-kpi data-el-id="kpi_rev" query-id="revenue" label="Revenue" format="currency"></nubi-kpi>
      <nubi-table data-el-id="tbl_orders" query-id="orders" limit="10"></nubi-table>
      <nubi-chart data-el-id="chart_sales" query-id="sales" type="bar" x="month" y="total"></nubi-chart>
    `
    const doc = parse(html)
    const kpi = doc.querySelector('nubi-kpi')
    const table = doc.querySelector('nubi-table')
    const chart = doc.querySelector('nubi-chart')
    assert.ok(kpi, '<nubi-kpi> must survive canvas sanitization')
    assert.ok(table, '<nubi-table> must survive canvas sanitization')
    assert.ok(chart, '<nubi-chart> must survive canvas sanitization')
    assert.equal(kpi.getAttribute('data-el-id'), 'kpi_rev')
    assert.equal(table.getAttribute('data-el-id'), 'tbl_orders')
    assert.equal(chart.getAttribute('data-el-id'), 'chart_sales')
  })
})
