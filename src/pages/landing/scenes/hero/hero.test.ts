import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from '../../stage/kit'
import { presetState } from '../../stage/story'
import { TONE, type Beat, type Layout } from '../../stage/types'
import { DAYS, REV_K, ORD_K, kpis } from '../../stage/data'
import hero from './index'
import { HERO_INITIAL, KPI_DELTA, heroBoard, heroBoardCamera, heroBoardLayout, heroInside, heroUi } from './layouts'

const layoutOf = (b: number, patch: object = {}): Layout => (hero.beats[b] as Beat<any>).layout(kit, { ...presetState(hero, b), ...patch })
const accentGroups = (l: Layout) => new Set(l.cells.filter(c => c.tone === TONE.ACCENT).map(c => c.group))

test('every beat stays within the 3,400-cell budget', () => {
  for (let b = 0; b < hero.beats.length; b++) {
    const n = layoutOf(b).cells.length
    assert.ok(n <= 3400, `${hero.beats[b].id}: ${n} cells`)
    assert.ok(n >= 1200, `${hero.beats[b].id}: ${n} cells is too sparse for the board`)
  }
})

test('one accent focus: the selected day (bar + line point), plus the favicon and status dot', () => {
  for (const day of [0, 8, 13]) {
    const g = accentGroups(layoutOf(0, { day, live: true }))
    assert.deepEqual([...g].sort(), [`bar-${day}`, `line-${day}`, 'status', 'win-tab'].sort())
  }
})

test('the board carries the build-in order; an interaction variant does not (no tile flips on a day change)', () => {
  const built = heroBoard(HERO_INITIAL)
  assert.ok(built.cells.every(c => c.order != null && c.order >= 0 && c.order <= 1))
  assert.ok(built.cells.every(c => c.kind != null))
  const live = heroBoard({ day: 4, live: true })
  assert.ok(live.cells.every(c => c.kind == null))
})

test('build phases follow the storyboard: frame, then screen, then KPIs, bars, line; accent bar lands last', () => {
  const b = heroBoard(HERO_INITIAL)
  const maxOf = (pred: (g: string) => boolean) => Math.max(...b.cells.filter(c => pred(c.group)).map(c => c.order!))
  const minOf = (pred: (g: string) => boolean) => Math.min(...b.cells.filter(c => pred(c.group)).map(c => c.order!))
  assert.ok(maxOf(g => g === 'win') < minOf(g => g === 'win-screen') + 0.2)
  assert.ok(minOf(g => g.startsWith('kpi-')) > minOf(g => g === 'win-screen'))
  assert.ok(minOf(g => /^bar-\d+$/.test(g)) > minOf(g => g.startsWith('kpi-')))
  const accent = b.cells.find(c => c.tone === TONE.ACCENT && /^bar-/.test(c.group))!.group
  assert.equal(minOf(g => g === accent), Math.max(...b.cells.filter(c => /^bar-\d+$/.test(c.group)).map(c => minOf(g => g === c.group))))
  // the whole build ends inside 2.6 s: order·spread + (1 − spread) ≤ 1
  assert.ok(Math.max(...b.cells.map(c => c.order!)) * 0.8 + 0.2 <= 1 + 1e-9)
})

test('the UI layer is identical on both beats (the dashboard never breaks apart)', () => {
  const ui = heroUi(HERO_INITIAL).cells.length
  const board = heroBoard(HERO_INITIAL), inside = heroInside(HERO_INITIAL)
  const pick = (l: Layout) => l.cells.filter(c => !c.group.startsWith('pane-') && !c.group.startsWith('engine') && !c.group.startsWith('data')).map(c => c.p.join(','))
  assert.equal(pick(board).length, ui)
  assert.deepEqual(pick(board), pick(inside))
})

test('engine and data layers persist across beats (same groups) so they slide out from behind the tab', () => {
  const groups = (l: Layout) => new Set(l.cells.map(c => c.group))
  const b = groups(heroBoard()), i = groups(heroInside())
  for (const g of ['pane-engine', 'engine', 'pane-data']) { assert.ok(b.has(g), g); assert.ok(i.has(g), g) }
  assert.ok(!b.has('engine-pins') && i.has('engine-pins'))
})

test('the parked layers are fully behind the window on the board beat', () => {
  const b = heroBoard()
  for (const c of b.cells.filter(c => c.group.startsWith('pane-') || c.group === 'engine')) {
    const s = c.s ?? [1, 1, 1]
    assert.ok(c.p[2] + s[2] / 2 < -0.5, 'behind the back plate')
    assert.ok(Math.abs(c.p[0]) + s[0] / 2 <= 26 && c.p[1] + s[1] / 2 <= 34, 'inside the window outline')
  }
})

test('every number shown is derived from stage/data', () => {
  const vals = kpis().map(k => k.value)
  assert.deepEqual(vals, ['5.05M', '79.5k', '63.46', '3:44'])
  assert.equal(REV_K.length, DAYS)
  assert.ok(Math.abs(KPI_DELTA.revenue - (2546 / 2499 - 1)) < 1e-9)
  const ctl = hero.controls[0]
  assert.equal(ctl.announce!({ day: 13 }), 'Day 14: revenue $428k, 6.8k orders')
  assert.equal(ctl.get({ day: 13 }), 14)
  assert.deepEqual(ctl.set({ day: 13 }, 9), { day: 8, live: true })
  const tip = hero.pick!.hover!('bar-8', { day: 13 })!
  assert.equal(tip.text, 'Day 9 · $348K')
  assert.equal(hero.pick!.hover!('line-8', { day: 13 })!.sub, `${(ORD_K[8] * 1000).toLocaleString('en-US')} orders`)
  assert.equal(hero.pick!.hover!('kpi-revenue', { day: 13 })!.sub, '$5,045,000')
  assert.deepEqual(hero.pick!.click!('bar-3', { day: 13 }), { day: 3, live: true })
})

test('poster exports are pure and composed', () => {
  const l = heroBoardLayout()
  assert.ok(l.cells.length > 1200 && l.cells.every(c => c.kind == null))
  assert.equal(heroBoardCamera.zoom > 0, true)
})

test('copy and labels fit the contract', () => {
  for (const b of hero.beats) assert.ok(b.caption.split(' ').length <= 6, b.caption)
  for (const c of hero.callouts) if (c.short) assert.ok(c.short.length <= 12, c.id)
  for (const b of hero.beats) assert.ok(hero.callouts.filter(c => c.beats.includes(b.id)).length <= 3)
})
