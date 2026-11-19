/**
 * DataGrid.report.test.tsx — the 'report' table, rendered for real on jsdom:
 *   - group columns of dates start newest first (text groups A→Z), also when
 *     the rows arrive after the table mounted, and a reader's own sort sticks;
 *   - hovering a header changes nothing (no hover colour, no arrow popping in);
 *   - `wrapHeaders` lets a long header break after its underscores only.
 */
import { test, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
import { JSDOM } from 'jsdom'

// The grid's empty-state pictures import a stylesheet, which Node cannot load.
// Stub .css imports as empty modules (only for this test process).
register('data:text/javascript,' + encodeURIComponent(`
  export async function load(url, context, next) {
    if (url.endsWith('.css')) return { format: 'module', source: '', shortCircuit: true }
    return next(url, context)
  }
`))

const dom = new JSDOM('<!doctype html><div id="root"></div>', { pretendToBeVisual: true })
const g = globalThis as any
g.window = dom.window
g.document = dom.window.document
g.HTMLElement = dom.window.HTMLElement
g.Element = dom.window.Element
g.Node = dom.window.Node
g.getComputedStyle = dom.window.getComputedStyle.bind(dom.window)
g.IS_REACT_ACT_ENVIRONMENT = true
g.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
dom.window.ResizeObserver = g.ResizeObserver
// jsdom lays nothing out, so every box is 0 px tall and the row virtualizer
// would draw no rows. Give boxes a height so the rows render.
Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 800 })
Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 1200 })

const React = await import('react')
const { createRoot } = await import('react-dom/client')
const { act } = await import('react')
const { default: DataGrid } = await import('./DataGrid.js')

const columns = [
  { key: 'FY', label: 'FY', type: 'date' },
  { key: 'Cat', label: 'Cat', type: 'string' },
  { key: 'Value', label: 'Forecasted_Contri_Perc', type: 'number', aggregation: 'sum' },
]
const rows = [
  { FY: '2025-01-01', Cat: 'B', Value: 1 },
  { FY: '2027-05-01', Cat: 'A', Value: 2 },
  { FY: '2026-12-01', Cat: 'C', Value: 3 },
  { FY: '2027-05-01', Cat: 'C', Value: 4 },
]

let root: any
beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>'
  root = createRoot(document.getElementById('root'))
})
afterEach(async () => { await act(async () => root.unmount()) })

async function render(props: Record<string, unknown>) {
  await act(async () => {
    root.render(React.createElement(DataGrid, { columns, appearance: 'report', paginate: false, ...props }))
  })
}

/** Group labels in screen order, without their "(n)" counts. */
const groupLabels = () =>
  [...document.querySelectorAll('tbody td button')].map((b) => b.textContent!.replace(/\(\d+\)$/, '').trim())

const headerFor = (label: string) =>
  [...document.querySelectorAll('thead th')].find((th) => th.querySelector('button span')?.textContent === label)!

test('date groups open newest first', async () => {
  await render({ rows, initialGrouping: ['FY'] })
  assert.deepEqual(groupLabels(), ['2027-05-01', '2026-12-01', '2025-01-01'])
})

test('text groups still open A→Z', async () => {
  await render({ rows, initialGrouping: ['Cat'] })
  assert.deepEqual(groupLabels(), ['A', 'B', 'C'])
})

test('newest first also when the rows arrive after the table mounted', async () => {
  await render({ rows: [], loading: true, initialGrouping: ['FY'], columns: [] })
  await render({ rows, initialGrouping: ['FY'] })
  assert.deepEqual(groupLabels(), ['2027-05-01', '2026-12-01', '2025-01-01'])
})

test("a reader's own sort is kept when new rows arrive", async () => {
  await render({ rows, initialGrouping: ['FY'] })
  await act(async () => { (headerFor('FY').querySelector('button') as HTMLButtonElement).click() })
  const afterClick = groupLabels()
  assert.notDeepEqual(afterClick, ['2027-05-01', '2026-12-01', '2025-01-01'])
  await render({ rows: [...rows], initialGrouping: ['FY'] })
  assert.deepEqual(groupLabels(), afterClick)
})

test('hovering a report header changes nothing', async () => {
  await render({ rows, initialGrouping: ['FY'], headerStyle: { background: '#002060', color: '#ffffff' } })
  for (const th of document.querySelectorAll('thead th')) {
    const button = th.querySelector('button')!
    assert.ok(!/hover:/.test(button.className), `header button has a hover style: ${button.className}`)
    assert.ok(!/transition/.test(button.className), 'header button animates')
    const arrow = button.querySelector('span[data-export-hide]')!
    assert.ok(!/group-hover/.test(arrow.className), 'sort arrow still appears on hover')
  }
  // unsorted columns show no arrow at all; the sorted group column shows its own
  assert.equal(headerFor('Cat').querySelector('span[data-export-hide]')!.className, 'hidden')
  assert.equal(headerFor('FY').querySelector('span[data-export-hide]')!.className, 'shrink-0')
})

test('report rows and cells do not animate colour changes', async () => {
  await render({ rows, initialGrouping: ['FY'], rowStripe: { odd: '#f0f0f0', even: '#ffffff' } })
  for (const el of document.querySelectorAll('tbody tr, tbody td, tbody td button')) {
    assert.ok(!/transition-colors/.test(el.className), `${el.tagName} still has transition-colors`)
    if (el.tagName === 'BUTTON') assert.ok(!/hover:text/.test(el.className), 'group button changes colour on hover')
  }
})

test('wrapHeaders: breaks allowed after underscores only, text unchanged', async () => {
  await render({ rows, initialGrouping: ['FY'], wrapHeaders: true })
  const label = headerFor('Forecasted_Contri_Perc').querySelector('button span')!
  assert.equal(label.querySelectorAll('wbr').length, 2)
  assert.equal(label.textContent, 'Forecasted_Contri_Perc')
})

test('without wrapHeaders a header has no break points', async () => {
  await render({ rows, initialGrouping: ['FY'] })
  assert.equal(headerFor('Forecasted_Contri_Perc').querySelectorAll('wbr').length, 0)
})
