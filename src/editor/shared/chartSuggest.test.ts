/**
 * chartSuggest.test.ts — profiling a result and ranking visuals for it.
 *
 * Run: npm run test:dash
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { makeTable, vectorFromArray, Float64, Int32, Utf8, Bool, DateDay } from 'apache-arrow'

import { profileTable, suggestVisuals, encodingFor } from './chartSuggest.js'

const tbl = (cols: Record<string, any>) => makeTable(cols as any)
const f64 = (xs: number[]) => vectorFromArray(xs, new Float64())
const i32 = (xs: number[]) => vectorFromArray(xs, new Int32())
const str = (xs: string[]) => vectorFromArray(xs, new Utf8())

const kindOf = (p: ReturnType<typeof profileTable>, name: string) => p.columns.find(c => c.name === name)?.kind
const types = (s: ReturnType<typeof suggestVisuals>) => s.map(x => x.type)

describe('profileTable', () => {
  test('classifies by arrow type and by value', () => {
    const p = profileTable(tbl({
      day:      vectorFromArray([new Date('2024-01-01'), new Date('2024-01-02')], new DateDay()),
      month:    str(['2024-01', '2024-02']),
      region:   str(['North', 'South']),
      order_id: i32([101, 102]),
      active:   vectorFromArray([true, false], new Bool()),
      revenue:  f64([10, 20]),
    }))
    assert.equal(kindOf(p, 'day'), 'temporal')
    assert.equal(kindOf(p, 'month'), 'temporal', 'ISO date strings are a time axis')
    assert.equal(kindOf(p, 'region'), 'category')
    assert.equal(kindOf(p, 'order_id'), 'id', 'unique integer named like a key')
    assert.equal(kindOf(p, 'active'), 'boolean')
    assert.equal(kindOf(p, 'revenue'), 'measure')
    assert.equal(p.rows, 2)
  })

  test('measure stats: sign, range, monotonic', () => {
    const p = profileTable(tbl({
      stage: str(['Visit', 'Cart', 'Pay']),
      users: f64([100, 60, 20]),
      delta: f64([5, -3, 2]),
    }))
    const users = p.columns.find(c => c.name === 'users')!
    assert.equal(users.allPositive, true)
    assert.equal(users.monotonicDown, true)
    assert.equal(users.min, 20)
    assert.equal(users.max, 100)
    const delta = p.columns.find(c => c.name === 'delta')!
    assert.equal(delta.hasNegative, true)
    assert.equal(delta.monotonicDown, false)
  })

  test('a four-digit integer named year is temporal, a plain count is not', () => {
    const p = profileTable(tbl({ year: i32([2021, 2022, 2023]), count: i32([5, 9, 12]) }))
    assert.equal(kindOf(p, 'year'), 'temporal')
    assert.equal(kindOf(p, 'count'), 'measure')
  })

  test('reads through the decimal read-shim (getChild only, no getChildAt)', () => {
    const real = tbl({ k: str(['a', 'b']), v: f64([1, -2]) })
    const shim = { numRows: real.numRows, schema: real.schema, getChild: (n: string) => real.getChild(n) }
    const p = profileTable(shim)
    assert.equal(kindOf(p, 'v'), 'measure')
    assert.equal(p.columns.find(c => c.name === 'v')!.hasNegative, true)
  })

  test('tolerates no table', () => {
    const p = profileTable(undefined)
    assert.equal(p.rows, 0)
    assert.deepEqual(suggestVisuals(p), [])
  })
})

describe('suggestVisuals', () => {
  test('single row with a number → KPI first', () => {
    const p = profileTable(tbl({ total_sales: f64([1234]) }))
    const s = suggestVisuals(p)
    assert.equal(s[0].type, 'kpi')
    assert.deepEqual(s[0].encoding, { value: 'total_sales', spark: 'total_sales' })
  })

  test('single bounded rate → KPI then gauge', () => {
    const p = profileTable(tbl({ completion_pct: f64([87.5]) }))
    assert.deepEqual(types(suggestVisuals(p)).slice(0, 2), ['kpi', 'gauge'])
  })

  test('date + measure → line first, with the date on x', () => {
    const p = profileTable(tbl({
      month:   str(['2024-01', '2024-02', '2024-03', '2024-04']),
      revenue: f64([100, 120, 90, 140]),
    }))
    const s = suggestVisuals(p)
    assert.equal(s[0].type, 'line')
    assert.equal(s[0].encoding.x, 'month')
    assert.equal(s[0].encoding.y, 'revenue')
    assert.match(s[0].reason, /time axis/)
  })

  test('date + category + measure → line grouped by the category', () => {
    const p = profileTable(tbl({
      region:  str(['N', 'S', 'N', 'S']),
      month:   str(['2024-01', '2024-01', '2024-02', '2024-02']),
      revenue: f64([1, 2, 3, 4]),
    }))
    const s = suggestVisuals(p)
    assert.equal(s[0].type, 'line')
    assert.equal(s[0].encoding.x, 'month', 'time wins x even though the category comes first')
    assert.equal(s[0].encoding.color, 'region')
  })

  test('two measures on very different scales over time → combo beats line', () => {
    const p = profileTable(tbl({
      month:  str(['2024-01', '2024-02', '2024-03']),
      units:  f64([100000, 120000, 90000]),
      margin: f64([12.5, 13.1, 11.8]),
    }))
    const s = suggestVisuals(p)
    assert.equal(s[0].type, 'combo')
    assert.deepEqual(s[0].encoding, { x: 'month', bars: ['units'], lines: ['margin'] })
  })

  test('few categories + measure → bar first; donut offered for parts of a whole', () => {
    const p = profileTable(tbl({
      channel: str(['Web', 'Store', 'Phone']),
      orders:  f64([50, 30, 20]),
    }))
    const s = suggestVisuals(p, { limit: 10 })
    assert.equal(s[0].type, 'bar')
    assert.ok(types(s).includes('donut'))
    assert.ok(!types(s).includes('pie') || types(s).indexOf('donut') < types(s).indexOf('pie'))
  })

  test('many categories → horizontal bars, no pie', () => {
    const names = Array.from({ length: 25 }, (_, i) => `Store ${i}`)
    const p = profileTable(tbl({ store: str(names), sales: f64(names.map((_, i) => i + 1)) }))
    const s = suggestVisuals(p, { limit: 10 })
    assert.equal(s[0].type, 'bar')
    assert.equal(s[0].config?.orientation, 'horizontal')
    assert.ok(!types(s).includes('pie'))
    assert.ok(!types(s).includes('donut'))
  })

  test('declining stages → funnel; a merely sorted result does not', () => {
    const p = profileTable(tbl({
      stage: str(['Visit', 'Signup', 'Trial', 'Paid']),
      users: f64([1000, 400, 150, 40]),
    }))
    assert.equal(suggestVisuals(p)[0].type, 'funnel')
    const sorted = profileTable(tbl({
      product: str(['A', 'B', 'C', 'D']),
      sales:   f64([1000, 400, 150, 40]),
    }))
    assert.equal(suggestVisuals(sorted)[0].type, 'bar', 'ORDER BY sales DESC is not a funnel')
  })

  test('few cells → grouped bar beats heatmap', () => {
    const p = profileTable(tbl({
      region: str(['N', 'N', 'S', 'S']), half: str(['H1', 'H2', 'H1', 'H2']), sales: f64([1, 2, 3, 4]),
    }))
    const s = suggestVisuals(p)
    assert.equal(s[0].type, 'bar')
    assert.equal(s[0].encoding.color, 'half')
  })

  test('two small categories + measure → heatmap and pivot', () => {
    const rows = [] as [string, string, number][]
    for (const r of ['N', 'S', 'E', 'W']) for (const q of ['Q1', 'Q2', 'Q3', 'Q4']) rows.push([r, q, Math.random()])
    const p = profileTable(tbl({
      region: str(rows.map(r => r[0])), quarter: str(rows.map(r => r[1])), sales: f64(rows.map(r => r[2])),
    }))
    const s = suggestVisuals(p, { limit: 10 })
    assert.equal(s[0].type, 'heatmap')
    assert.ok(types(s).includes('pivot'))
    const pivot = s.find(x => x.type === 'pivot')!
    assert.deepEqual(pivot.encoding, { rows: 'region', cols: 'quarter', value: 'sales' })
  })

  test('source/target/value → sankey', () => {
    const p = profileTable(tbl({
      source: str(['A', 'A', 'B']), target: str(['X', 'Y', 'X']), value: f64([1, 2, 3]),
    }))
    const s = suggestVisuals(p)
    assert.equal(s[0].type, 'sankey')
    assert.deepEqual(s[0].encoding, { source: 'source', target: 'target', value: 'value' })
  })

  test('two measures, no dimensions → scatter', () => {
    const n = 20
    const p = profileTable(tbl({
      spend: f64(Array.from({ length: n }, (_, i) => i)),
      leads: f64(Array.from({ length: n }, (_, i) => i * 2)),
    }))
    assert.equal(suggestVisuals(p)[0].type, 'scatter')
  })

  test('country column → map suggested', () => {
    const p = profileTable(tbl({ country: str(['ZA', 'KE', 'NG']), sales: f64([1, 2, 3]) }))
    const s = suggestVisuals(p, { limit: 10 })
    const map = s.find(x => x.type === 'map')!
    assert.ok(map)
    assert.deepEqual(map.encoding, { name: 'country', value: 'sales' })
  })

  test('OHLC over time → candlestick', () => {
    const p = profileTable(tbl({
      day: str(['2024-01-01', '2024-01-02']),
      open: f64([1, 2]), high: f64([3, 4]), low: f64([0, 1]), close: f64([2, 3]),
    }))
    const s = suggestVisuals(p)
    assert.equal(s[0].type, 'candlestick')
    assert.deepEqual(s[0].encoding, { x: 'day', open: 'open', close: 'close', low: 'low', high: 'high' })
  })

  test('raw rows per category → box plot', () => {
    const cats = ['A', 'B']
    const p = profileTable(tbl({
      team:  str(Array.from({ length: 40 }, (_, i) => cats[i % 2])),
      hours: f64(Array.from({ length: 40 }, (_, i) => i)),
    }))
    assert.equal(suggestVisuals(p)[0].type, 'boxplot')
  })

  test('no numeric column → table, and nothing else pretends to work', () => {
    const p = profileTable(tbl({ name: str(['a', 'b']), email: str(['x', 'y']) }))
    const s = suggestVisuals(p)
    assert.deepEqual(types(s), ['table'])
  })

  test('every suggestion has an actionable reason and a usable encoding', () => {
    const p = profileTable(tbl({
      month: str(['2024-01', '2024-02', '2024-03']), region: str(['N', 'S', 'N']),
      revenue: f64([1, 2, 3]), cost: f64([1, 1, 2]),
    }))
    for (const s of suggestVisuals(p, { limit: Infinity })) {
      assert.ok(s.reason.length > 10, `${s.type} has a reason`)
      assert.ok(s.score > 0 && s.score <= 100)
      if (s.widgetType === 'chart') assert.ok(Object.keys(s.encoding).length > 0, `${s.type} has an encoding`)
    }
  })
})

describe('encodingFor', () => {
  test('bar prefers the category; line prefers the date', () => {
    const p = profileTable(tbl({
      month: str(['2024-01', '2024-02']), region: str(['N', 'S']), revenue: f64([1, 2]),
    }))
    assert.equal(encodingFor('bar', p).encoding.x, 'region')
    assert.equal(encodingFor('line', p).encoding.x, 'month')
  })

  test('narrow result still fills required slots, reports the truly impossible', () => {
    const p = profileTable(tbl({ k: str(['a', 'b']), v: f64([1, 2]) }))
    assert.deepEqual(encodingFor('bar', p).missing, [])
    const sankey = encodingFor('sankey', p)
    assert.ok(sankey.missing.length > 0, 'sankey needs two distinct dimension columns')
  })

  test('optional colour slot is never a second date column', () => {
    const p = profileTable(tbl({
      month: str(['2024-01', '2024-02']), week: str(['2024-01-01', '2024-01-08']), revenue: f64([1, 2]),
    }))
    assert.equal(encodingFor('line', p).encoding.color, undefined)
  })
})
