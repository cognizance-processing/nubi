/**
 * 04 BOARD — "Then it becomes a board" (storyboard §4.5, contact sheet H).
 * The same governed rows become a cross-filtering board. Click one day: the filter chip updates at once,
 * then a ring wavefront crosses the board and every tile flips to the new numbers as the front reaches it,
 * computed in this tab. Pure data + pure functions (3D extras live in Extras.tsx, loaded lazily).
 */
import { lazy } from 'react'
import { kit } from '../../stage/kit'
import type { Beat, CameraKey, Layout, MorphSpec, SceneModule, Vec3 } from '../../stage/types'
import { DAYS, REGIONS, REV_K, fmtMoneyK } from '../../stage/data'
import {
  MAX_REACH, NONE, board, reachOf, cursorTarget, filterLabel, kpiAnchor, kpiValues, originOf, regionValues, shown,
  type BoardState, type Filter,
} from './layout'
import { planZip } from './zip'

export type { BoardState } from './layout'

/* ───────────── timing: one radial order drives the ring and the flips ───────────── */

/**
 * The layout pins the radial order to distance / reach(origin) (two zero-size sentinels), so a cell at distance d
 * starts flipping at m = d · spread / reach. The ring's radius is R · m with one R for every origin (the engine
 * takes it from the beat); choosing spread = reach / R makes the ring reach each cell exactly when its flip
 * starts, from any clicked point: a tile never updates before the front reaches it.
 */
const SPREAD_MAX = 0.85
const R = MAX_REACH / SPREAD_MAX
const WAVE_R: [number, number] = [0, R]
const WAVE_W = 5
const followSpec = (from: Vec3): MorphSpec => ({ kind: 'scale', len: 0.9, assign: 'group', order: 'radial', from, spread: Math.min(SPREAD_MAX, reachOf(from) / R) })

/* ───────────── states ───────────── */

const initialState: BoardState = { day: null, region: null, applied: false, was: NONE, user: false, o: 'bar-9' }
const CLICK: Partial<BoardState> = { day: 9, applied: false, o: 'bar-9' }
const FOLLOW: Partial<BoardState> = { applied: true }

const S_BOARD = initialState
const S_CLICK = { ...S_BOARD, ...CLICK }
const S_FOLLOW = { ...S_CLICK, ...FOLLOW }
const BAR9 = originOf('bar-9', S_FOLLOW)

/* ───────────── rows ───────────── */

// The board row is memoised so the zip's key function can match our cells by identity.
const BOARD_ROW = board(S_BOARD)
const ZIP = planZip(BOARD_ROW)

/** The click beat (scroll): the cursor lands on day 10 at m ≈ .55; the bar presses in, then the other bars dim outward. */
function clickRow(s: BoardState): Layout {
  const P = board(s)
  const x9 = BAR9[0]
  for (const c of P.cells) {
    if (c.group === 'bar-9') c.order = 0.74
    else if (/^bar-\d+$/.test(c.group)) c.order = 0.8 + 0.2 * Math.min(1, Math.abs(c.p[0] - x9) / 18)
    else c.order = 1                                     // nothing else moves on this beat
  }
  return P
}

const cam: CameraKey = { az: -0.2, el: 0.14, target: [0, 17, 0], zoom: 0.8, mobile: { az: -0.16, el: 0.12, zoom: 0.95, target: [0, 17, 0] } }
/** Interactions replay the ring from the clicked point; on beats that have no scroll wave the ring centre is parked far away. */
const PARKED_WAVE = { centre: [0, -600, 0] as Vec3, radius: WAVE_R, width: WAVE_W }

const beats: Beat<BoardState>[] = [
  { id: 'board', caption: 'One board, six tiles', len: 0.4, camera: cam, wave: PARKED_WAVE,
    layout: (_k, s) => (s.user ? board(s) : BOARD_ROW) },
  { id: 'click', caption: 'Click a day', len: 0.2, camera: cam, state: CLICK, wave: PARKED_WAVE,
    morph: { kind: 'slide', len: 0.6, assign: 'group', spread: 0.8, order: 'x-asc' },
    layout: (_k, s) => (s.user ? board(s) : clickRow(s)) },
  { id: 'follow', caption: 'Every tile follows, in this tab', len: 0.6, camera: cam, state: FOLLOW,
    morph: followSpec(BAR9), wave: { centre: BAR9, radius: WAVE_R, width: WAVE_W },
    layout: (_k, s) => board(s) },
]

/* ───────────── interactions ───────────── */

/** A change from a click or chip: tiles show the new filter; `was` is what they showed before. */
function change(s: BoardState, f: Filter, o: string): Partial<BoardState> {
  return { day: f.day, region: f.region, applied: true, was: shown(s), user: true, o }
}
const toggleDay = (s: BoardState, d: number) => change(s, { day: s.day === d ? null : d, region: s.region }, `bar-${d}`)
const toggleRegion = (s: BoardState, r: number) => change(s, { day: s.day, region: s.region === r ? null : r }, `region-${r}`)

function announce(s: BoardState): string {
  const f = shown(s)
  if (f.day == null && f.region == null) return 'Filter cleared: all 14 days, all regions. Every tile updated.'
  return `Filtered to ${filterLabel(f).replace(' · ', ', ').replace('Day', 'day')}: revenue $${kpiValues(f)[0]}. Every tile updated in this tab.`
}

/* ───────────── the module ───────────── */

const boardScene: SceneModule<BoardState> = {
  id: 'board', index: '04', kicker: 'BOARDS',
  copy: {
    title: 'Then it becomes a board.',
    body: 'Cross-filtering dashboards with 17 chart types, built in the editor or by an agent. Click a bar and every tile follows, computed in this tab.',
    facts: [['17', 'chart types'], ['1', 'tag to embed'], ['15', 'MCP tools']],
    code: '<nubi-dashboard dashboard-id="store-performance"\n                get-token="getEmbedToken">',
    try: 'Click a bar to filter the board to that day, or pick a day. Pick it again to clear.',
    spec: { href: '/docs/embedding' },
  },
  summary: 'A dashboard where selecting one day updates every tile.',
  initialState,
  in: { kind: 'slide', len: 1.2, assign: 'rank', spread: 0.6, order: ZIP.key },
  beats,
  callouts: [
    { id: 'embed', anchor: [21, 33.2, 0.6], text: 'Embedded in your app', sub: '<nubi-dashboard> · one tag', short: 'YOUR APP', side: 'ne', lead: 26, beats: ['board'] },
    { id: 'sel', anchor: { group: 'sel', at: 'top' }, tone: 'accent', side: 'n', lead: 24, priority: 2, beats: ['follow', 'board'],
      text: (s: BoardState) => (s.applied ? filterLabel(shown(s)) : `Day ${(s.day ?? 9) + 1}`),
      sub: (s: BoardState) => (s.applied ? 'filtered in this tab · 0 server calls' : 'click: filter every tile'),
      short: 'DAY 10' },
  ],
  surface: kit.surface(BOARD_ROW.texts, ['board', 'click', 'follow']),
  controls: [
    { id: 'day', kind: 'chips', label: 'Day', options: Array.from({ length: DAYS }, (_, i) => ({ value: String(i), label: String(i + 1) })),
      get: s => (s.day == null ? '' : String(s.day)), set: (s, v) => toggleDay(s, +v), announce },
    { id: 'region', kind: 'chips', label: 'Region', options: REGIONS.map((r, i) => ({ value: String(i), label: r })),
      get: s => (s.region == null ? '' : String(s.region)), set: (s, v) => toggleRegion(s, +v), announce },
  ],
  pick: {
    hover(g, s) {
      const f = shown(s)
      const b = /^bar-(\d+)$/.exec(g)
      if (b) { const d = +b[1]; return { anchor: originOf(g, s), text: `Day ${d + 1} · $${fmtMoneyK(REV_K[d])}`, sub: s.day === d ? 'click again to clear' : 'click to filter every tile' } }
      const r = /^region-(\d+)/.exec(g)
      if (r) { const k = +r[1]; return { anchor: originOf(`region-${k}`, s), text: `${REGIONS[k]} · $${fmtMoneyK(regionValues(f)[k])}`, sub: s.region === k ? 'click again to clear' : 'click to filter by region' } }
      const k = /^kpi-([a-z]+)/.exec(g)
      if (k) {
        const i = ['revenue', 'orders', 'ticket', 'fulfil'].indexOf(k[1])
        const label = ['Revenue', 'Orders', 'Avg ticket', 'Fulfilment'][i]
        return { anchor: kpiAnchor(i), text: `${label} · ${kpiValues(f)[i]}`, sub: filterLabel(f) }
      }
      return null
    },
    click(g, s) {
      const b = /^bar-(\d+)$/.exec(g)
      if (b) return toggleDay(s, +b[1])
      const r = /^region-(\d+)$/.exec(g)
      if (r) return toggleRegion(s, +r[1])
      return null
    },
  },
  // the ring and the flips share one radial order from the clicked point (900 ms, time-based)
  onState: (_prev, next) => ({ ...followSpec(originOf(next.o, next)), len: 900 }),
  Extras: lazy(() => import('./Extras')),
  poster: 'follow',
}

export const CURSOR_TARGET = cursorTarget(9)
export default boardScene
