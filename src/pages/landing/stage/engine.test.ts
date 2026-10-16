/** Story timeline, assignment and camera: the pure parts of the stage engine. */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { activeSceneAt, compileStory, currentBeatAt, labelT, quantise, resolve } from './story'
import { assignLayout, ATLAS_W, GroupIds, N_INSTANCES, ROWS_PER_LAYOUT, writeRow } from './assign'
import { makeView, project, rayAt, rayBox } from './camera'
import hero from '../scenes/hero/index'
import columns from '../scenes/columns/index'
import cache from '../scenes/cache/index'
import security from '../scenes/security/index'
import board from '../scenes/board/index'
import agents from '../scenes/agents/index'
import type { Layout } from './types'

const SCENES = [hero, columns, cache, security, board, agents]

test('story compiles: labels at rest points, ~27 beats, monotone segments', () => {
  const st = compileStory(SCENES)
  assert.equal(st.labels.length, SCENES.reduce((n, s) => n + s.beats.length, 0))
  assert.equal(st.labels[0].name, 'hero.board')
  assert.equal(st.labels[0].T, 0)
  for (let i = 1; i < st.segs.length; i++) assert.ok(Math.abs(st.segs[i].t0 - st.segs[i - 1].t1) < 1e-9)
  assert.ok(st.total > 15 && st.total < 40, `total ${st.total}`)
  const tInside = labelT(st, 'hero', 'inside')!
  assert.equal(tInside, hero.beats[0].len + (hero.beats[1].morph?.len ?? 0.6))
  const r = resolve(st, tInside - 0.5)
  assert.equal(r.seg.kind, 'morph'); assert.ok(Math.abs(r.m - 0.5) < 1e-9)
  assert.equal(resolve(st, tInside).atRest, true)
})

test('active scene switches at the midpoint of its `in`; beat highlight 0.15 early; quantise', () => {
  const st = compileStory(SCENES)
  const span = st.spans[1]
  assert.equal(activeSceneAt(st, span.activeAt - 0.01), 0)
  assert.equal(activeSceneAt(st, span.activeAt + 0.01), 1)
  const tSplit = labelT(st, 'columns', 'split')!
  assert.equal(currentBeatAt(st, 1, tSplit - 0.1), 1)
  assert.equal(currentBeatAt(st, 1, tSplit - 0.3), 0)
  assert.equal(quantise(st, tSplit + 0.2), tSplit)
})

test('every layout stays within the cell budget (≤ 3,400 visible)', () => {
  const st = compileStory(SCENES)
  for (const r of st.rows.slice(1)) assert.ok(r.layout.cells.length <= 3400, `${r.label}: ${r.layout.cells.length}`)
})

test('assignment: group match keeps instances, the rest rise/sink, deterministic', () => {
  const A: Layout = { cells: [{ p: [0, 0, 0], tone: 0, group: 'a' }, { p: [5, 0, 0], tone: 0, group: 'b' }] }
  const B: Layout = { cells: [{ p: [1, 0, 0], tone: 1, group: 'b' }, { p: [9, 9, 9], tone: 1, group: 'c' }, { p: [2, 0, 0], tone: 1, group: 'a' }] }
  const ia = assignLayout(null, A, null, 8)
  const ib = assignLayout({ cells: A.cells, inst2cell: ia }, B, null, 8)
  assert.equal(ib[0], 2)          // instance of 'a' → 'a'
  assert.equal(ib[1], 0)          // instance of 'b' → 'b'
  assert.ok([...ib].includes(1))  // 'c' got a parked instance
  assert.deepEqual([...assignLayout({ cells: A.cells, inst2cell: ia }, B, null, 8)], [...ib])
  // a cell whose own kind is 'rise' is never matched
  const C: Layout = { cells: [{ p: [0, 0, 0], tone: 0, group: 'a', kind: 'rise' }] }
  const ic = assignLayout({ cells: A.cells, inst2cell: ia }, C, null, 8)
  assert.notEqual(ic[0], 0); assert.ok([...ic].includes(0))
})

test('atlas row: 3 texels per instance, parked = scale 0, sinking keep their pose', () => {
  const A: Layout = { cells: [{ p: [1, 2, 3], tone: 2, group: 'g', yaw: 0.5 }] }
  const ia = assignLayout(null, A, null, N_INSTANCES)
  const ib = assignLayout({ cells: A.cells, inst2cell: ia }, { cells: [] }, null, N_INSTANCES)
  const data = new Float32Array(ATLAS_W * ROWS_PER_LAYOUT * 4 * 2)
  const ids = new GroupIds()
  writeRow(data, 0, { cells: A.cells, inst2cell: ia }, null, null, g => ids.id('x', g))
  assert.deepEqual([...data.slice(0, 12)].map(v => +v.toFixed(3)), [1, 2, 3, 2, 0.95, 0.95, 0.95, 1, 0.5, 0, 0, 1])
  writeRow(data, 1, { cells: [], inst2cell: ib }, { cells: A.cells, inst2cell: ia }, null, g => ids.id('x', g))
  const o = ROWS_PER_LAYOUT * ATLAS_W * 4
  assert.deepEqual([...data.slice(o, o + 8)], [1, 2, 3, 2, 0, 0, 0, 0])
  assert.equal(data[o + 11], 2)  // sink
})

test('camera: the target projects to the centre of the safe rect; picking hits a box', () => {
  const vp = { w: 1440, h: 900, mobile: false, safe: { x0: 620, y0: 80, x1: 1384, y1: 868 } }
  const v = makeView({ az: -0.36, el: 0.2, target: [0, 15.5, 0], zoom: 0.86 }, vp)
  const p = project(v, [0, 15.5, 0])
  assert.ok(Math.abs(p[0] - 1002) < 1e-6 && Math.abs(p[1] - 474) < 1e-6)
  const q = project(v, [3, 4, 5])
  const r = rayAt(v, q[0], q[1])
  assert.ok(rayBox(r.o, r.d, [2.5, 3.5, 4.5], [3.5, 4.5, 5.5]) > 0)
  assert.equal(rayBox(r.o, r.d, [20, 20, 20], [21, 21, 21]), -1)
})

test('director: ambient stops within 5 s of the last input; reduced motion quantises T and disables ambient', async () => {
  const { Director } = await import('./director')
  const st = compileStory(SCENES)
  const d = new Director(st)
  const real = performance.now.bind(performance)
  let offset = 0
  ;(performance as any).now = () => real() + offset
  try {
    const amb = st.labels.find(l => (st.scenes[l.scene].beats[l.beat] as any).ambient?.kind && (st.scenes[l.scene].beats[l.beat] as any).ambient.kind !== 'none')
    assert.ok(amb, 'some beat declares ambient')
    d.setT(amb!.T + 0.01)
    assert.equal(d.ambientActive(), true)
    offset += 4900; assert.equal(d.ambientActive(), true)
    offset += 200; assert.equal(d.ambientActive(), false)
    d.touch(); assert.equal(d.ambientActive(), true)
    d.reduced = true
    assert.equal(d.ambientActive(), false)
    d.setT(amb!.T + 0.3)
    assert.equal(d.T, amb!.T)
  } finally { (performance as any).now = real }
})

test('director: interactions make a variant on the current beat; leaving the beat drops it', async () => {
  const { Director, isVariant } = await import('./director')
  const st = compileStory(SCENES)
  const d = new Director(st)
  const si = st.scenes.findIndex(s => s.id === 'board')
  const T = labelT(st, 'board', 'board')!
  d.setT(T)
  d.setSceneState(si, { day: 3 }, true)
  const disp = d.display()
  assert.ok(isVariant(disp.to))
  assert.equal(d.sceneState(si).day, 3)
  d.setT(labelT(st, 'board', 'follow')!)
  assert.notEqual(d.sceneState(si).day, 3)
})
