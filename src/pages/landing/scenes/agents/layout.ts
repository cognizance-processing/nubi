/**
 * 05 AGENTS: geometry (pure). A chat panel on the left, the MCP server in the middle, the board in a browser
 * window on the right, all made of the stage's cubes. One function lays out any state; `phase` adds the per-cell
 * timing (order, kind, flight flash) for the scroll morph INTO that beat, and only on the beat's own preset state,
 * so interaction variants morph with the scene's onState spec instead.
 *
 *            chat (14×26)          MCP (5×15)            board window (26×22)            [ LIVE · /d/emea-review ]
 *   x  −24 ……………… −10       −7 …… −2           1.5 ……………………………………………… 27.5
 *   z        +3                    0                         −3
 * Traces run in the air at y = TY: chat → MCP → board, and MCP (top) → the tag for `promote`.
 */
import { kit, type Part } from '../../stage/kit'
import { TONE, type Cell, type Vec3 } from '../../stage/types'
import { AGENT } from '../../stage/data'
import {
  BUILD, CALLS, IN, LIVE, QUERY_TOOLS, SPREAD, TOOL_LEG, arriveT, isPreset, orderAt, toolT,
  type AgentsState, type BeatId,
} from './timeline'

/* ───────────── placement ───────────── */

export const CHAT = { cx: -17, z: 3, w: 14, h: 26 }
export const TOWER = { cx: -4.5, z: 0, w: 5, h: 15, d: 4 }
export const WIN = { cx: 14.5, z: -3, w: 26, h: 22 }
export const TY = 13.2                                   // height of the air traces (the gap between the board's tile rows)

const CX0 = CHAT.cx - CHAT.w / 2, CX1 = CHAT.cx + CHAT.w / 2
const CZ = CHAT.z
const TX0 = TOWER.cx - TOWER.w / 2, TX1 = TOWER.cx + TOWER.w / 2, TZF = TOWER.z + TOWER.d / 2
const WX0 = WIN.cx - WIN.w / 2, WX1 = WIN.cx + WIN.w / 2

/* the board's screen and its four slots (2 × 2: KPI, KPI / bars, table) */
const SCR = { x0: WX0 + 1, x1: WX1 - 1, y0: 1, y1: WIN.h - 3, z: WIN.z + 0.4 }
const MARGIN = 0.8, GAP = 0.8
const COL_W = (SCR.x1 - SCR.x0 - 2 * MARGIN - GAP) / 2
const KPI_H = 4.6
const COLX = [SCR.x0 + MARGIN, SCR.x0 + MARGIN + COL_W + GAP]
const KPI_TOP = SCR.y1 - MARGIN
const LOW_TOP = KPI_TOP - KPI_H - GAP
const LOW_Y0 = SCR.y0 + MARGIN
export interface Slot { id: string; x0: number; y0: number; w: number; h: number }
export const SLOTS: Slot[] = [
  { id: 'rev', x0: COLX[0], y0: KPI_TOP - KPI_H, w: COL_W, h: KPI_H },
  { id: 'mgn', x0: COLX[1], y0: KPI_TOP - KPI_H, w: COL_W, h: KPI_H },
  { id: 'bars', x0: COLX[0], y0: LOW_Y0, w: COL_W, h: LOW_TOP - LOW_Y0 },
  { id: 'tbl', x0: COLX[1], y0: LOW_Y0, w: COL_W, h: LOW_TOP - LOW_Y0 },
]
export const slotCentre = (k: number): Vec3 => { const s = SLOTS[k]; return [s.x0 + s.w / 2, s.y0 + s.h / 2, SCR.z + 0.7] }

/* ───────────── traces and packet paths (used by Extras.tsx) ───────────── */

const PZ = SCR.z + 0.7                                   // packets fly just in front of the board's screen
/** chat → MCP */
export const ROUTE_A: Vec3[] = [[CX1, TY, CZ], [CX1 + 1.6, TY, CZ], [CX1 + 1.6, TY, TOWER.z], [TX0 - 0.5, TY, TOWER.z]]
/** MCP → board (ends on the window's front-left frame) */
export const ROUTE_B: Vec3[] = [[TX1 + 0.5, TY, TOWER.z], [WX0 + 0.5, TY, TOWER.z], [WX0 + 0.5, TY, PZ]]
/** MCP (top) → the draft/live tag above the board's top-right corner */
export const TAG = { x0: WX1 - 13, x1: WX1, y0: WIN.h + 0.15, h: 1.8, z: WIN.z }
const PROMOTE_Y = TAG.y0 + TAG.h + 1.6
export const ROUTE_P: Vec3[] = [[TOWER.cx, TOWER.h + 0.5, TOWER.z], [TOWER.cx, PROMOTE_Y, TOWER.z], [TOWER.cx, PROMOTE_Y, WIN.z], [TAG.x0 + 2, PROMOTE_Y, WIN.z], [TAG.x0 + 2, TAG.y0 + TAG.h + 0.1, WIN.z]]
/** MCP → slot k: route B, then along the gap between the tile rows to the slot's centre */
export const routeTo = (k: number): Vec3[] => { const c = slotCentre(k); return [...ROUTE_B, [c[0], TY, PZ], [c[0], c[1], PZ]] }

/* ───────────── data (derived from stage/data.ts) ───────────── */

const sum = (a: number[]) => a.reduce((s, v) => s + v, 0)
export const REV = AGENT.byCountry.map(c => c[1])
export const REV_TOTAL = sum(REV)                        // = AGENT.revenueQ3 (tested)
export const KPI_REV = `${AGENT.revenueQ3.toFixed(2)}M`
export const KPI_MGN = `${AGENT.marginPct.toFixed(1)}%`
const CODE: Record<string, string> = { Germany: 'DE', 'United Kingdom': 'UK', France: 'FR', Spain: 'ES', Italy: 'IT', Netherlands: 'NL' }
export const codeOf = (name: string) => CODE[name] ?? name.slice(0, 2).toUpperCase()
/** Margin by country: seeded spread around the board margin, re-centred so the revenue-weighted mean is exactly AGENT.marginPct. */
export const MARGINS: number[] = (() => {
  const r = kit.rng(kit.hashStr('agents/margin'))
  const raw = REV.map(() => (r() - 0.5) * 9)
  const bias = sum(raw.map((d, i) => d * REV[i])) / REV_TOTAL
  return raw.map(d => Math.round((AGENT.marginPct + d - bias) * 10) / 10)
})()

/* ───────────── objects ───────────── */

const P0 = (): Part => ({ cells: [], groups: {}, anchors: {}, texts: [] })
const put = (P: Part, p: Vec3, s: Vec3, tone: number, group: string, extra?: Partial<Cell>) => { P.cells.push({ p, s, tone, group, ...extra }); return P }

/** Rows of the chat log (tool calls), centre y of row k. */
export const rowY = (k: number) => 17.2 - k * 1.85
export const BUBBLE = { x0: CX0 + 1.4, x1: CX1 - 0.6, y0: 19.2, y1: 23.2 }
export const REPLY = { x0: CX0 + 1.4, x1: CX0 + 10.6, y: 5.5, h: 1.6 }

function chat(s: AgentsState): Part {
  const P = P0()
  for (let i = 0; i < CHAT.w; i += 2) for (let j = 0; j < CHAT.h; j += 2) {
    put(P, [CX0 + i + 1, j + 1, CZ], [1.94, 1.94, 0.6], j >= CHAT.h - 2 ? TONE.MID : TONE.SCREEN, 'ag-chat')
  }
  // your request: an ink bubble with a tail, right-aligned like every chat client
  put(P, [(BUBBLE.x0 + BUBBLE.x1) / 2, (BUBBLE.y0 + BUBBLE.y1) / 2, CZ + 0.5], [BUBBLE.x1 - BUBBLE.x0, BUBBLE.y1 - BUBBLE.y0, 0.35], TONE.INK, 'ag-ask')
  put(P, [BUBBLE.x1 - 0.55, BUBBLE.y0 - 0.3, CZ + 0.5], [0.7, 0.6, 0.35], TONE.INK, 'ag-ask')
  // the agent's tool calls, one row each (mark + slab), as collapsed tool-use blocks
  for (let k = 0; k < CALLS.length; k++) {
    if (s.step <= k) continue
    const y = rowY(k), g = `ag-row-${k}`
    put(P, [CX0 + 1.0, y, CZ + 0.5], [0.45, 1.15, 0.3], k === s.call ? TONE.ACCENT : TONE.INK, g)
    put(P, [(CX0 + 1.5 + CX1 - 0.6) / 2, y, CZ + 0.45], [CX1 - 0.6 - CX0 - 1.5, 1.35, 0.25], TONE.SHEET, g)
  }
  // the agent's reply once the board is live
  if (s.step >= 6) put(P, [(REPLY.x0 + REPLY.x1) / 2, REPLY.y, CZ + 0.45], [REPLY.x1 - REPLY.x0, REPLY.h, 0.3], TONE.SHEET, 'ag-reply')
  // input bar: slab, caret, send button
  put(P, [CHAT.cx, 2.2, CZ + 0.45], [CHAT.w - 2, 1.9, 0.3], TONE.SHEET, 'ag-input')
  put(P, [CX0 + 1.75, 2.2, CZ + 0.65], [0.18, 1.0, 0.2], TONE.INK, 'ag-input')
  put(P, [CX1 - 1.9, 2.2, CZ + 0.62], [1.2, 1.2, 0.4], TONE.INK, 'ag-input')
  P.groups['ag-chat'] = {}
  P.groups['ag-ask'] = { hover: true }
  for (let k = 0; k < CALLS.length; k++) P.groups[`ag-row-${k}`] = { hover: true, click: true, liftOnHover: [0, 0, 0.5] }
  return P
}

/** Socket (column, row) of each of the 15 tools: reading order from the top-left. Calls 0..5 use the first six. */
export const socketPos = (i: number): Vec3 => [TOWER.cx + ((i % 3) - 1) * 1.35, 9.1 - Math.floor(i / 3) * 1.6, TZF + 0.12]

function tower(s: AgentsState): Part {
  const P = P0()
  for (let i = 0; i < TOWER.w; i++) for (let j = 0; j < TOWER.h; j++) put(P, [TX0 + i + 0.5, j + 0.5, TOWER.z], [0.96, 0.96, TOWER.d], TONE.INK, 'ag-mcp')
  // jacks where the traces plug in (left, right, top)
  put(P, [TX0 - 0.25, TY, TOWER.z], [0.5, 1.0, 1.0], TONE.MID, 'ag-mcp')
  put(P, [TX1 + 0.25, TY, TOWER.z], [0.5, 1.0, 1.0], TONE.MID, 'ag-mcp')
  put(P, [TOWER.cx, TOWER.h + 0.25, TOWER.z], [1.0, 0.5, 1.0], TONE.MID, 'ag-mcp')
  // fifteen tool sockets; a used tool stays lit (soft), the focused call is the accent
  for (let i = 0; i < 15; i++) {
    const tone = i === s.call ? TONE.ACCENT : i < s.step ? TONE.ACCENT_SOFT : TONE.PAPER
    put(P, socketPos(i), [0.8, 0.8, 0.3], tone, `ag-sock-${i}`)
  }
  // a ring marks the focused call's socket (and anchors its callout)
  if (s.call >= 0) {
    const [x, y, z] = socketPos(s.call)
    put(P, [x, y + 0.6, z], [1.25, 0.12, 0.3], TONE.ACCENT, 'ag-focus')
    put(P, [x, y - 0.6, z], [1.25, 0.12, 0.3], TONE.ACCENT, 'ag-focus')
    put(P, [x - 0.6, y, z], [0.12, 1.08, 0.3], TONE.ACCENT, 'ag-focus')
    put(P, [x + 0.6, y, z], [0.12, 1.08, 0.3], TONE.ACCENT, 'ag-focus')
  }
  // callout anchors: specks hidden inside the server's foot (the dot lands on its front edge). `ag-call` exists only
  // while a call is in focus, so the call callout appears and disappears with the state.
  const foot: Vec3 = [TOWER.cx + 0.6, 0.06, TZF - 0.03]
  put(P, foot, [0.02, 0.02, 0.02], TONE.INK, 'ag-foot')
  if (s.call >= 0) put(P, foot, [0.02, 0.02, 0.02], TONE.INK, 'ag-call')
  P.groups['ag-mcp'] = { hover: true }
  P.groups['ag-focus'] = {}
  P.anchors.mcpTop = [TOWER.cx, TOWER.h, TOWER.z]
  return P
}

function plate(P: Part, sl: Slot, group: string) {
  const W = Math.round(sl.w), H = Math.round(sl.h), sx = sl.w / W, sy = sl.h / H
  for (let i = 0; i < W; i++) for (let j = 0; j < H; j++) put(P, [sl.x0 + (i + 0.5) * sx, sl.y0 + (j + 0.5) * sy, SCR.z + 0.2], [0.96 * sx, 0.96 * sy, 0.4], TONE.SHEET, group)
}

/** An empty slot: a hidden-line card (opaque, dashed edge) with a small plus, the editor's "add a tile" mark. */
function slotOutline(k: number): Part {
  const sl = SLOTS[k], g = `ag-slot-${k}`, P = P0(), z = SCR.z + 0.12
  const cx = sl.x0 + sl.w / 2, cy = sl.y0 + sl.h / 2
  put(P, [cx, cy, z], [sl.w - 0.3, sl.h - 0.3, 0.16], TONE.GHOST, g)
  put(P, [cx, cy, z + 0.12], [1.5, 0.2, 0.12], TONE.MID, g)
  put(P, [cx, cy, z + 0.12], [0.2, 1.5, 0.12], TONE.MID, g)
  P.groups[g] = {}
  return P
}

/** Tile k of the board (the governed queries the agent arranged). */
function tile(k: number): Part {
  const sl = SLOTS[k]
  if (k < 2) {
    const id = k === 0 ? 'ag-kpi-rev' : 'ag-kpi-mgn'
    const value = k === 0 ? KPI_REV : KPI_MGN
    // shrink the numerals until they fit inside the plate (0.8 inset each side)
    const px = Math.min(0.5, (sl.w - 1.6) / kit.measureVoxelText(value, 1))
    const P = kit.kpiTile({ id, at: [sl.x0, sl.y0 + sl.h, SCR.z], w: sl.w, h: sl.h, value, caption: '', px })
    P.texts = []
    return P
  }
  const P = P0()
  if (k === 2) {
    plate(P, sl, 'ag-bars')
    const max = Math.max(...REV)
    const B = kit.barChart({ id: 'ag-bar', at: [sl.x0 + 0.9, sl.y0 + 2.0, SCR.z + 0.4], values: REV, max, height: 6.8, barW: 1.1, gap: 0.55, depth: 1.0 })
    P.cells.push(...B.cells); Object.assign(P.groups, B.groups); Object.assign(P.anchors, B.anchors)
    P.groups['ag-bars'] = { hover: true }
  } else {
    plate(P, sl, 'ag-tbl')
    const yt = sl.y0 + sl.h
    for (let i = 0; i < Math.round(sl.w - 1.2); i++) put(P, [sl.x0 + 0.6 + i + 0.5, yt - 2.3, SCR.z + 0.45], [0.95, 0.9, 0.45], TONE.INK, 'ag-tbl-head')
    MARGINS.forEach((mg, r) => {
      const y = tableRowY(r), n = Math.round((mg / 40) * 9)
      for (let i = 0; i < n; i++) put(P, [sl.x0 + 3.2 + i * 0.5 + 0.25, y, SCR.z + 0.45], [0.42, 0.7, 0.5], TONE.INK, `ag-tbl-r${r}`)
      P.groups[`ag-tbl-r${r}`] = { hover: true }
    })
    P.groups['ag-tbl'] = { hover: true }
  }
  return P
}
export const tableRowY = (r: number) => SLOTS[3].y0 + SLOTS[3].h - 3.55 - r * 1.3

function board(s: AgentsState): Part {
  const win = kit.browserWindow({ id: 'ag-win', at: [WIN.cx, 0, WIN.z], w: WIN.w, h: WIN.h, tiles: 2 })
  const parts: Part[] = [win]
  for (let k = 0; k < SLOTS.length; k++) {
    if (s.step >= 5) parts.push(tile(k))
    else parts.push(slotOutline(k))
  }
  // the publish tag above the top-right corner: DRAFT (sheet) until promoted, then LIVE (accent, wider)
  const T = P0()
  const live = s.step >= 6
  const x0 = live ? TAG.x0 : TAG.x1 - 5
  for (let x = x0; x < TAG.x1 - 1e-6; x += 1) put(T, [x + 0.5, TAG.y0 + TAG.h / 2, TAG.z], [0.97, TAG.h, 0.6], live ? TONE.ACCENT : TONE.SHEET, live ? 'ag-live' : 'ag-draft')
  T.groups['ag-live'] = { hover: true }
  T.anchors.liveTop = [(TAG.x0 + TAG.x1) / 2, TAG.y0 + TAG.h, TAG.z]
  return kit.merge(...parts, T)
}

/* ───────────── per-beat timing (the scroll morph INTO each beat) ───────────── */

const span = (cells: Cell[], key: (c: Cell) => number) => { let lo = Infinity, hi = -Infinity; for (const c of cells) { const v = key(c); if (v < lo) lo = v; if (v > hi) hi = v } return (c: Cell) => (hi > lo ? (key(c) - lo) / (hi - lo) : 0) }
/** Give `cells` starts spread over [a, b] (by `key`, 0..1) in a segment with `spread`. */
function startIn(cells: Cell[], a: number, b: number, spread: number, key: (c: Cell) => number, extra?: Partial<Cell>) {
  if (!cells.length) return
  const f = span(cells, key)
  for (const c of cells) { c.order = orderAt(a + (b - a) * f(c), spread); if (extra) Object.assign(c, extra) }
}
const by = (cells: Cell[], test: (g: string) => boolean) => cells.filter(c => test(c.group))
const isSocket = (g: string, i: number) => g === `ag-sock-${i}`

function decorate(cells: Cell[], phase: BeatId) {
  if (phase === 'ask') {
    const sp = SPREAD.in
    startIn(by(cells, g => g === 'ag-win' || g === 'ag-win-tab' || g === 'ag-win-url'), IN.frame[0], IN.frame[1], sp, c => c.p[0], { kind: 'rise' })
    startIn(by(cells, g => g === 'ag-win-screen'), IN.screen[0], IN.screen[1], sp, c => -c.p[1], { kind: 'flip' })
    startIn(by(cells, g => g === 'ag-chat' || g === 'ag-input'), IN.chat[0], IN.chat[1], sp, c => c.p[1], { kind: 'flip' })
    startIn(by(cells, g => g === 'ag-mcp'), IN.tower[0], IN.tower[1], sp, c => c.p[0], { kind: 'rise' })
    startIn(by(cells, g => g.startsWith('ag-sock-')), IN.sockets[0], IN.sockets[1], sp, c => -c.p[1], { kind: 'scale' })
    startIn(by(cells, g => g.startsWith('ag-slot-')), IN.slots[0], IN.slots[1], sp, c => -c.p[1] * 0.2 + c.p[0] * 0.05, { kind: 'scale' })
    startIn(by(cells, g => g === 'ag-ask'), IN.bubble, IN.bubble + 0.02, sp, c => c.p[0], { kind: 'scale' })
    startIn(by(cells, g => g === 'ag-draft'), IN.tag, IN.tag + 0.1 * 0.7, sp, c => c.p[0], { kind: 'scale' })
    return
  }
  if (phase === 'tools') {
    const sp = SPREAD.tools
    for (let k = 0; k < QUERY_TOOLS; k++) {
      const t = toolT(k)
      startIn(by(cells, g => g === `ag-row-${k}`), t, t + 0.01, sp, c => c.p[0], { kind: 'scale', flight: TONE.ACCENT })
      startIn(by(cells, g => isSocket(g, k)), t + TOOL_LEG.socket, t + TOOL_LEG.socket, sp, () => 0, { flight: TONE.ACCENT })
    }
    // the placeholders' timing for the NEXT morph (build): each vanishes as its tile's packet lands
    for (let k = 0; k < SLOTS.length; k++) startIn(by(cells, g => g === `ag-slot-${k}`), arriveT(k) - 0.01, arriveT(k) - 0.01, SPREAD.build, () => 0)
    return
  }
  if (phase === 'build') {
    const sp = SPREAD.build
    startIn(by(cells, g => g === 'ag-row-4'), BUILD.row, BUILD.row + 0.01, sp, c => c.p[0], { kind: 'scale', flight: TONE.ACCENT })
    startIn(by(cells, g => isSocket(g, 4)), BUILD.socket, BUILD.socket, sp, () => 0, { flight: TONE.ACCENT })
    const groupsOf: ((g: string) => boolean)[] = [
      g => g.startsWith('ag-kpi-rev'), g => g.startsWith('ag-kpi-mgn'), g => g === 'ag-bars' || g.startsWith('ag-bar-'), g => g.startsWith('ag-tbl'),
    ]
    groupsOf.forEach((test, k) => {
      const a = arriveT(k), [cx, cy] = slotCentre(k)
      const own = by(cells, test)
      // plate: grows out from where the packet landed
      startIn(own.filter(c => c.tone === TONE.SHEET), a, a + 0.06, sp, c => Math.hypot(c.p[0] - cx, c.p[1] - cy), { kind: 'scale' })
      // content: numerals left → right, bars bottom-up, table rows top-down (all grow in place: a parked cell that
      // hinge-flips shows as a slat before its start, so late arrivals scale)
      const content = own.filter(c => c.tone !== TONE.SHEET)
      if (k < 2) startIn(content, a + 0.05, a + 0.09, sp, c => c.p[0], { kind: 'scale' })
      else if (k === 2) startIn(content, a + 0.04, a + 0.09, sp, c => c.p[0] * 0.15 + c.p[1], { kind: 'scale' })
      else startIn(content, a + 0.04, a + 0.09, sp, c => -c.p[1], { kind: 'scale' })
    })
    // the draft tag's timing for the NEXT morph (live): it folds away as the promote packet lands
    startIn(by(cells, g => g === 'ag-draft'), LIVE.tag[0] - 0.02, LIVE.tag[0], SPREAD.live, c => c.p[0])
    return
  }
  // live
  const sp = SPREAD.live
  startIn(by(cells, g => g === 'ag-row-5'), LIVE.row, LIVE.row + 0.01, sp, c => c.p[0], { kind: 'scale', flight: TONE.ACCENT })
  startIn(by(cells, g => isSocket(g, 5)), LIVE.socket, LIVE.socket, sp, () => 0, { flight: TONE.ACCENT })
  startIn(by(cells, g => g === 'ag-live'), LIVE.tag[0], LIVE.tag[1], sp, c => c.p[0], { kind: 'scale' })
  startIn(by(cells, g => g === 'ag-reply'), LIVE.reply, LIVE.reply, sp, c => c.p[0], { kind: 'scale' })
}

/** The scene at state `s`; with `phase`, timed for the scroll morph into that beat (only on the beat's preset). */
export function agentsLayout(s: AgentsState, phase?: BeatId): Part {
  const P = kit.merge(chat(s), tower(s), board(s))
  if (phase && isPreset(s, phase)) decorate(P.cells, phase)
  return P
}
