// A server-side render applies the same option rules as the browser: nothing a
// spec supplied is drawn as markup, whatever shape or depth it arrives in.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { scrubRenderedHtml, MAX_OPTION_DEPTH } from './option-scrub.mjs'

const PAYLOAD = '<img src=x onerror="alert(1)">'
const nest = (leaf, levels) => {
  let node = leaf
  for (let i = 0; i < levels; i++) node = { children: [node] }
  return node
}

test('list tooltips, component tooltips and the data view are scrubbed', () => {
  const opt = scrubRenderedHtml({
    tooltip: [{ formatter: PAYLOAD, className: 'x', trigger: 'item' }],
    title: { link: 'javascript:alert(1)', sublink: 'https://example.com/', tooltip: { content: PAYLOAD, show: true } },
    toolbox: { feature: { dataView: { lang: [PAYLOAD], content: PAYLOAD }, saveAsImage: { lang: [PAYLOAD] }, restore: {} } },
  })
  assert.ok(!JSON.stringify(opt).includes('onerror'))
  assert.deepEqual(opt.tooltip, [{ trigger: 'item' }])
  assert.deepEqual(opt.title, { sublink: 'https://example.com/', tooltip: { show: true } })
  assert.deepEqual(opt.toolbox.feature, { saveAsImage: {}, restore: {} })
})

test('nesting depth does not exempt a value', () => {
  for (const levels of [6, 13, 20, 40, 200, 20000]) {
    const opt = scrubRenderedHtml({ series: [{ data: [nest({ tooltip: { formatter: PAYLOAD } }, levels)] }] })
    assert.ok(!JSON.stringify(opt).includes('onerror'), String(levels))
  }
  assert.ok(MAX_OPTION_DEPTH >= 32)
})

test('functions and ordinary nested data are kept', () => {
  const fn = () => 'x'
  const opt = scrubRenderedHtml({ tooltip: { formatter: fn }, series: [{ data: [nest({ name: 'leaf' }, 10)] }] })
  assert.equal(opt.tooltip.formatter, fn)
  assert.deepEqual(opt.series[0].data[0], nest({ name: 'leaf' }, 10))
})
