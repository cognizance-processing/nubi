/**
 * 02 CACHE — "1,800 viewers. One warehouse query." (storyboard §4.3).
 *
 * Story: one query reaches the warehouse once; the result is stored at the edge under a content hash and
 * every other viewer reads that copy. Objects: a database cylinder (warehouse), a server rack (edge cache)
 * and a crowd of browser windows with people (viewers), joined by circuit-board floor traces.
 *
 * Pure data + pure functions (no three, no DOM, no timers). Geometry and timings live in ./geometry.ts so
 * Extras.tsx (packets, slab, flashes) runs on exactly the same clock as the cubes.
 */
import { lazy } from 'react'
import { kit, type Part } from '../../stage/kit'
import { TONE, type Beat, type CalloutSpec, type CameraKey, type Cell, type GroupInfo, type Layout, type MorphSpec, type SceneModule, type SurfaceText, type Vec3 } from '../../stage/types'
import { CACHE_KEY, fmtInt, viewers } from '../../stage/data'
import {
  ADD, COLS, COL_X, DB_H, DB_R, FAN, FIRST, IN, MINI, PEOPLE_ROWS, PERSON_DX, PERSON_DZ, PERSON_SCALE, PADS, RACK, RACK_D, RACK_W,
  ROWS, SLAB, STAND_H, STORE, TO_DB, TRUNK, WAREHOUSE, WIN_H, WIN_W, bus, busZ, drop, feed, orderFor, pad, pathLen, rowZ, toneOrder, traceCells,
  L_UP, L_PASS, L_DB,
} from './geometry'

export interface CacheState {
  /** presses of "Add 100 viewers" (the conveyor position) */
  adds: number
  /** the result is in the edge cache */
  stored: boolean
  /** the whole crowd is present and served */
  crowd: boolean
}
export type CacheBeat = 'query' | 'store' | 'fanout' | 'add'

export { WAREHOUSE, RACK }

/* ───────────── objects ───────────── */

const stationId = (rowId: number, c: number) => `v${rowId}-${c}`

/**
 * The warehouse: the kit cylinder. During the IN its cells pour in along a helix, bottom ring first, going
 * round the ring; the cap closes last. Every cell is `helix`: cells matched to the columns' cubes spiral in
 * from the towers, any extra cells spiral up from the floor ring (hidden under the first ring once it lands).
 */
function warehouse(beat: CacheBeat, pour: boolean): Part {
  const db = kit.cylinder({ id: 'db', at: WAREHOUSE, r: DB_R, h: DB_H, n: 30, bands: [0, 3, 6] })
  // the top ring flashes accent when the query arrives (cells a little proud of the ring); afterwards they
  // shrink back inside the wall and stay hidden there
  const tw = ((2 * Math.PI * DB_R) / 30) * 1.04
  const flash = beat === 'query'
  for (let k = 0; k < 30; k++) {
    const a = (k / 30) * Math.PI * 2
    db.cells.push({
      p: [WAREHOUSE[0] + DB_R * Math.cos(a), DB_H - 0.5, WAREHOUSE[2] + DB_R * Math.sin(a)],
      s: flash ? [tw * 1.03, 1.0, 0.98] : [tw * 0.8, 0.8, 0.7], tone: flash ? TONE.ACCENT : TONE.PAPER, group: 'db-ring', yaw: -a + Math.PI / 2,
      ...(pour ? { kind: 'scale' as const, order: orderFor(IN.packet[1] - 0.01 + (k / 30) * 0.02, IN.spread) } : beat === 'store' ? { order: 0 } : {}),
    })
  }
  // the result slab: hidden in the warehouse until STORE, then filed inside the rack (behind drawer 0)
  const inRack = beat !== 'query'
  db.cells.push({
    p: inRack ? [RACK[0], 1.6 + 0.55, RACK[2] - 0.3] : [WAREHOUSE[0], SLAB[1] / 2 + 0.06, WAREHOUSE[2]], s: SLAB, tone: TONE.ACCENT, group: 'slab',
    ...(pour ? { kind: 'scale' as const, order: 1 } : beat === 'store' ? { order: orderFor(STORE.slab, STORE.spread) } : {}),
  })
  if (pour) {
    const [p0, p1] = IN.pour
    for (const c of db.cells) {
      if (c.group !== 'db') continue
      const j = Math.floor(c.p[1])
      const ang = (Math.atan2(c.p[2] - WAREHOUSE[2], c.p[0] - WAREHOUSE[0]) + Math.PI) / (Math.PI * 2)
      const isCap = c.p[1] > DB_H - 0.5 && !!c.s && c.s[1] < 0.6
      const f = isCap ? 0.92 + 0.08 * (1 - Math.hypot(c.p[0] - WAREHOUSE[0], c.p[2] - WAREHOUSE[2]) / DB_R) : Math.min(1, (j + ang * 0.9) / DB_H) * 0.92
      c.order = orderFor(p0 + (p1 - p0) * f, IN.spread)
      c.kind = 'helix'
    }
  }
  return db
}

function rack(beat: CacheBeat, s: CacheState, forced: boolean): Part {
  const r = kit.rack({ id: 'rack', at: RACK, w: RACK_W, h: 9, d: RACK_D, stored: s.stored ? 1 : 0 })
  for (const c of r.cells) {
    if (forced) { c.kind = 'scale'; c.order = orderFor(IN.rack + (c.p[1] / 9) * 0.07, IN.spread) }
    // the LED of drawer 0 blinks accent while the query passes through the rack (cache MISS)
    if (beat === 'query' && c.group === 'rack-led-0') {
      const mPass = IN.packet[0] + (IN.packet[1] - IN.packet[0]) * (L_UP / (L_UP + L_PASS + L_DB))
      c.order = orderFor(mPass - 0.075, IN.spread)
      c.flight = TONE.ACCENT
    }
    // the drawer turns accent the moment the slab is filed
    if (beat === 'store' && (c.group === 'rack-drawer-0' || c.group === 'rack-led-0')) c.order = toneOrder(STORE.file, STORE.spread)
  }
  return r
}

interface StationOpts { slot: number; c: number; rowId: number; lit: boolean; people: boolean; beat: CacheBeat; litAt?: number; riseAt?: number; spread: number; forced?: boolean; screenExists?: boolean }

/** One viewer: a small browser window on a post, a person beside it, and (when served) a four-bar chart on the screen. */
function station(o: StationOpts): Part {
  const id = stationId(o.rowId, o.c)
  const x = COL_X[o.c], z = rowZ(o.slot)
  const back = o.slot >= PEOPLE_ROWS
  const win = kit.browserWindow({ id, at: [x, STAND_H, z], w: WIN_W, h: WIN_H, titleBar: 1, small: true, screenTone: o.lit ? TONE.ACCENT_SOFT : TONE.SCREEN, frameTone: back ? TONE.SHEET : TONE.MID })
  const post: Cell[] = [
    { p: [x, STAND_H / 2, z - 0.15], s: [0.7, STAND_H, 0.5], tone: back ? TONE.SHEET : TONE.MID, group: id },
    { p: [x, 0.07, z - 0.15], s: [2.6, 0.14, 1.3], tone: back ? TONE.SHEET : TONE.MID, group: id },
  ]
  const chart: Cell[] = []
  if (o.lit) {
    const { x0, x1, y0, zFront } = win.screen
    const bw = 0.5, gap = ((x1 - x0) - 0.9 - MINI.length * bw) / (MINI.length - 1)
    MINI.forEach((v, i) => {
      const h = 0.45 + v * 1.25
      chart.push({ p: [x0 + 0.45 + bw / 2 + i * (bw + gap), y0 + 0.3 + h / 2, zFront + 0.12], s: [bw, h, 0.22], tone: TONE.INK, group: `${id}-screen` })
    })
  }
  const person = o.people ? kit.person({ id: `${id}-p`, at: [x + PERSON_DX, 0, z + PERSON_DZ], scale: PERSON_SCALE, turn: -0.3 }) : null
  const P = kit.merge(win, post, chart, person)
  // timing: the station prints bottom-up (cubes scale in layer by layer), then its screen flips on when served
  const lit = o.litAt != null ? orderFor(o.litAt, o.spread) : undefined
  for (const c of P.cells) {
    const isScreen = c.group === `${id}-screen`
    // a new screen flips on; an existing one only changes tone (a flip would show it rotated before its turn); bars scale in
    if (isScreen && o.litAt != null) {
      const bars = c.tone === TONE.INK
      c.order = bars ? orderFor(o.litAt + (o.screenExists ? -0.06 : 0.04), o.spread) : o.screenExists ? toneOrder(o.litAt, o.spread) : lit!
      c.kind = bars ? 'scale' : o.screenExists ? undefined : 'flip'
    }
    else if (o.riseAt != null) { c.kind = 'scale'; c.order = orderFor(o.riseAt + o.c * 0.012 + (c.p[1] / (STAND_H + WIN_H)) * 0.06, o.spread) }
  }
  P.groups[id] = { hover: true, click: true, liftOnHover: [0, 0.5, 0] }
  P.groups[`${id}-screen`] = {}
  if (o.people) P.groups[`${id}-p`] = { hover: true, click: true, liftOnHover: [0, 0.4, 0] }
  return P
}

/* ───────────── traces ───────────── */

const SPINE_X0 = COL_X[0]
/** bus 0 to the right of the first viewer's drop (the first feed already covers the left part) */
const busRest = (k: number) => {
  const b = bus(k)
  return k === 0 ? [[SPINE_X0 - 1.2, b[0][1]], b[1]] as [number, number][] : b
}

function traces(beat: CacheBeat, s: CacheState, forced: boolean): Cell[] {
  const out: Cell[] = []
  const first = feed(FIRST[0], FIRST[1])
  const Lf = pathLen(first), Lt = pathLen(TRUNK)
  const total = L_UP + L_PASS + L_DB
  const qm = (d: number) => IN.packet[0] + (IN.packet[1] - IN.packet[0]) * d / total       // m at which the query passes distance d
  // idle (dashed) — the first route and the trunk draw in after the rack rises
  const idleOrder = (frac: number) => orderFor(IN.idle[0] + (IN.idle[1] - IN.idle[0]) * frac, IN.spread)
  const kind = forced ? 'scale' as const : undefined
  out.push(...traceCells(first, { lit: false, orderAt: forced ? d => idleOrder(d / Lf) : undefined, kind }))
  out.push(...traceCells(TRUNK, { lit: false, orderAt: forced ? d => idleOrder(d / Lt) : undefined, kind }))
  out.push(pad(PADS.db, false, forced ? { kind, order: idleOrder(1) } : {}), pad(PADS.rackBack, false, forced ? { kind, order: idleOrder(0) } : {}))
  // lit by the query: station → rack (first feed, reversed distance) and rack → warehouse (trunk, reversed)
  out.push(...traceCells(first, { lit: true, orderAt: forced ? d => orderFor(qm(Lf - d), IN.spread) : undefined, kind }))
  out.push(...traceCells(TRUNK, { lit: true, orderAt: forced ? d => orderFor(qm(L_UP + L_PASS + (Lt - d)), IN.spread) : undefined, kind }))
  out.push(pad(PADS.rackFront, true, forced ? { kind, order: orderFor(qm(L_UP), IN.spread) } : {}))
  out.push(pad([COL_X[FIRST[1]], rowZ(FIRST[0]) + 0.45], true, forced ? { kind, order: orderFor(qm(0), IN.spread) } : {}))
  if (!s.crowd) return out

  // the crowd's circuit: rows light from the spine outward, front row first (FAN timings)
  const fanning = beat === 'fanout' && s.adds === 0                 // the scroll preset; +100 variants use the conveyor
  for (let k = 0; k < ROWS; k++) {
    const start = FAN.row0 + k * FAN.rowStep
    const b = busRest(k)
    const L = pathLen(b), x0 = b[0][0]
    const litM = (x: number) => start + FAN.bus[0] + (FAN.bus[1] - FAN.bus[0]) * Math.max(0, x - x0) / Math.max(1, L)
    const fk = fanning ? 'scale' as const : undefined
    out.push(...traceCells(b, { lit: false, kind: fk, orderAt: fanning ? d => orderFor(start + 0.04 * d / L, FAN.spread) : undefined }))
    out.push(...traceCells(b, { lit: true, kind: fk, orderAt: fanning ? d => orderFor(litM(x0 + d), FAN.spread) : undefined }))
    for (let c = 0; c < COLS; c++) {
      if (k === FIRST[0] && c === FIRST[1]) continue
      const dr = drop(k, c)
      const at = litM(COL_X[c]) + 0.01
      out.push(...traceCells(dr, { lit: false, kind: fk, orderAt: fanning ? () => orderFor(start + 0.04, FAN.spread) : undefined }))
      out.push(...traceCells(dr, { lit: true, kind: fk, orderAt: fanning ? () => orderFor(at, FAN.spread) : undefined }))
      out.push(pad(dr[1], true, fanning ? { kind: fk, order: orderFor(at + 0.01, FAN.spread) } : {}))
    }
  }
  return out
}

/* ───────────── the scene ───────────── */

/** m (in the FAN morph) at which window (slot k, column c) is served */
export const fanServeM = (k: number, c: number) => {
  const start = FAN.row0 + k * FAN.rowStep
  const b = busRest(k), L = pathLen(b), x0 = b[0][0]
  return start + FAN.bus[0] + (FAN.bus[1] - FAN.bus[0]) * Math.max(0, COL_X[c] - x0) / Math.max(1, L) + 0.02
}

function crowd(beat: CacheBeat, s: CacheState): Part[] {
  const parts: Part[] = []
  for (let k = 0; k < ROWS; k++) for (let c = 0; c < COLS; c++) {
    const rowId = s.adds - k
    const fan = beat === 'fanout' && s.adds === 0 && !(k === FIRST[0] && c === FIRST[1])
    const isNew = !(beat === 'fanout' && s.adds === 0) && s.adds > 0 && k === 0
    parts.push(station({
      slot: k, c, rowId, lit: true, people: k < PEOPLE_ROWS, beat,
      spread: fan ? FAN.spread : ADD.spread,
      riseAt: fan ? FAN.row0 + k * FAN.rowStep + FAN.rise : isNew ? 0.25 : undefined,
      litAt: fan ? fanServeM(k, c) : isNew ? ADD.flip + c * 0.03 : undefined,
    }))
  }
  return parts
}

function scene(beat: CacheBeat, s: CacheState): Layout {
  // the IN pours into the warehouse; only the preset frame of the first beat carries that choreography
  const forced = beat === 'query' && !s.stored && !s.crowd
  const parts: Array<Part | Cell[]> = [warehouse(beat, forced), rack(beat, s, forced), traces(beat, s, forced)]
  if (s.crowd) parts.push(...crowd(beat, s))
  else {
    const storeServe = beat === 'store' ? STORE.flip : undefined
    parts.push(station({
      slot: FIRST[0], c: FIRST[1], rowId: s.adds, lit: s.stored, people: true, beat, forced,
      spread: forced ? IN.spread : STORE.spread,
      riseAt: forced ? IN.station : undefined,
      litAt: storeServe, screenExists: true,
    }))
  }
  const P = kit.merge(...parts)
  const groups: Record<string, GroupInfo> = { ...P.groups, db: { hover: false }, rack: { hover: false }, 'db-ring': {}, slab: {} }
  for (const g of Object.keys(groups)) if (/^rack-drawer-/.test(g)) groups[g] = {}
  return { cells: P.cells, groups }
}

/* ───────────── camera ───────────── */

const CAM: CameraKey = { az: -0.3, el: 0.55, target: [12.1, 4.6, 24], zoom: 0.84, mobile: { el: 0.58, zoom: 0.92, target: [11.6, 4, 24] } }
/** query/store: closer on the one viewer → cache → warehouse line; fanout pulls back to reveal the crowd */
const CAM_Q: CameraKey = { az: -0.32, el: 0.55, target: [4.6, 4, 21], zoom: 0.98, mobile: { el: 0.58, zoom: 1.05, target: [4.2, 3.5, 21] } }

/* ───────────── beats ───────────── */

const beats: Beat<CacheState>[] = [
  { id: 'query', caption: 'One query reaches the warehouse', len: 0.4, camera: CAM_Q, layout: (_k, s) => scene('query', s) },
  { id: 'store', caption: 'The result is stored by its hash', len: 0.4, camera: CAM_Q, state: { stored: true },
    morph: { kind: 'slide', len: 0.7, spread: STORE.spread, order: 'x-asc' }, layout: (_k, s) => scene('store', s) },
  { id: 'fanout', caption: 'Every viewer reads the edge copy', len: 0.4, camera: CAM, state: { stored: true, crowd: true },
    morph: { kind: 'rise', len: 1.0, spread: FAN.spread, order: 'z-desc' }, layout: (_k, s) => scene('fanout', s), ambient: { kind: 'packets' } },
  { id: 'add', caption: 'Add 100 viewers: still one query', len: 0.5, camera: CAM, state: { adds: 1 },
    morph: { kind: 'slide', len: 0.6, spread: ADD.spread, order: 'z-asc', assign: 'group' }, layout: (_k, s) => scene('add', s) },
]

/** IN from columns `read`: the towers drain along a helix into the warehouse rings, bottom-up. */
const IN_MORPH: MorphSpec = {
  kind: 'helix', len: 1.4, spread: IN.spread, assign: 'rank', toneAt: 0.8,
  helix: { centre: WAREHOUSE, radius: DB_R, turns: 1 },
  // rank key: warehouse cells by their arrival order; the towers give their top layers first. Everything else
  // the IN builds late (rack, station, traces, the top ring, the slab) sorts last, so no column cube is paired
  // with it and left standing in the old picture until its late cue.
  order: (c: Cell) => (c.group === 'db' ? (c.order ?? 0) : c.group === '' || /^(rack|v-?\d|db-ring|slab)/.test(c.group) ? 10 + (c.order ?? 0) : -c.p[1] * 100 + Math.hypot(c.p[0] - WAREHOUSE[0], c.p[2] - WAREHOUSE[2])),
}

/* ───────────── labels ───────────── */

const ALL: CacheBeat[] = ['query', 'store', 'fanout', 'add']
const DRAWER0_Y = 1.6 + 0.55, DRAWER_Z = RACK[2] + RACK_D / 2 - 0.25 + 0.02
const viewersText = (s: CacheState) => `${fmtInt(viewers(s.adds))} viewers`
export const VIEWERS_ANCHOR: Vec3 = [COL_X[COLS - 1] + 1.2, 0, busZ(0)]

const callouts: CalloutSpec[] = [
  { id: 'wh', anchor: { group: 'db', at: 'top' }, text: 'Warehouse', sub: 'queries: 1', short: 'DB · 1 QUERY', side: 'ne', lead: 26, priority: 3, beats: ['query', 'store', 'fanout'] },
  { id: 'wh-add', anchor: { group: 'db', at: 'top' }, text: 'Warehouse', sub: 'queries: 1 · still', short: 'DB · 1 QUERY', tone: 'accent', side: 'ne', lead: 26, priority: 3, beats: ['add'] },
  { id: 'edge', anchor: { group: 'rack', at: 'top' }, text: 'Edge cache', sub: `key ${CACHE_KEY}`, short: 'CACHE', tone: 'accent', side: 'ne', lead: 24, priority: 2, beats: ['store', 'fanout'] },
  { id: 'edge-add', anchor: { group: 'rack', at: 'top' }, text: 'Edge cache', sub: `key ${CACHE_KEY}`, short: 'CACHE', side: 'ne', lead: 24, priority: 2, beats: ['add'] },
  { id: 'viewers', anchor: VIEWERS_ANCHOR, text: viewersText, sub: 'each reads the edge copy', side: 's', lead: 30, priority: 1, beats: ['fanout', 'add'] },
  { id: 'viewer-1', anchor: { group: stationId(0, FIRST[1]), at: 'top' }, text: 'Viewer 1', sub: 'cache miss · asks once', short: 'VIEWER 1', side: 'ne', lead: 22, priority: 1, beats: ['query'] },
]

const surface: SurfaceText[] = [
  { id: 'miss', at: [RACK[0] - 1.25, DRAWER0_Y - 0.34, DRAWER_Z + 0.3], text: 'MISS', font: 'mono-strong', size: 0.66, tone: 'accent', beats: ['query'] },
  { id: 'key', at: [RACK[0], DRAWER0_Y - 0.26, DRAWER_Z + 0.3], text: CACHE_KEY.replace('sha256:', ''), font: 'mono-strong', size: 0.56, tone: 'onInk', align: 'centre', beats: ['store', 'fanout', 'add'] },
]

/* ───────────── interactions ───────────── */

const ADD_PATCH = (s: CacheState): Partial<CacheState> => ({ adds: s.adds + 1, stored: true, crowd: true })

/** A stable, plausible viewer number for a window. */
export function viewerNo(rowId: number, c: number, adds: number): number {
  const total = viewers(adds)
  if (rowId === 0 && c === FIRST[1]) return 1
  const h = Math.abs(((rowId + 31) * 2654435761 + c * 40503) | 0)
  return 2 + (h % (total - 1))
}

const cache: SceneModule<CacheState> = {
  id: 'cache', index: '02', kicker: 'CACHE',
  copy: {
    title: s => `${fmtInt(viewers(s.adds))} viewers. One warehouse query.`,
    body: 'Every result is stored under a content hash. Viewers who ask the same question with the same permissions share one result, and the engine in each tab does the rest.',
    facts: [['1', 'warehouse query'], ['sha256', 'content-hash key'], ['0', 'repeat queries']],
    code: 'key = sha256(canonical_json({sql, params, rls_claims}))',
    try: 'Click the crowd to add 100 viewers, or',
    spec: { href: '/docs/cache-key-spec' },
  },
  summary: 'One warehouse query feeds an edge cache that serves many viewers.',
  initialState: { adds: 0, stored: false, crowd: false },
  in: IN_MORPH,
  beats,
  callouts,
  surface,
  controls: [
    { id: 'add', kind: 'button', label: 'Add 100 viewers', get: s => s.adds, set: s => ADD_PATCH(s),
      announce: s => `${fmtInt(viewers(s.adds))} viewers, 1 warehouse query` },
  ],
  pick: {
    hover: (g, s) => {
      const m = /^v(-?\d+)-(\d)(-p)?$/.exec(g)
      if (!m) return null
      const rowId = +m[1], c = +m[2], k = s.crowd ? s.adds - rowId : 0
      const n = viewerNo(rowId, c, s.adds)
      return { anchor: [COL_X[c], STAND_H + WIN_H + 0.3, rowZ(k)], text: `Viewer ${fmtInt(n)}`, sub: n === 1 ? 'asked the warehouse once' : 'read from the edge · 0 warehouse queries' }
    },
    click: (g, s) => (/^v-?\d+-\d/.test(g) ? ADD_PATCH(s) : null),
  },
  onState: (prev, next) => (!prev.crowd && next.crowd
    ? { kind: 'scale', len: 900, spread: 0.6, order: 'z-desc' }
    : { kind: 'slide', len: 700, spread: ADD.spread, order: 'z-asc', assign: 'group' }),
  Extras: lazy(() => import('./Extras')),
  poster: 'fanout',
}
export default cache

/** exported for Extras/tests */
export const BEAT_LEN = { in: IN_MORPH.len, beats: beats.map(b => ({ id: b.id, morph: b.morph?.len ?? 0, len: b.len })) }
export const ROUTES = { TRUNK, TO_DB, feed, bus, busZ }
