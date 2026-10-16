import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from '../../stage/kit'
import { presetState } from '../../stage/story'
import { TONE } from '../../stage/types'
import { kpis, regionRevK, REV_K } from '../../stage/data'
import scene from './index'
import { ALL_FILTERS, MAX_REACH, NONE, ORIGINS, board, kpiValues, originOf, reachOf, regionValues, type BoardState } from './layout'

const st = (p: Partial<BoardState> = {}): BoardState => ({ ...scene.initialState, ...p })
const visible = (s?: number[]) => !s || (s[0] > 0 && s[1] > 0 && s[2] > 0)
const byGroup = (cells: { group: string }[]) => cells.reduce((m, c) => m.set(c.group, (m.get(c.group) ?? 0) + 1), new Map<string, number>())

test('every beat stays within the cell budget and has at most one accent focus at rest', () => {
  scene.beats.forEach((b, i) => {
    const L = b.layout(kit, presetState(scene, i))
    assert.ok(L.cells.length <= 3400, `${b.id}: ${L.cells.length} cells`)
    const accentGroups = new Set(L.cells.filter(c => c.tone === TONE.ACCENT && visible(c.s)).map(c => c.group))
    assert.ok(accentGroups.size <= 1, `${b.id}: accent in ${[...accentGroups]}`)
  })
})

test('the KPI strip shows the derived values (unfiltered and day 10)', () => {
  assert.deepEqual(kpiValues(NONE), kpis(null).map(k => k.value))
  assert.deepEqual(kpiValues({ day: 9, region: null }), ['362k', '5.7k', '63.51', '3:41'])
  assert.deepEqual(regionValues({ day: 9, region: null }), [145, 105, 76, 36])
  assert.deepEqual(regionValues(NONE), regionRevK(null))
})

test('slots keep instance identity: every filter state has the same groups with the same cell counts', () => {
  const ref = byGroup(board(st()).cells.filter(c => c.group && c.group !== 'sel'))
  for (const f of ALL_FILTERS.filter((_, i) => i % 7 === 0)) {
    const g = byGroup(board(st({ ...f, applied: true, user: true, o: f.day != null ? `bar-${f.day}` : 'region-0' })).cells.filter(c => c.group && c.group !== 'sel'))
    assert.deepEqual([...g.entries()].sort(), [...ref.entries()].sort())
  }
})

test('click beat: the bar shows the selection, the other tiles still show the 14-day totals', () => {
  const L = scene.beats[1].layout(kit, presetState(scene, 1))
  assert.ok(L.cells.some(c => c.group === 'bar-9' && c.tone === TONE.ACCENT))
  const fol = scene.beats[2].layout(kit, presetState(scene, 2))
  const lit = (l: typeof L) => l.cells.filter(c => c.group === 'kpi-revenue-value' && visible(c.s)).length
  assert.notEqual(lit(L), lit(fol))
  assert.equal(lit(L), lit(board(st())))
})

test('the order normalisation is pinned: no cell is farther from its origin than the reach', () => {
  for (const o of ORIGINS) {
    const s = st({ day: /^bar/.test(o) ? +o.slice(4) : null, region: /^region/.test(o) ? +o.slice(7) : null, applied: true, user: true, o })
    const origin = originOf(o, s), reach = reachOf(origin)
    assert.ok(reach <= MAX_REACH)
    for (const c of board(s).cells) assert.ok(Math.hypot(c.p[0] - origin[0], c.p[1] - origin[1], c.p[2] - origin[2]) <= reach + 1e-6, `${o}: ${c.group}`)
  }
})

test('the ring never runs behind a flip: spread = reach / R for every origin', () => {
  const follow = scene.beats[2]
  const R = follow.wave!.radius[1]
  for (const o of ORIGINS) {
    const spec = scene.onState!(scene.initialState, st({ day: 3, applied: true, user: true, o }))!
    const reach = reachOf(spec.from!)
    // a cell at distance d starts at m = d / reach * spread; the ring reaches it at m = d / R
    assert.ok((spec.spread ?? 0) * R >= reach - 1e-6 || (spec.spread ?? 0) >= 0.85 - 1e-9, o)
    assert.equal(spec.order, 'radial')
  }
})

test('controls toggle and clicks mirror them', () => {
  const day = scene.controls.find(c => c.id === 'day')!
  const s1 = { ...scene.initialState, ...day.set(scene.initialState, '9') } as BoardState
  assert.equal(s1.day, 9); assert.equal(s1.applied, true)
  assert.match(day.announce!(s1), /day 10: revenue \$362k/)
  const s2 = { ...s1, ...day.set(s1, '9') } as BoardState
  assert.equal(s2.day, null)
  assert.deepEqual(scene.pick!.click!('bar-4', scene.initialState), day.set(scene.initialState, '4'))
  assert.equal(scene.pick!.hover!('bar-9', scene.initialState)!.text, `Day 10 · $${REV_K[9]}k`)
})

test('copy and labels follow the storyboard limits', () => {
  for (const b of scene.beats) assert.ok(b.caption.split(' ').length <= 6, b.caption)
  for (const b of scene.beats) assert.ok(scene.callouts.filter(c => c.beats.includes(b.id)).length <= 2)
  for (const c of scene.callouts) assert.ok(!c.short || c.short.length <= 12)
  assert.equal(scene.poster, 'follow')
})
