/**
 * HERO layouts (storyboard §4.1): the store-performance board in a browser tab, and the same tab exploded into its
 * three layers (UI · ENGINE · DATA). Pure TypeScript: no three, no DOM, no randomness. Every number is derived from
 * stage/data.ts. Imported by the scene module, the 2D poster and the Login/Register art panel.
 *
 * World frame: the window stands on the floor at x = 0 facing +z, 52 × 34 with a 3-row title bar. Its screen runs
 * x −25..25, y 1..31 at z ≈ .4 (zFront). Screen grid (left → right):
 *   header  y 31 → 28    board title · two filter chips
 *   KPIs    y 27.2 → 21.8 four tiles 11 × 5.4, gap 1.2
 *   charts  y 20 → 4.8   revenue bars (left, 14 days) · orders line with area (right)
 *   status  y 1 → 2.4    "● Computed in this tab · DuckDB-WASM · 1,048,576 rows · 38 ms"
 * The ENGINE and DATA panes are parked behind the window on the board beat (fully occluded) so the explode can slide
 * them out from behind the tab, like cards fanned from a deck.
 */
import { kit, type Part } from '../../stage/kit'
import { TONE, type Cell, type CameraKey, type Layout, type Vec3 } from '../../stage/types'
import {
  BOARD_TITLE, BOARD_URL, DAYS, FULFIL_S, ORD_K, ORD_MAX_K, REV_K, REV_MAX_K, ROWS_TOTAL,
  fmtInt, fmtPct, fmtThousands, kpis,
} from '../../stage/data'

export interface HeroState {
  /** selected day, 0..13 (the accent bar and the accent point on the line) */
  day: number
  /** true once the visitor has interacted: the layout then carries no build-in kinds (no flips on a day change) */
  live?: boolean
}
export const HERO_INITIAL: HeroState = { day: DAYS - 1 }

/* ───────────── geometry constants (world units) ───────────── */

export const WIN_W = 52, WIN_H = 34, TITLE_BAR = 3
const SX0 = -WIN_W / 2 + 1, SX1 = WIN_W / 2 - 1, SY0 = 1, SY1 = WIN_H - TITLE_BAR
const Z = 0.4                                  // screen front
const M = 1.2                                  // screen margin
const TY = WIN_H - TITLE_BAR / 2               // title-bar centre line

/** KPI tiles */
const KPI_W = 11, KPI_H = 6.3, KPI_TOP = 27.4, KPI_GAP = (SX1 - SX0 - 4 * KPI_W) / 5, KPI_PX = 0.5
/** charts */
const CH_BASE = 4.8, CH_H = 13
const BAR_X0 = SX0 + M + 2.6, BAR_W = 1, BAR_GAP = 0.74, BAR_DEPTH = 1.4
const BAR_SPAN = DAYS * (BAR_W + BAR_GAP) - BAR_GAP
const LINE_X0 = 6.6, LINE_X1 = SX1 - M, LINE_STEP = (LINE_X1 - LINE_X0) / DAYS
const LINE_MAX = ORD_MAX_K
const CAP_Y = 19.3                             // chart caption baseline

/* ───────────── derived figures ───────────── */

const sum = (a: number[]) => a.reduce((s, v) => s + v, 0)
const mean = (a: number[]) => sum(a) / a.length
/** last 7 days vs the 7 before, for the KPI deltas */
const half = (a: number[]) => [a.slice(0, 7), a.slice(7)] as const
function deltas(): Record<string, number> {
  const [r0, r1] = half(REV_K), [o0, o1] = half(ORD_K), [f0, f1] = half(FULFIL_S)
  const t0 = sum(r0) / sum(o0), t1 = sum(r1) / sum(o1)
  return { revenue: sum(r1) / sum(r0) - 1, orders: sum(o1) / sum(o0) - 1, ticket: t1 / t0 - 1, fulfil: mean(f1) / mean(f0) - 1 }
}
export const KPI_DELTA = deltas()
const fmtDelta = (f: number) => `${f >= 0 ? '+' : '−'}${fmtPct(Math.abs(f))}`

export const barH = (v: number) => Math.max(1, Math.round((v / REV_MAX_K) * CH_H))
export const lineH = (v: number) => Math.max(1, Math.round((v / LINE_MAX) * CH_H))
export const barX = (i: number) => BAR_X0 + i * (BAR_W + BAR_GAP) + BAR_W / 2
export const lineX = (i: number) => LINE_X0 + i * LINE_STEP + LINE_STEP / 2

/** Anchors used by picks and callouts (world points, board beat). */
export const HERO_ANCHORS = {
  status: [SX0 + 1.0, SY0 + 0.7, Z + 0.45] as Vec3,
  bar: (i: number): Vec3 => [barX(i), CH_BASE + barH(REV_K[i]), Z + BAR_DEPTH + 0.8],
  line: (i: number): Vec3 => [lineX(i), CH_BASE + lineH(ORD_K[i]), Z + 1.2],
  kpi: (i: number): Vec3 => [SX0 + KPI_GAP + i * (KPI_W + KPI_GAP) + KPI_W / 2, KPI_TOP, Z + 0.9],
}

/* ───────────── pieces ───────────── */

const P = (): Part => ({ cells: [], groups: {}, anchors: {}, texts: [] })
const put = (part: Part, p: Vec3, s: Vec3, tone: number, group: string, extra?: Partial<Cell>) => {
  const c: Cell = { p, s, tone, group, ...extra }
  part.cells.push(c)
  return c
}

/** The browser window: kit frame + screen, with the tab and URL bar re-cut so the full title fits. */
function windowPart(): Part {
  const win = kit.browserWindow({ id: 'win', at: [0, 0, 0], w: WIN_W, h: WIN_H, titleBar: TITLE_BAR, tiles: 2 })
  const x0 = -WIN_W / 2
  const tabX0 = x0 + 5.2, tabX1 = x0 + 17.4, urlX0 = tabX1 + 1.2, urlX1 = x0 + WIN_W - 2
  for (const c of win.cells) {
    if (c.group === 'win-tab') { c.p = [(tabX0 + tabX1) / 2, TY - 0.3, 0.58]; c.s = [tabX1 - tabX0, TITLE_BAR - 0.8, 0.22] }
    if (c.group === 'win-url') { c.p = [(urlX0 + urlX1) / 2, TY, 0.58]; c.s = [urlX1 - urlX0, 1.4, 0.2] }
  }
  // favicon (the product's accent square) and the URL's lock
  put(win, [tabX0 + 0.9, TY - 0.15, 0.74], [0.62, 0.62, 0.12], TONE.ACCENT, 'win-tab')
  put(win, [urlX0 + 0.75, TY - 0.05, 0.72], [0.42, 0.5, 0.12], TONE.INK, 'win-url')
  win.texts.push(
    { id: 'win-title', at: [tabX0 + 1.65, TY - 0.42, 0.71], text: BOARD_TITLE, font: 'ui', size: 0.56, tone: 'ink', maxWidth: tabX1 - tabX0 - 2.1 },
    { id: 'win-url', at: [urlX0 + 1.4, TY - 0.24, 0.71], text: BOARD_URL, font: 'mono', size: 0.5, tone: 'muted', maxWidth: urlX1 - urlX0 - 2 },
  )
  return win
}

/** Board header: title + two filter chips. */
function headerPart(): Part {
  const h = P()
  h.texts.push({ id: 'board-title', at: [SX0 + M, 28.75, Z + 0.05], text: BOARD_TITLE, font: 'title', size: 0.82, tone: 'ink' })
  const chips: [string, number, number][] = [['Last 14 days', SX1 - M - 5.4 - 0.5 - 5.6, 5.6], ['All regions', SX1 - M - 5.4, 5.4]]
  chips.forEach(([label, x, w], i) => {
    put(h, [x + w / 2, 29.1, Z + 0.15], [w, 1.4, 0.26], TONE.SHEET, `chip-${i}`)
    h.texts.push({ id: `chip-${i}`, at: [x + 0.6, 28.86, Z + 0.3], text: label, font: 'ui', size: 0.5, tone: 'ink', maxWidth: w - 0.9 })
  })
  return h
}

/** A KPI card: one SHEET plate, voxel numerals, caption + 7-day delta. Plate and numerals share one group, so they
 *  lift together on hover and take one contour. */
function kpiPart(i: number): Part {
  const k = kpis()[i]
  const x0 = SX0 + KPI_GAP + i * (KPI_W + KPI_GAP), yt = KPI_TOP
  const g = `kpi-${k.id}`
  const part = P()
  put(part, [x0 + KPI_W / 2, yt - KPI_H / 2, Z + 0.2], [KPI_W, KPI_H, 0.4], TONE.SHEET, g)
  const v = kit.voxelText({ text: k.value, at: [x0 + 0.75, yt - 1.8, Z + 0.55], px: KPI_PX, tone: TONE.INK, group: g })
  part.cells.push(...v.cells)
  part.texts.push(
    { id: `${g}-caption`, at: [x0 + 0.75, yt - 1.05, Z + 0.42], text: k.caption, font: 'mono', size: 0.5, tone: 'muted', maxWidth: KPI_W - 1.4 },
    { id: `${g}-delta`, at: [x0 + 0.75, yt - KPI_H + 0.75, Z + 0.42], text: `${fmtDelta(KPI_DELTA[k.id])} vs prior 7 days`, font: 'mono', size: 0.5, tone: 'muted', maxWidth: KPI_W - 1.4 },
  )
  part.groups[g] = { hover: true, liftOnHover: [0, 0, 0.6] }
  return part
}

/** Revenue bars: 14 days, the selected day ACCENT, light gridlines (one segment per day slot, so the 2D poster's
 *  painter sorts them behind the bars) and axis labels. */
function barsPart(sel: number): Part {
  const part = P()
  put(part, [BAR_X0 + BAR_SPAN / 2, CH_BASE - 0.1, Z + 0.15], [BAR_SPAN + 0.6, 0.14, 0.3], TONE.INK, 'bar-axis')
  for (const v of [200, 400]) {
    const y = CH_BASE + (v / REV_MAX_K) * CH_H
    for (let i = 0; i < DAYS; i++) put(part, [barX(i), y, Z + 0.03], [BAR_W + BAR_GAP + 0.02, 0.08, 0.05], TONE.MID, 'grid')
    part.texts.push({ id: `bar-y${v}`, at: [BAR_X0 - 0.7, y - 0.22, Z + 0.1], text: `$${v}k`, font: 'mono', size: 0.5, tone: 'muted', align: 'right' })
  }
  REV_K.forEach((v, i) => {
    const g = `bar-${i}`, hh = barH(v), x = barX(i)
    for (let u = 0; u < hh; u++) put(part, [x, CH_BASE + u + 0.5, Z + BAR_DEPTH / 2], [BAR_W * 0.9, 0.92, BAR_DEPTH], i === sel ? TONE.ACCENT : TONE.INK, g)
    part.groups[g] = { hover: true, click: true, liftOnHover: [0, 0, 0.8] }
  })
  for (const i of [0, 6, 13]) part.texts.push({ id: `bar-x${i}`, at: [barX(i), CH_BASE - 1.15, Z + 0.1], text: `${i + 1}`, font: 'mono', size: 0.5, tone: 'muted', align: 'centre' })
  part.texts.push({ id: 'cap-bars', at: [SX0 + M, CAP_Y, Z + 0.05], text: 'Revenue per day', font: 'ui', size: 0.52, tone: 'ink' })
  return part
}

/** Orders line with area: one group per day point (hover reads that day), the selected day ACCENT. */
function linePart(sel: number): Part {
  const part = P()
  put(part, [(LINE_X0 + LINE_X1) / 2, CH_BASE - 0.1, Z + 0.15], [LINE_X1 - LINE_X0 + 0.6, 0.14, 0.3], TONE.INK, 'line-axis')
  for (const v of [3, 6]) {
    const y = CH_BASE + (v / LINE_MAX) * CH_H
    for (let i = 0; i < DAYS; i++) put(part, [lineX(i), y, Z + 0.03], [LINE_STEP + 0.02, 0.08, 0.05], TONE.MID, 'grid')
    part.texts.push({ id: `line-y${v}`, at: [LINE_X0 - 0.7, y - 0.22, Z + 0.1], text: `${v}k`, font: 'mono', size: 0.5, tone: 'muted', align: 'right' })
  }
  ORD_K.forEach((v, i) => {
    const hh = lineH(v), x = lineX(i), g = `line-${i}`
    const tone = i === sel ? TONE.ACCENT : TONE.INK
    for (let u = 0; u < hh - 1; u++) put(part, [x, CH_BASE + u + 0.5, Z + 0.12], [0.97 * LINE_STEP, 0.97, 0.2], TONE.ACCENT_SOFT, 'line-area')
    put(part, [x, CH_BASE + hh - 0.5, Z + 0.45], [0.97 * LINE_STEP, 0.97, 0.7], tone, g)
    const prev = i ? lineH(ORD_K[i - 1]) : hh
    for (let u = Math.min(prev, hh); u < Math.max(prev, hh) - 1; u++) put(part, [x, CH_BASE + u + 0.5, Z + 0.45], [0.97 * LINE_STEP, 0.97, 0.7], tone, g)
    part.groups[g] = { hover: true, click: true, liftOnHover: [0, 0, 0.6] }
  })
  for (const i of [0, 6, 13]) part.texts.push({ id: `line-x${i}`, at: [lineX(i), CH_BASE - 1.15, Z + 0.1], text: `${i + 1}`, font: 'mono', size: 0.5, tone: 'muted', align: 'centre' })
  part.texts.push({ id: 'cap-line', at: [LINE_X0 - 2.4, CAP_Y, Z + 0.05], text: 'Orders per day', font: 'ui', size: 0.52, tone: 'ink' })
  return part
}

/** Status strip across the bottom of the screen. */
function statusPart(): Part {
  const part = P()
  put(part, [0, SY0 + 0.7, Z + 0.1], [SX1 - SX0, 1.4, 0.2], TONE.SHEET, 'statusbar')
  put(part, [SX0 + 1.0, SY0 + 0.7, Z + 0.3], [0.5, 0.5, 0.3], TONE.ACCENT, 'status')
  part.texts.push(
    { id: 'status', at: [SX0 + 1.7, SY0 + 0.47, Z + 0.22], text: `Computed in this tab · DuckDB-WASM · ${fmtInt(ROWS_TOTAL)} rows · 38 ms`, font: 'mono', size: 0.5, tone: 'ink' },
    { id: 'status-r', at: [SX1 - 1.0, SY0 + 0.47, Z + 0.22], text: 'Acme · USD', font: 'mono', size: 0.5, tone: 'muted', align: 'right' },
  )
  return part
}

/* ───────────── the two layers behind the tab ───────────── */

export const PANE_W = 22, PANE_H = 28
/** Where the ENGINE / DATA panes stand: parked behind the window (board) and fanned back-right (inside). */
export const PANES = {
  engine: { board: [1, 0, -2.2] as Vec3, inside: [33, 0, -10] as Vec3 },
  data: { board: [3, 0, -3.4] as Vec3, inside: [53, 0, -20] as Vec3 },
}
/** Engine chip centre relative to its pane's bottom-centre (in the part that stays visible). */
const CHIP_REL: Vec3 = [4.2, 15.5, 0.75]
/** Callout anchor: the engine chip's bottom pins, exploded. */
export const ENGINE_CHIP_BOTTOM: Vec3 = [PANES.engine.inside[0] + CHIP_REL[0], CHIP_REL[1] - 4.8, PANES.engine.inside[2] + CHIP_REL[2]]
/** Data table top-left relative to its pane's bottom-centre. */
const TABLE_REL: Vec3 = [-0.4, 21.6, 0.2]

function enginePane(at: Vec3, pins: boolean): Part {
  const pane = kit.pane({ id: 'pane-engine', at, w: PANE_W, h: PANE_H, tone: TONE.SHEET })
  const chip = kit.engineChip({ id: 'engine', at: [at[0] + CHIP_REL[0], at[1] + CHIP_REL[1], at[2] + CHIP_REL[2]] })
  if (!pins) chip.cells = chip.cells.filter(c => c.group !== 'engine-pins')
  return kit.merge(pane, chip)
}

/** A small Arrow table on the DATA pane: four named columns (INK headers) over ten rows of values, drawn
 *  column by column (Arrow is columnar). Values are bars whose length follows the day's figure. */
const TABLE_COLS: { name: string; w: number; v: (d: number) => number }[] = [
  { name: 'region', w: 2.6, v: () => 1 },
  { name: 'day', w: 1.6, v: () => 1 },
  { name: 'orders', w: 2.6, v: d => ORD_K[d] / ORD_MAX_K },
  { name: 'revenue', w: 3.0, v: d => REV_K[d] / REV_MAX_K },
]
const TABLE_ROWS = 10, TABLE_PITCH = 1.12, TABLE_GAP = 0.22
export const TABLE_W = TABLE_COLS.reduce((a, c) => a + c.w, 0) + TABLE_GAP * (TABLE_COLS.length - 1)

function dataPane(at: Vec3, rows: boolean): Part {
  const pane = kit.pane({ id: 'pane-data', at, w: PANE_W, h: PANE_H, tone: TONE.SHEET })
  if (!rows) return pane
  const part = P()
  const [x0, yt, z] = [at[0] + TABLE_REL[0], at[1] + TABLE_REL[1], at[2] + TABLE_REL[2] + 0.2]
  let x = x0
  TABLE_COLS.forEach((col, c) => {
    const g = `data-c${c}`
    put(part, [x + col.w / 2, yt - 0.55, z + 0.25], [col.w, 1.1, 0.5], TONE.INK, g)
    for (let r = 0; r < TABLE_ROWS; r++) {
      const d = (DAYS - 1 - r + DAYS) % DAYS
      const y = yt - 1.1 - TABLE_GAP - r * TABLE_PITCH - TABLE_PITCH / 2
      const f = Math.min(1, Math.max(0.3, col.v(d)))
      const len = c < 2 ? col.w * (c === 0 ? 0.62 + 0.3 * ((r * 7) % 3) / 2 : 0.7) : col.w * (0.3 + 0.7 * (f - 0.6) / 0.4)
      put(part, [x + 0.1 + len / 2, y, z + 0.18], [len, TABLE_PITCH * 0.56, 0.36], c >= 2 ? TONE.MID : TONE.PAPER, g)
    }
    part.texts.push({ id: g, at: [x + 0.25, yt - 0.78, z + 0.52], text: col.name, font: 'mono-strong', size: 0.42, tone: 'onInk', maxWidth: col.w - 0.4 })
    part.groups[g] = {}
    x += col.w + TABLE_GAP
  })
  part.anchors.table = [x0 + TABLE_W / 2, yt, z]
  return kit.merge(pane, part)
}

/* ───────────── layouts ───────────── */

/** The UI layer: the board in its browser tab (no panes). */
export function heroUi(s: HeroState = HERO_INITIAL): Part {
  const sel = Math.min(DAYS - 1, Math.max(0, Math.round(s.day)))
  return kit.merge(windowPart(), headerPart(), ...[0, 1, 2, 3].map(kpiPart), barsPart(sel), linePart(sel), statusPart())
}

/** Build-in order (2.6 s, spread .8): each cell animates for .52 s; `start` is its start time in seconds. */
const BUILD_MS = 2600, BUILD_SPREAD = 0.8
const SCREEN_KIND: Cell['kind'] = 'scale'
const start = (sec: number) => Math.min(1, Math.max(0, sec / ((BUILD_MS / 1000) * BUILD_SPREAD)))
function phase(cells: Cell[], s0: number, s1: number, key: (c: Cell) => number, kind: Cell['kind']) {
  if (!cells.length) return
  const ks = cells.map(key)
  const lo = Math.min(...ks), hi = Math.max(...ks), span = hi - lo || 1
  cells.forEach((c, i) => { c.order = start(s0 + ((ks[i] - lo) / span) * (s1 - s0)); c.kind = kind })
}

/** Stamp the page-load build onto a board part (storyboard §4.1 "Transition IN"). */
function withBuild(part: Part): Part {
  const is = (pred: (g: string) => boolean) => part.cells.filter(c => pred(c.group))
  // 1. frame + chrome rise from the paper, left to right (0 – .6 s)
  phase(is(g => g === 'win' || g === 'win-tab' || g === 'win-url'), 0, 0.1, c => c.p[0], 'rise')
  // 2. screen tiles paint in top to bottom like a page rendering (.35 – 1.25 s); the status strip with them.
  //    (SCREEN_KIND is 'scale' until the engine's flip-from-parked hides the cell's depth before it starts; see report)
  phase(is(g => g === 'win-screen' || g === 'statusbar'), 0.35, 0.73, c => -c.p[1] + c.p[0] * 0.02, SCREEN_KIND)
  // 3. KPI plates extrude (.9 – 1.0 s), numerals split-flap glyph by glyph, 40 ms apart (1.0 – 1.18 s)
  for (let i = 0; i < 4; i++) {
    const g = `kpi-${kpis()[i].id}`
    const cells = is(x => x === g)
    const plate = cells.filter(c => (c.s?.[0] ?? 1) > 2), digits = cells.filter(c => (c.s?.[0] ?? 1) <= 2)
    phase(plate, 0.9 + i * 0.04, 0.9 + i * 0.04, () => 0, 'scale')
    const gx0 = Math.min(...digits.map(c => c.p[0])), gy0 = Math.max(...digits.map(c => c.p[1]))
    const yt0 = (c: Cell) => Math.round((gy0 - c.p[1]) / KPI_PX)
    digits.forEach(c => { c.order = start(1.0 + i * 0.04 + Math.floor((c.p[0] - gx0) / (4 * KPI_PX)) * 0.04 + (yt0(c) * 0.012)); c.kind = 'scale' })
  }
  // chips and header details with the KPIs
  phase(is(g => g.startsWith('chip-')), 0.85, 0.95, c => c.p[0], SCREEN_KIND)
  // 4. bars grow from the axis, left to right, 45 ms apart; the accent bar lands last (1.2 – 2.2 s)
  phase(is(g => g === 'bar-axis' || g === 'line-axis'), 1.1, 1.2, c => c.p[0], 'scale')
  phase(is(g => g === 'grid'), 1.15, 1.3, c => c.p[1] + c.p[0] * 0.01, 'scale')
  const bars = is(g => /^bar-\d+$/.test(g))
  const accent = bars.find(c => c.tone === TONE.ACCENT)?.group
  const others = [...new Set(bars.map(c => c.group))].filter(g => g !== accent)
  bars.forEach(c => {
    const b = c.group === accent ? DAYS : others.indexOf(c.group)
    const u = c.p[1] - CH_BASE
    c.order = start(1.2 + b * 0.034 + u * 0.03); c.kind = 'scale'
  })
  // 5. the line pops along x, the area fills behind it (1.6 – 2.4 s)
  phase(is(g => /^line-\d+$/.test(g)), 1.6, 1.88, c => c.p[0], 'scale')
  phase(is(g => g === 'line-area'), 1.7, 1.95, c => c.p[0] + (c.p[1] - CH_BASE) * 0.15, 'scale')
  // the status dot blinks on last; the hidden layers behind the tab appear once the screen is opaque
  phase(is(g => g === 'status'), 2.05, 2.05, () => 0, 'scale')
  phase(is(g => g.startsWith('pane-') || g.startsWith('engine') || g.startsWith('data')), 2.0, 2.0, () => 0, 'cut')
  return part
}

/** Board beat: the tab, with the engine and data layers parked (hidden) behind it. */
export function heroBoard(s: HeroState = HERO_INITIAL): Part & Layout {
  const part = kit.merge(heroUi(s), enginePane(PANES.engine.board, false), dataPane(PANES.data.board, false))
  return (s.live ? part : withBuild(part)) as Part & Layout
}

/** Inside beat: the tab exploded into UI · ENGINE · DATA, fanned back-right. */
export function heroInside(s: HeroState = HERO_INITIAL): Part & Layout {
  const ui = heroUi(s)
  ui.cells.forEach(c => { c.order = 0 })                      // the board slides as one piece (it does not move)
  // spread .7 (beat morph): a cell moves for .3 of the scroll. The layers glide out while the camera pulls back
  // (engine m .32–.62, data m .42–.72; both reach their x/z by .62 of their window), then the Arrow table fills row by
  // row and the chip's pins pop (m .63–1).
  const eng = enginePane(PANES.engine.inside, true)
  eng.cells.forEach(c => {
    if (c.group === 'engine-pins') { c.order = 1; c.kind = 'scale' }
    else c.order = 0.45
  })
  const dat = dataPane(PANES.data.inside, true)
  const rows = dat.cells.filter(c => c.group.startsWith('data-'))
  phase(rows, 0, 1, c => -c.p[1] + c.p[0] * 0.01, 'scale')
  rows.forEach(c => { c.order = 0.9 + 0.1 * (c.order ?? 0) })
  dat.cells.filter(c => c.group === 'pane-data').forEach(c => { c.order = 0.6 })
  return kit.merge(ui, eng, dat)
}

/* ───────────── cameras ───────────── */

export const heroBoardCamera: CameraKey = { az: -0.36, el: 0.2, target: [0, 15.5, 0], zoom: 0.85, mobile: { az: -0.3, el: 0.18, target: [0, 16, 0], zoom: 1.08 } }
export const heroInsideCamera: CameraKey = { az: -0.52, el: 0.24, target: [21, 15, -8], zoom: 0.58, mobile: { az: -0.5, el: 0.22, target: [19, 16, -8], zoom: 0.74 } }

/** The composed board (poster / no-WebGL fallback / Login panel): pure, no build kinds. */
export function heroBoardLayout(s: HeroState = HERO_INITIAL): Part & Layout {
  return kit.merge(heroUi({ ...s, live: true }), enginePane(PANES.engine.board, false), dataPane(PANES.data.board, false)) as Part & Layout
}

/** Formatting helpers shared with the scene module. */
export const dayLabel = (d: number) => `Day ${d + 1}`
export const revLabel = (d: number) => `$${REV_K[d]}k`
export const ordLabel = (d: number) => fmtThousands(ORD_K[d])
