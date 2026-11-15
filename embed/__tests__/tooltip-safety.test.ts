/**
 * A board spec is data. Its `config.tooltip` may tune how the tooltip looks,
 * but ECharts renders tooltip formatters and extra CSS as HTML/CSS in the
 * viewer's page — so those keys never pass through from the spec.
 */
import { describe, test, expect } from 'vitest'
import { buildChartOption } from '../widgets/chart-options'

const ROWS = [
  { month: 'Jan', sales: 10 },
  { month: 'Feb', sales: 20 },
]
const PAYLOAD = '<img src=x onerror="alert(document.cookie)">'

function build(tooltip: any, type = 'line') {
  return buildChartOption({
    type,
    table: ROWS,
    encoding: { x: 'month', y: 'sales' },
    config: { tooltip },
    theme: {},
  })
}

describe('config.tooltip is whitelisted', () => {
  test('formatter, extraCssText and className never reach the chart option', () => {
    const opt = build({
      formatter: PAYLOAD,
      extraCssText: 'position:fixed;inset:0;background:url(https://evil.example/x)',
      className: 'x" onmouseover="alert(1)',
      renderMode: 'html',
      position: () => [0, 0],
      appendTo: 'body',
      enterable: true,
    })
    const json = JSON.stringify(opt.tooltip)
    expect(json).not.toContain('onerror')
    expect(json).not.toContain('evil.example')
    for (const key of ['formatter', 'extraCssText', 'className', 'renderMode', 'position', 'appendTo', 'enterable']) {
      expect(opt.tooltip).not.toHaveProperty(key)
    }
  })

  test('nested text style cannot smuggle rich HTML either', () => {
    const opt = build({ textStyle: { color: '#123456', fontSize: 14, rich: { a: {} }, formatter: PAYLOAD } })
    expect(opt.tooltip.textStyle.color).toBe('#123456')
    expect(opt.tooltip.textStyle.fontSize).toBe(14)
    expect(JSON.stringify(opt.tooltip)).not.toContain('onerror')
    expect(opt.tooltip.textStyle).not.toHaveProperty('rich')
  })

  test('the supported look-and-feel keys still work', () => {
    const opt = build({ trigger: 'item', valueFormat: { style: 'number' }, backgroundColor: '#ffffff', borderColor: '#000000', confine: false, show: true })
    expect(opt.tooltip.trigger).toBe('item')
    expect(opt.tooltip.backgroundColor).toBe('#ffffff')
    expect(opt.tooltip.borderColor).toBe('#000000')
    expect(typeof opt.tooltip.valueFormatter).toBe('function')
    expect(build(false).tooltip).toEqual({ show: false })
  })

  test('colour values must look like colours', () => {
    const opt = build({ backgroundColor: 'url(https://evil.example/x)', borderColor: 'red;position:fixed' })
    expect(JSON.stringify(opt.tooltip)).not.toContain('evil.example')
    expect(JSON.stringify(opt.tooltip)).not.toContain('position:fixed')
  })
})
