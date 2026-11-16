import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from '../../stage/kit'
import { TONE, type Cell } from '../../stage/types'
import { presetState } from '../../stage/story'
import scene, { _layoutFor } from './index'
import { INITIAL, PER_BAND, ROWS, badgeAt, regionOfRow, rowY, slotAt, type SecurityState } from './build'

const rowsOf = (cells: Cell[], side: 'a' | 'b') => {
  const out: { visible: number; ghost: number } = { visible: 0, ghost: 0 }
  for (let r = 0; r < ROWS; r++) {
    const c = cells.filter(x => x.group === `table-${side}-r${r}`)
    if (c.some(x => x.tone === TONE.ACCENT)) out.visible++
    else if (cells.some(x => x.group === `station-${side}-screen` && x.tone === TONE.GHOST && Math.abs(x.p[1] - rowY(r)) < 1e-6 && (x.s?.[0] ?? 1) > 0.1)) out.ghost++
  }
  return out
}

test('every beat is within the cube budget (~850 target, 3,400 hard cap)', () => {
  for (const [i, b] of scene.beats.entries()) {
    const l = b.layout(kit, presetState(scene, i))
    assert.ok(l.cells.length <= 1000, `${b.id}: ${l.cells.length} cells`)
  }
})

test('the table is sorted into four bands of three rows', () => {
  assert.equal(ROWS, 12)
  assert.deepEqual([0, 3, 6, 9].map(regionOfRow), ['AMER', 'EMEA', 'APAC', 'LATAM'])
  assert.equal(PER_BAND, 3)
})

test('same: both tables full; filter: one band of 3 solid rows per screen, 9 hidden lines', () => {
  const same = _layoutFor('same').cells
  assert.deepEqual(rowsOf(same, 'a'), { visible: 12, ghost: 0 })
  assert.deepEqual(rowsOf(same, 'b'), { visible: 12, ghost: 0 })
  const f = _layoutFor('filter').cells
  assert.deepEqual(rowsOf(f, 'a'), { visible: 3, ghost: 9 })
  assert.deepEqual(rowsOf(f, 'b'), { visible: 3, ghost: 9 })
  // A's visible rows are the AMER band, B's the EMEA band
  const vis = (side: string) => [...Array(ROWS).keys()].filter(r => f.some(x => x.group === `table-${side}-r${r}` && x.tone === TONE.ACCENT))
  assert.deepEqual(vis('a'), [0, 1, 2])
  assert.deepEqual(vis('b'), [3, 4, 5])
})

test('claim: the token chip docks in the query bar; before it, it hides inside the badge', () => {
  const tok = (l: { cells: Cell[] }, side: string) => l.cells.find(c => c.group === `token-${side}`)!
  assert.deepEqual(tok(_layoutFor('same'), 'a').p, badgeAt('a'))
  assert.deepEqual(tok(_layoutFor('claim'), 'a').p, slotAt('a'))
  assert.equal(tok(_layoutFor('claim'), 'a').kind, 'arc')
})

test('swap: the physical cards trade places and the rows follow', () => {
  const f = _layoutFor('filter'), s = _layoutFor('swap')
  const at = (l: { cells: Cell[] }, g: string) => l.cells.find(c => c.group === g)!.p
  assert.deepEqual(at(f, 'card-1'), badgeAt('a'))
  assert.deepEqual(at(s, 'card-1'), badgeAt('b'))
  assert.deepEqual(at(s, 'card-2'), badgeAt('a'))
  // the card landing on B arcs over, the one landing on A slides beneath it
  assert.equal(s.cells.find(c => c.group === 'card-1')!.kind, 'arc')
  assert.equal(s.cells.find(c => c.group === 'card-2')!.kind, 'slide')
  const vis = (side: string) => [...Array(ROWS).keys()].filter(r => s.cells.some(x => x.group === `table-${side}-r${r}` && x.tone === TONE.ACCENT))
  assert.deepEqual(vis('a'), [3, 4, 5])
  assert.deepEqual(vis('b'), [0, 1, 2])
})

test('no claim: every row is a hidden line, the badge is gone and the NO CLAIM anchor exists', () => {
  const l = _layoutFor('filter', { a: 'None' })
  assert.deepEqual(rowsOf(l.cells, 'a'), { visible: 0, ghost: 12 })
  assert.ok(l.cells.some(c => c.group === 'void-a'))
  assert.ok(!l.cells.some(c => c.group === 'lanyard-a'))
  assert.ok(!l.cells.some(c => c.group === 'token-a'))
})

test('static cells never carry an arc (only the token on claim, only the cards on swap)', () => {
  for (const beat of ['same', 'claim', 'filter', 'swap']) {
    const arcs = new Set(_layoutFor(beat).cells.filter(c => c.kind === 'arc' || c.kind === 'lift').map(c => c.group))
    const want = beat === 'claim' ? ['token-a', 'token-b'] : beat === 'swap' ? ['card-1'] : []
    assert.deepEqual([...arcs].sort(), want.sort(), beat)
  }
})

test('controls: swap exchanges claims and parity; chips filter; announcements read as specified', () => {
  const s: SecurityState = { ...INITIAL, claimed: true, filtered: true }
  const swap = scene.controls.find(c => c.id === 'swap')!
  const next = { ...s, ...swap.set(s, '') } as SecurityState
  assert.equal(next.a, 'EMEA'); assert.equal(next.b, 'AMER'); assert.equal(next.swapped, true)
  assert.equal(swap.announce!(next), 'Viewer A sees EMEA rows: 3 of 12. Viewer B sees AMER rows: 3 of 12.')
  const a = scene.controls.find(c => c.id === 'a')!
  const none = { ...s, ...a.set(s, 'None') } as SecurityState
  assert.equal(a.announce!(none), 'Viewer A has no claim: 0 of 12 rows')
  assert.equal(scene.pick!.click!('card-2', s)!.a, 'EMEA')
  assert.equal(scene.pick!.click!('table-a-r0', s), null)
})

test('labels: at most 3 callouts per beat in the preset state', () => {
  for (const b of scene.beats) {
    const n = scene.callouts.filter(c => c.beats.includes(b.id) && !c.id.startsWith('none-')).length
    assert.ok(n <= 3, `${b.id}: ${n}`)
  }
})
