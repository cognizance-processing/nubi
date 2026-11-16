import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from '../../stage/kit'
import { presetState } from '../../stage/story'
import { TONE } from '../../stage/types'
import { viewers } from '../../stage/data'
import scene, { viewerNo, type CacheState } from './index'
import { COLS, ROWS, TRUNK, feed, pathLen, rowZ } from './geometry'

const layoutAt = (i: number, patch: Partial<CacheState> = {}) => scene.beats[i].layout(kit, { ...presetState(scene, i), ...patch })

test('every beat stays within the cell budget and has finite cells', () => {
  scene.beats.forEach((b, i) => {
    const l = layoutAt(i)
    assert.ok(l.cells.length <= 3400, `${b.id}: ${l.cells.length} cells`)
    for (const c of l.cells) for (const v of [...c.p, ...(c.s ?? [1, 1, 1])]) assert.ok(Number.isFinite(v), `${b.id}: non-finite cell`)
  })
})

test('query shows one viewer; fanout shows the whole 4 × 4 crowd', () => {
  const stations = (i: number) => new Set(layoutAt(i).cells.map(c => c.group).filter(g => /^v-?\d+-\d$/.test(g))).size
  assert.equal(stations(0), 1)
  assert.equal(stations(1), 1)
  assert.equal(stations(2), ROWS * COLS)
  assert.equal(stations(3), ROWS * COLS)
})

test('the conveyor keeps the crowd size constant (cells are recycled)', () => {
  const n = (adds: number) => layoutAt(3, { adds }).cells.length
  assert.equal(n(1), n(2))
  assert.equal(n(2), n(7))
})

test('the drawer holds the result from STORE on (accent), not before', () => {
  const drawer = (i: number) => layoutAt(i).cells.find(c => c.group === 'rack-drawer-0' && (c.s?.[0] ?? 0) > 3)!
  assert.equal(drawer(0).tone, TONE.SHEET)
  for (const i of [1, 2, 3]) assert.equal(drawer(i).tone, TONE.ACCENT)
})

test('only the IN preset carries forced choreography (interactions never re-rise the rack)', () => {
  const variant = scene.beats[0].layout(kit, { adds: 1, stored: true, crowd: true })
  assert.ok(!variant.cells.some(c => c.group.startsWith('rack') && c.kind), 'rack cells must not carry a kind in a variant')
  assert.ok(!variant.cells.some(c => c.kind === 'rise'), 'no forced rise in a variant')
})

test('viewer count, title and announcement are derived from the data module', () => {
  assert.equal(viewers(0), 1800)
  const title = scene.copy.title as (s: CacheState) => string
  assert.equal(title({ adds: 1, stored: true, crowd: true }), '1,900 viewers. One warehouse query.')
  const add = scene.controls[0]
  const next = { ...scene.initialState, ...add.set(scene.initialState, 0) } as CacheState
  assert.equal(add.announce!(next), '1,900 viewers, 1 warehouse query')
  assert.equal(scene.pick!.click!('v0-0', scene.initialState)?.adds, 1)
})

test('labels: at most three callouts per beat; short forms ≤ 12 chars', () => {
  for (const b of scene.beats) assert.ok(scene.callouts.filter(c => c.beats.includes(b.id)).length <= 3, b.id)
  for (const c of scene.callouts) if (c.short) assert.ok(c.short.length <= 12, c.id)
})

test('viewer numbers are stable and in range', () => {
  assert.equal(viewerNo(0, 0, 0), 1)
  for (let r = -3; r < 5; r++) for (let c = 0; c < COLS; c++) {
    const n = viewerNo(r, c, 2)
    assert.ok(n >= 1 && n <= viewers(2))
    assert.equal(n, viewerNo(r, c, 2))
  }
})

test('routes: the trunk is one straight line; every feed ends at its window', () => {
  assert.equal(TRUNK.length, 2)
  assert.ok(pathLen(TRUNK) > 15)
  for (let k = 0; k < ROWS; k++) for (let c = 0; c < COLS; c++) {
    const f = feed(k, c)
    assert.ok(Math.abs(f[f.length - 1][1] - (rowZ(k) + 0.45)) < 1e-9)
  }
})
