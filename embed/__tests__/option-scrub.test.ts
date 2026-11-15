/**
 * Spec-supplied chart options never reach the page as markup, whatever shape
 * or depth they arrive in.
 */
import { describe, test, expect } from 'vitest'
import { buildChartOption, tableView, scrubRenderedHtml } from '../widgets/chart-options.js'

const PAYLOAD = '<img src=x onerror="alert(1)">'
const ROWS = [{ month: 'Jan', sales: 1 }, { month: 'Feb', sales: 2 }]

function build(echarts: Record<string, any>) {
  return buildChartOption({ type: 'bar', table: tableView(ROWS), encoding: { x: 'month', y: 'sales' }, config: { echarts } })
}

function nest(leaf: any, levels: number) {
  let node = leaf
  for (let i = 0; i < levels; i++) node = { children: [node] }
  return node
}

describe('option scrubbing', () => {
  test('tooltips given as a list are scrubbed', () => {
    const opt = scrubRenderedHtml({ tooltip: [{ formatter: PAYLOAD, extraCssText: 'x', trigger: 'item' }], series: [{ tooltip: [{ formatter: PAYLOAD }] }] })
    expect(JSON.stringify(opt)).not.toContain('onerror')
    expect(opt.tooltip).toEqual([{ trigger: 'item' }])
  })

  test('component tooltip content is dropped', () => {
    const opt = build({
      title: { tooltip: { show: true, content: PAYLOAD } },
      legend: { tooltip: { show: true, formatter: PAYLOAD } },
      toolbox: { tooltip: { content: PAYLOAD }, feature: { saveAsImage: { lang: [PAYLOAD] } } },
    })
    expect(JSON.stringify(opt)).not.toContain('onerror')
    expect(opt.title.tooltip).toEqual({ show: true })
  })

  test('the toolbox data view is dropped', () => {
    const opt = build({
      toolbox: { show: true, feature: { dataView: { show: true, title: PAYLOAD, lang: [PAYLOAD, 'x', 'y'], content: PAYLOAD }, restore: { show: true } } },
    })
    expect(JSON.stringify(opt)).not.toContain('onerror')
    expect(opt.toolbox.feature).toEqual({ restore: { show: true } })
  })

  test('nesting depth does not exempt a value', () => {
    for (const levels of [6, 13, 20, 40, 200]) {
      const opt = build({ series: [{ data: [nest({ tooltip: { formatter: PAYLOAD } }, levels)] }] })
      expect(JSON.stringify(opt), String(levels)).not.toContain('onerror')
    }
  })

  test('formatter functions and ordinary nested data survive', () => {
    const fn = () => 'x'
    const data = nest({ name: 'leaf', value: 1 }, 10)
    const opt = scrubRenderedHtml({ tooltip: { formatter: fn, valueFormatter: fn }, series: [{ type: 'tree', data: [data] }] })
    expect(opt.tooltip.formatter).toBe(fn)
    expect(opt.series[0].data[0]).toEqual(nest({ name: 'leaf', value: 1 }, 10))
  })
})
