/**
 * 02 CACHE geometry (storyboard §4.3). Pure TypeScript shared by the scene module (layouts) and Extras
 * (packet routes, timings). No three, no DOM, no randomness.
 *
 * World plan (y up, +z toward the viewer, camera front-left):
 *
 *        ⌸ warehouse (back-left)
 *          └─ trunk ─┐
 *                 ▣ rack (edge cache) ── spine runs +z down the crowd's left edge
 *                    ├── bus 3 ─ ▭ ▭ ▭ ▭      back rows: no people, receding
 *                    ├── bus 2 ─ ▭ ▭ ▭ ▭
 *                    ├── bus 1 ─ ☺▭ ☺▭ ☺▭ ☺▭
 *                    └── bus 0 ─ ☺▭ ☺▭ ☺▭ ☺▭   front row; station (0,0) is the first viewer
 *
 * Traces are made of the stage's own cubes (thin floor slabs), so the circuit renders in the poster,
 * reduced motion and WebGL alike, and its light front is driven by per-cell `order` (the engine's morph).
 * Idle traces are INK dashes; lit traces are solid ACCENT slabs laid just above them.
 */
import { REV_K } from '../../stage/data'
import { TONE, type Cell, type Vec3 } from '../../stage/types'

/* ───────────── placement ───────────── */

export const ROWS = 4
export const COLS = 4
/** window centre x per column */
export const COL_X = [7.8, 14.8, 21.8, 28.8]
/** row z (window plane) per conveyor slot k (0 = front) */
export const ROW_PITCH = 7.6
export const rowZ = (k: number) => 52 - k * ROW_PITCH
/** The window stands on a post, so the person reads as standing at a screen. */
export const STAND_H = 1
export const WIN_W = 6
export const WIN_H = 4.4
export const PERSON_DX = -3.75
export const PERSON_DZ = 1.25
export const PERSON_SCALE = 0.52
/** rows with people (front two) */
export const PEOPLE_ROWS = 2

export const WAREHOUSE: Vec3 = [0, 0, -9]
export const DB_R = 5.2
export const DB_H = 9
export const RACK: Vec3 = [0, 0, 22]
export const RACK_W = 7
export const RACK_D = 4
/** the spine runs along +z at this x, from the rack's front pad to the front bus */
export const SPINE_X = 1.4
/** bus z for slot k: in front of the windows, behind the next row's people */
export const busZ = (k: number) => rowZ(k) + 2.7
export const FIRST: [number, number] = [0, 0]           // [slot, column] of the first viewer

/* ───────────── trace routing (floor polylines, y = 0) ───────────── */

type P2 = [number, number]                              // [x, z]

/** 45° chamfer at every corner (PCB style); c = chamfer leg. */
export function chamfer2(pts: P2[], c = 1.2): P2[] {
  if (pts.length < 3) return pts
  const out: P2[] = [pts[0]]
  for (let i = 1; i < pts.length - 1; i++) {
    const a = pts[i - 1], b = pts[i], n = pts[i + 1]
    const la = Math.hypot(b[0] - a[0], b[1] - a[1]), ln = Math.hypot(n[0] - b[0], n[1] - b[1])
    const k1 = Math.min(c, la / 2) / (la || 1), k2 = Math.min(c, ln / 2) / (ln || 1)
    out.push([b[0] + (a[0] - b[0]) * k1, b[1] + (a[1] - b[1]) * k1])
    out.push([b[0] + (n[0] - b[0]) * k2, b[1] + (n[1] - b[1]) * k2])
  }
  out.push(pts[pts.length - 1])
  return out
}

export const pathLen = (p: P2[]) => p.slice(1).reduce((s, q, i) => s + Math.hypot(q[0] - p[i][0], q[1] - p[i][1]), 0)

/** Point at arc-length fraction t of a 2D route, lifted to height y. */
export function at3(p: P2[], t: number, y = 0): Vec3 {
  const L = pathLen(p)
  let d = Math.min(1, Math.max(0, t)) * L
  for (let i = 1; i < p.length; i++) {
    const l = Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1])
    if (d <= l || i === p.length - 1) {
      const u = l > 0 ? Math.min(1, d / l) : 0
      return [p[i - 1][0] + (p[i][0] - p[i - 1][0]) * u, y, p[i - 1][1] + (p[i][1] - p[i - 1][1]) * u]
    }
    d -= l
  }
  return [p[0][0], y, p[0][1]]
}

const DB_PORT: P2 = [WAREHOUSE[0], WAREHOUSE[2] + DB_R + 0.8]                 // front of the cylinder
const RACK_BACK: P2 = [RACK[0], RACK[2] - RACK_D / 2 - 0.7]                  // pad behind the rack
const RACK_FRONT: P2 = [SPINE_X, RACK[2] + RACK_D / 2 + 0.8]                 // pad in front of the rack

/** warehouse → rack (enters the rack from behind). */
export const TRUNK: P2[] = [DB_PORT, RACK_BACK]
/** the spine: rack front → down the crowd's left edge to the front bus. */
export const SPINE: P2[] = [RACK_FRONT, [SPINE_X, busZ(0)]]
/** bus for slot k: from the spine to the last window. */
export const bus = (k: number): P2[] => [[SPINE_X, busZ(k)], [COL_X[COLS - 1] + 1.2, busZ(k)]]
/** drop from bus k to the stand of window c. */
export const drop = (k: number, c: number): P2[] => [[COL_X[c], busZ(k)], [COL_X[c], rowZ(k) + 0.45]]

/** spine length from the rack pad down to the junction of bus k */
const spineTo = (k: number) => busZ(k) - RACK_FRONT[1]

/** The full route from the rack's front pad to window (k, c): spine → bus k → drop. */
export function feed(k: number, c: number): P2[] {
  return chamfer2([RACK_FRONT, [SPINE_X, busZ(k)], [COL_X[c], busZ(k)], [COL_X[c], rowZ(k) + 0.45]], 1.2)
}
/** The query's way back: rack front → through the rack → trunk reversed → warehouse. */
export const RACK_PASS: P2[] = [RACK_FRONT, [RACK[0] + 0.6, RACK_FRONT[1]], [RACK[0] + 0.6, RACK_BACK[1]], RACK_BACK]
export const TO_DB: P2[] = [...TRUNK].reverse()
/** station (0,0) → rack (the query going up). */
export const QUERY_UP: P2[] = [...feed(FIRST[0], FIRST[1])].reverse()
export const L_UP = pathLen(QUERY_UP), L_PASS = pathLen(RACK_PASS), L_DB = pathLen(TO_DB)

/* ───────────── timings (fractions of a morph's m), shared by layouts and Extras ───────────── */

/** IN (columns → query): pour, then the rack and the first station rise, then the query packet travels. */
export const IN = {
  spread: 0.85,                                          // each cell travels for (1 − spread) of m
  pour: [0, 0.6] as P2,                                  // start window of the pour (by layer)
  rack: 0.58, station: 0.62, idle: [0.63, 0.7] as P2,   // starts (m)
  packet: [0.72, 0.93] as P2,                            // the query packet: station → rack → warehouse
}
/** where along the packet journey (0..1) it passes the rack and arrives */
export const Q_RACK = L_UP / (L_UP + L_PASS + L_DB)
export const Q_PASS_END = (L_UP + L_PASS) / (L_UP + L_PASS + L_DB)
/** STORE: the result slab rides back and is filed; then it is served to the first viewer. */
/** STORE: the result slab rides the trunk back and lifts into the rack (a layout cell, so it follows the straight
 *  warehouse→rack trace); the drawer lights; then a packet serves viewer 1, whose screen lights. */
export const STORE = { spread: 0.5, slab: 0.05, file: 0.62, serve: [0.64, 0.84] as P2, flip: 0.88 }
/** FANOUT: rows rise from the front, each row's bus lights from the spine, drops light, screens flip. */
export const FAN = { spread: 0.8, row0: 0.0, rowStep: 0.16, rise: 0.0, bus: [0.06, 0.16] as P2, drop: 0.17, flip: 0.2 }
/** ADD (conveyor): rows slide back, the new front row rises, a packet serves it. */
export const ADD = { spread: 0.5, packet: [0.45, 0.9] as P2, flip: 0.8 }

/** order value whose cell STARTS its morph at m (e = 0 at m = order · spread) */
export const orderFor = (m: number, spread: number) => m / spread
/** order value whose cell switches tone (toneAt .6) at m */
export const toneOrder = (m: number, spread: number) => (m - 0.6 * (1 - spread)) / spread
/** the result slab (an Arrow batch): 2.2 wide, .9 tall, 3.6 long along the trunk */
export const SLAB: Vec3 = [2.2, 0.9, 3.6]

/* ───────────── trace cells ───────────── */

export const TRACE_W = 0.26
const IDLE_H = 0.05, LIT_H = 0.08

/**
 * Cells along a 2D route. Idle: INK dashes (length .48 at pitch 1). Lit: solid ACCENT slabs (pitch-long, raised).
 * `orderAt(d)` gives each piece's order from its distance along the route (light-front timing).
 */
export function traceCells(route: P2[], o: { lit: boolean; orderAt?: (d: number) => number; kind?: Cell['kind'] }): Cell[] {
  const out: Cell[] = []
  let d0 = 0
  for (let i = 1; i < route.length; i++) {
    const a = route[i - 1], b = route[i]
    const L = Math.hypot(b[0] - a[0], b[1] - a[1])
    if (L < 1e-3) continue
    const n = Math.max(1, Math.round(L / 1))
    const step = L / n, yaw = Math.atan2(-(b[1] - a[1]), b[0] - a[0])
    for (let j = 0; j < n; j++) {
      const u = (j + 0.5) / n
      const p: Vec3 = [a[0] + (b[0] - a[0]) * u, o.lit ? LIT_H / 2 + 0.005 : IDLE_H / 2, a[1] + (b[1] - a[1]) * u]
      const len = o.lit ? step + 0.04 : step * 0.48
      const c: Cell = { p, s: [len, o.lit ? LIT_H : IDLE_H, o.lit ? TRACE_W : TRACE_W * 0.8], tone: o.lit ? TONE.ACCENT : TONE.INK, group: '', yaw }
      if (o.orderAt) c.order = o.orderAt(d0 + (j + 0.5) * step)
      if (o.kind) c.kind = o.kind
      out.push(c)
    }
    d0 += L
  }
  return out
}

/** A square pad (via) at a route end. */
export const pad = (p: P2, lit: boolean, extra?: Partial<Cell>): Cell =>
  ({ p: [p[0], lit ? 0.05 : 0.035, p[1]], s: [0.75, lit ? 0.1 : 0.07, 0.75], tone: lit ? TONE.ACCENT : TONE.INK, group: '', ...extra })

export const PADS = { db: DB_PORT, rackBack: RACK_BACK, rackFront: RACK_FRONT }
export { spineTo }

/* ───────────── station chart (the board each viewer sees) ───────────── */

/** Four bars from the shared revenue series, normalised to the screen (0..1). */
export const MINI = [REV_K[2], REV_K[5], REV_K[9], REV_K[13]].map(v => (v - 250) / (Math.max(...REV_K) - 250))
