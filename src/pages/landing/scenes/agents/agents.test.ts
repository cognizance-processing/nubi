import { test } from 'node:test'
import assert from 'node:assert/strict'
import { kit } from '../../stage/kit'
import { AGENT } from '../../stage/data'
import { TONE, type Beat, type Cell } from '../../stage/types'
import { normalisedOrder } from '../../stage/kit/order'
import scene from './index'
import { CALLS, PRESET, ZW, arriveT, typed, visibleAt, type AgentsState, type BeatId } from './timeline'
import { KPI_MGN, KPI_REV, MARGINS, REV, REV_TOTAL, agentsLayout } from './layout'
import { PROMPT, PROMPT_LINES, TEXT_DEFS } from './texts'
import { frameAt } from './packets'

const preset = (b: BeatId): AgentsState => ({ ...PRESET[b], replay: 0 })
const rowOf = (i: number) => { const b = scene.beats[i] as Beat<AgentsState>; return b.layout(kit, preset(b.id as BeatId)) }

test('agents: every beat within budget, deterministic, ~400 → ~1,000 cells', () => {
  scene.beats.forEach((b, i) => {
    const l = rowOf(i)
    assert.ok(l.cells.length <= 3400 && l.cells.length >= 300, `${b.id}: ${l.cells.length}`)
    assert.deepEqual(JSON.stringify(rowOf(i).cells), JSON.stringify(l.cells))
  })
  assert.ok(rowOf(3).cells.length > rowOf(0).cells.length, 'the scene grows as the board builds')
})

test('agents: numbers are derived from stage/data', () => {
  assert.equal(Math.round(REV_TOTAL * 100) / 100, AGENT.revenueQ3)           // the KPI equals the sum of the bars
  assert.equal(KPI_REV, '4.12M'); assert.equal(KPI_MGN, '31.8%')
  const wmean = MARGINS.reduce((s, m, i) => s + m * REV[i], 0) / REV_TOTAL
  assert.ok(Math.abs(wmean - AGENT.marginPct) < 0.05, `weighted margin ${wmean}`)
  assert.equal(MARGINS.length, AGENT.byCountry.length)
  assert.match(CALLS[2].text, /6 rows/)
  assert.equal(PROMPT, 'Build a board for the EMEA review: revenue by country for Q3, margin next to it.')
})

test('agents: one accent focus per beat (the focused call, or the live tag)', () => {
  scene.beats.forEach((b, i) => {
    const s = preset(b.id as BeatId)
    const accent = new Set(rowOf(i).cells.filter(c => c.tone === TONE.ACCENT).map(c => c.group))
    const ok = new Set([`ag-row-${s.call}`, `ag-sock-${s.call}`, 'ag-focus', 'ag-live'])
    for (const g of accent) assert.ok(ok.has(g), `${b.id}: unexpected accent group ${g}`)
  })
})

test('agents: per-cell timing only on preset rows, all orders in 0..1', () => {
  scene.beats.forEach((b, i) => {
    for (const c of rowOf(i).cells) if (c.order != null) assert.ok(c.order >= 0 && c.order <= 1, `${b.id} ${c.group} ${c.order}`)
  })
  const variant = agentsLayout({ step: 6, call: 0, replay: 0 }, 'live')
  assert.ok(variant.cells.every(c => c.order == null && c.kind == null && c.flight == null), 'variants morph with onState, not scroll timing')
})

test('agents: scroll morph keys span exactly 0..1 so sinking cells keep their designed start', () => {
  for (let i = 1; i < scene.beats.length; i++) {
    const spec = (scene.beats[i] as Beat<AgentsState>).morph!
    const key = spec.order as (c: Cell) => number
    const probe = [...rowOf(i - 1).cells, ...rowOf(i).cells]
    const raw = probe.map(key)
    assert.equal(Math.min(...raw), 0); assert.ok(Math.max(...raw) > 0.999, `${scene.beats[i].id}: max ${Math.max(...raw)}`)
    assert.equal(normalisedOrder(probe, spec.order!).length, probe.length)
  }
})

test('agents: tiles build in order KPI, KPI, bars, table', () => {
  const build = rowOf(2).cells
  const start = (test: (g: string) => boolean) => Math.min(...build.filter(c => test(c.group)).map(c => c.order ?? 1))
  const s = [start(g => g.startsWith('ag-kpi-rev')), start(g => g.startsWith('ag-kpi-mgn')), start(g => g.startsWith('ag-bar')), start(g => g.startsWith('ag-tbl'))]
  for (let k = 1; k < 4; k++) assert.ok(s[k] > s[k - 1], `tile ${k} starts after tile ${k - 1}: ${s}`)
  assert.ok(arriveT(3) + 0.09 <= 0.85 + 1e-9, 'the last tile completes inside the morph')
})

test('agents: typed text reveals inside its window and is complete at rest', () => {
  const t = typed('get_context', 0.3, 0.5)
  assert.equal(visibleAt(t, 0.28), '')
  assert.equal(visibleAt(t, 0.52), 'get_context')
  assert.ok(visibleAt(t, 0.4).length > 0 && visibleAt(t, 0.4).length < 11)
  assert.equal(t.split(ZW).join(''), 'get_context')                           // at rest the label layer shows it whole
  assert.equal(typed('', 0, 1), '')
  for (const d of TEXT_DEFS) assert.ok(d.win[0] >= 0 && d.win[1] <= 0.87 && d.win[0] < d.win[1], d.id)
  assert.equal(new Set(scene.surface.map(s => s.id)).size, scene.surface.length)
  assert.equal(PROMPT_LINES.length, 3)
})

test('agents: packets follow the calls; reduced motion shows the composed still', () => {
  const s = preset('tools')
  const mid = frameAt('tools', 0.04 + 2 * 0.235 + 0.09, s, 0, false)
  assert.ok(mid.packets.some(p => p.label === 'query_metric'))
  const still = frameAt('tools', 0.4, s, 0, true)
  assert.equal(still.packets.length, 1); assert.equal(still.packets[0].label, CALLS[s.call].id)
  assert.equal(frameAt('live', 1, preset('live'), 0, false).packets.length, 0)
  assert.ok(frameAt('build', 0.5, preset('build'), 0, false).packets.some(p => p.label === 'upsert_dashboard'))
})

test('agents: copy, controls and callouts', () => {
  assert.equal(scene.copy.title, 'Agents lay out the board. SQL supplies the numbers.')
  assert.equal(scene.beats.map(b => b.caption).join(' · '), 'You ask in chat · The agent calls Nubi’s tools · The board builds, tile by tile · Promoted to live')
  const chips = scene.controls.find(c => c.kind === 'chips')!
  assert.deepEqual(chips.options!.map(o => o.label), ['get_context', 'list_metrics', 'query_metric', 'validate_spec', 'upsert_dashboard', 'promote'])
  assert.deepEqual(chips.set(preset('live'), '2'), { call: 2, step: 3 })
  for (const b of scene.beats) assert.ok(scene.callouts.filter(c => c.beats.includes(b.id)).length <= 3)
  for (const c of scene.callouts) for (const s of c.short ? [c.short] : []) assert.ok(s.length <= 12, s)
  const replay = scene.controls.find(c => c.id === 'replay')!
  assert.deepEqual(replay.set(preset('live'), 0), { replay: 1 })
  assert.equal(scene.onState!(preset('live'), { ...preset('live'), replay: 1 })!.kind, 'cut')
})
