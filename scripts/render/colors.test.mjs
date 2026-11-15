// Server-side renders write widget colours into SVG attributes, so a colour
// must be a colour.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { safeColor, safePalette } from './colors.mjs'

test('only colour values survive', () => {
  for (const ok of ['#fff', '#12ab34', '#12ab34ff', 'rgb(1, 2, 3)', 'rgba(1,2,3,0.5)', 'hsl(120, 50%, 50%)', 'red', 'transparent']) {
    assert.equal(safeColor(ok), ok)
  }
  for (const bad of ['"/><script>alert(1)</script>', 'red" onload="x', 'url(https://evil.example/x)', 'expression(alert(1))', '#12', '', null, 5, {}]) {
    assert.equal(safeColor(bad), undefined, String(bad))
  }
})

test('a palette keeps its valid entries and falls back when none are', () => {
  const fallback = ['#111111', '#222222']
  assert.deepEqual(safePalette(['#abcdef', '"><script>', 'blue'], fallback), ['#abcdef', 'blue'])
  assert.deepEqual(safePalette(['"><script>'], fallback), fallback)
  assert.deepEqual(safePalette('nope', fallback), fallback)
})
