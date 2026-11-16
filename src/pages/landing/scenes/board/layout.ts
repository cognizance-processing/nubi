/**
 * 04 BOARD — pure geometry (storyboard §4.5, contact sheet H). No three, no DOM, no randomness.
 *
 * The board is one browser window (your app, embedding the board with one tag) with six tiles:
 * a KPI strip of four cards, revenue per day (14 clickable bars) and revenue by region (4 clickable bars).
 *
 * Everything that can change with the filter is drawn as FIXED SLOTS: every numeral pixel, bar step and
 * region-bar cell exists in every state, lit (full size) or dark (zero size). Instance identity therefore
 * never changes between filter states, so a state change is a pure per-cell event: a pixel that lights up
 * arrives (APPEAR), a pixel that goes dark shrinks away, a lit pixel stays. That is
 * what lets one radial order drive the wave and the flips together, for scroll beats and for clicks alike.
 */
import { kit, type Part } from '../../stage/kit'
import { TONE, type Cell, type GroupInfo, type Layout, type MorphKind, type Vec3 } from '../../stage/types'
import {
  BOARD_TITLE, DAYS, FULFIL_MEAN_S, FULFIL_S, ORD_K, ORD_TOTAL_K, REGIONS, REGION_SHARE, REV_K, REV_MAX_K, REV_TOTAL_K,
  fmtMinSec, fmtMoneyK, fmtThousands, fmtTicket, regionRevK,
} from '../../stage/data'

/* ───────────── state ───────────── */

export interface Filter { day: number | null; region: number | null }
export interface BoardState extends Filter {
  /** the filter has reached the other tiles (false on the `click` beat: only the bar chart shows it yet) */
  applied: boolean
  /** the filter the tiles showed before this state: decides which pixels flip in and which shrink away */
  was: Filter
  /** set by an interaction (click, chip): no auto-demo cursor, no scroll-only arrival orders */
  user: boolean
  /** origin of the last change: 'bar-<i>' | 'region-<r>' (the wave starts there) */
  o: string
}

export const NONE: Filter = { day: null, region: null }
export const shown = (s: BoardState): Filter => (s.applied ? { day: s.day, region: s.region } : NONE)

/* ───────────── values (all derived from stage/data) ───────────── */

export const KPI_IDS = ['revenue', 'orders', 'ticket', 'fulfil'] as const
export const KPI_CAPTIONS = ['Revenue · USD', 'Orders', 'Avg ticket · USD', 'Fulfilment']

const dayRev = (d: number | null) => (d == null ? REV_TOTAL_K : REV_K[d])
const dayOrd = (d: number | null) => (d == null ? ORD_TOTAL_K : ORD_K[d])

/** The four KPI strings for a filter. Region splits use the region's revenue share (orders follow it). */
export function kpiValues(f: Filter): string[] {
  const sh = f.region == null ? 1 : REGION_SHARE[f.region]
  const rev = dayRev(f.day) * sh, ord = dayOrd(f.day) * sh
  const ful = f.day == null ? FULFIL_MEAN_S : FULFIL_S[f.day]
  return [fmtMoneyK(rev), fmtThousands(ord), fmtTicket(rev / ord), fmtMinSec(ful)]
}
/** Revenue by region ($k) for the day filter (the region chart is the source of the region filter, so it ignores it). */
export const regionValues = (f: Filter) => regionRevK(f.day)
/** Revenue per day ($k) for the region filter (the bar chart is the source of the day filter). */
export const dayValues = (f: Filter) => REV_K.map(v => (f.region == null ? v : v * REGION_SHARE[f.region]))

export function filterLabel(f: Filter): string {
  if (f.day == null && f.region == null) return 'All 14 days'
  return [f.day != null ? `Day ${f.day + 1}` : '', f.region != null ? REGIONS[f.region] : ''].filter(Boolean).join(' · ')
}

/** Every filter the board can show (15 day options × 5 region options). */
export const ALL_FILTERS: Filter[] = [null, ...REGIONS.map((_, r) => r)].flatMap(region =>
  [null, ...Array.from({ length: DAYS }, (_, d) => d)].map(day => ({ day, region })))

/** 1, 2, 2.5, 5 × 10^k ≥ v: the region axis rescales like a real chart. */
export function niceMax(v: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(1e-9, v))))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v - 1e-9) return m * p
  return 10 * p
}

/* ───────────── geometry ───────────── */

export const BOARD_W = 56, BOARD_H = 34
export const BOARD_URL_EMBED = 'ops.acme.example/stores'
const win0 = kit.browserWindow({ id: 'win', at: [0, 0, 0], w: BOARD_W, h: BOARD_H, tiles: 2 })
export const SCREEN = win0.screen                         // x −27..27, y 1..31, zFront .4
const Z = SCREEN.zFront
const PLATE_Z = Z + 0.2, PLATE_D = 0.4                     // tile plates: front at Z + .4
const ON = Z + PLATE_D                                     // the front of a tile plate

// KPI strip
const KPI_TOP = 26.9, KPI_H = 5.8, KPI_W = 12.1, KPI_GAP = 1.2, KPI_X0 = SCREEN.x0 + 1
export const KPI_PX = 0.55
const kpiX = (i: number) => KPI_X0 + i * (KPI_W + KPI_GAP)
// charts row
const ROW_Y0 = 2, ROW_Y1 = 20.2
export const BARS = { x0: SCREEN.x0 + 1, x1: 1.2 }
export const REG = { x0: 2.4, x1: SCREEN.x1 - 1 }
const BAR_BASE = 3.6, BAR_SLOTS = 12, BAR_W = 1.2, BAR_PITCH = 1.76, BAR_DEPTH = 1.4, BAR_PRESS = 0.4
const BAR_SPAN = DAYS * BAR_PITCH - (BAR_PITCH - BAR_W)
const BAR_LEFT = BARS.x0 + (BARS.x1 - BARS.x0 - BAR_SPAN) / 2
export const barX = (i: number) => BAR_LEFT + i * BAR_PITCH + BAR_W / 2
const REG_LABEL_X = REG.x0 + 0.9, REG_BAR_X = REG.x0 + 4.4, REG_SLOTS = 12, REG_PITCH = 3.8, REG_TOP = 15.4, REG_BAR_H = 1.6, REG_DEPTH = 1.2
export const regY = (r: number) => REG_TOP - r * REG_PITCH
const REG_PX = 0.25, REG_VAL_RIGHT = REG.x1 - 0.8

/* ───────────── voxel dot-matrix (fixed slots) ───────────── */

/** Lit pixels of `text` in the 3×5 font as a set of "col,row" (col in pixels from the left of the text). */
function pixels(text: string): { lit: Set<string>; w: number } {
  const lit = new Set<string>()
  let x = 0
  for (const ch of text) {
    const g = kit.FONT[ch]
    if (!g) { x += 2; continue }
    g.forEach((row, r) => [...row].forEach((b, c) => { if (b === '1') lit.add(`${x + c},${r}`) }))
    x += g[0].length + 1
  }
  return { lit, w: Math.max(0, x - 1) }
}
/** Matrix width (pixels) that fits every value a slot can show. */
const widest = (texts: string[]) => Math.max(...texts.map(t => pixels(t).w))
export const KPI_COLS = KPI_IDS.map((_, i) => widest(ALL_FILTERS.map(f => kpiValues(f)[i])))
const REG_COLS = widest(ALL_FILTERS.flatMap(f => regionValues(f).map(v => fmtMoneyK(v))))

/** Lit test for a slot of a matrix, left- or right-aligned in `cols`. */
function litFn(text: string, cols: number, align: 'left' | 'right') {
  const { lit, w } = pixels(text)
  const off = align === 'right' ? cols - w : 0
  return (c: number, r: number) => lit.has(`${c - off},${r}`)
}

const ZERO: Vec3 = [0, 0, 0]
/**
 * How a slot arrives when it lights up. 'flip' (split-flap) is the intent, but the engine's flip draws a parked
 * cube as a thin slat (its depth turned upright) until its turn comes; until that is fixed (requested in the
 * report) arriving pixels grow in place, which reads as a dot-matrix board.
 */
export const APPEAR: MorphKind = 'scale'
const kindOf = (now: boolean, before: boolean): MorphKind => (!now ? 'scale' : before ? 'slide' : APPEAR)

/**
 * A dot-matrix of voxel pixels. `at` = top-left (or top-right) of the matrix on the front plane.
 * Every slot is a cell: lit = INK pixel, dark = zero size (invisible, keeps its instance).
 */
function matrix(o: { group: string; at: Vec3; px: number; cols: number; text: string; before: string; align: 'left' | 'right'; tone?: number }): Cell[] {
  const { group, at, px, cols, align, tone = TONE.INK } = o
  const now = litFn(o.text, cols, align), was = litFn(o.before, cols, align)
  const x0 = align === 'right' ? at[0] - cols * px : at[0]
  const cells: Cell[] = []
  for (let c = 0; c < cols; c++) for (let r = 0; r < 5; r++) {
    const on = now(c, r)
    cells.push({ p: [x0 + (c + 0.5) * px, at[1] - (r + 0.5) * px, at[2]], s: on ? [px * 0.9, px * 0.9, px * 1.1] : ZERO, tone, group, wave: true, kind: kindOf(on, was(c, r)) })
  }
  return cells
}

/** A tile plate of SHEET cells with pitch ~`cell`, top-left at (x0, yTop). */
function plate(id: string, x0: number, yTop: number, w: number, h: number, cell: number, tone: number = TONE.SHEET): Cell[] {
  const nx = Math.max(1, Math.round(w / cell)), ny = Math.max(1, Math.round(h / cell))
  const sx = w / nx, sy = h / ny
  const cells: Cell[] = []
  for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) {
    cells.push({ p: [x0 + (i + 0.5) * sx, yTop - (j + 0.5) * sy, PLATE_Z], s: [sx, sy, PLATE_D], tone, group: id, wave: true })
  }
  // a backer of the same group: a seam between two plate cells then shows the same id (no stray contour)
  cells.push({ p: [x0 + w / 2, yTop - h / 2, PLATE_Z - 0.05], s: [w - 0.1, h - 0.1, PLATE_D * 0.5], tone, group: id })
  return cells
}

/* ───────────── the board ───────────── */

export interface BoardPart extends Part { origin: Vec3 }

/** Where a change starts: the top-front of the clicked bar, or the end of the clicked region bar. */
export function originOf(o: string, s: Filter): Vec3 {
  const b = /^bar-(\d+)$/.exec(o)
  if (b) { const i = +b[1]; const h = barLit(dayValues(s), i); return [barX(i), BAR_BASE + h, ON + BAR_DEPTH - (s.day === i ? BAR_PRESS : 0)] }
  const r = /^region-(\d+)$/.exec(o)
  if (r) { const k = +r[1]; return [REG_BAR_X + regLit(regionValues(s), k), regY(k), ON + REG_DEPTH] }
  return [barX(9), BAR_BASE + barLit(dayValues(NONE), 9), ON + BAR_DEPTH]
}
const barLit = (vals: number[], i: number) => Math.max(1, Math.round((vals[i] / REV_MAX_K) * BAR_SLOTS))
const fullLit = (i: number) => barLit(REV_K, i)
const regLit = (vals: number[], r: number) => Math.max(1, Math.round((vals[r] / niceMax(Math.max(...vals))) * REG_SLOTS))

/** Farthest any board cell can be from `o` (3D, the engine's radial order is 3D): the order's normalisation is pinned to it. */
export function reachOf(o: Vec3): number {
  let r = 0
  for (const x of [-BOARD_W / 2, BOARD_W / 2]) for (const y of [0, BOARD_H]) for (const z of [-0.6, ON + BAR_DEPTH + 0.6]) r = Math.max(r, Math.hypot(x - o[0], y - o[1], z - o[2]))
  return Math.ceil(r + 0.5)
}
/** Every origin a change can start from (14 bars, 4 region bars), for every filter state. */
export const ORIGINS = [...Array.from({ length: DAYS }, (_, i) => `bar-${i}`), ...REGIONS.map((_, r) => `region-${r}`)]
/** The largest reach of any origin: the ring's radius is scaled to it. */
export const MAX_REACH = Math.max(...ORIGINS.flatMap(o => ALL_FILTERS.map(f => reachOf(originOf(o, f)))))

/**
 * The board for a state. `s.was` decides which pixels flip in (lit now, dark before) and which shrink.
 * Two zero-size sentinels pin the radial order to distance / reachOf(origin), so with spread = reach / R a ring
 * of radius R · m reaches every cell exactly when its flip starts (see index.ts).
 */
export function board(s: BoardState): BoardPart {
  const now = shown(s), before = s.was
  const P = kit.merge(kit.browserWindow({ id: 'win', at: [0, 0, 0], w: BOARD_W, h: BOARD_H, tiles: 2, title: 'Stores', url: BOARD_URL_EMBED }))
  // tile the screen and the frame exactly: sub-pixel gaps would expose the group behind and draw contour lines
  // (and drop the frame cubes hidden behind the tab and URL slabs: their faces leak into the id buffer under MSAA)
  const slabs = P.cells.filter(c => c.group === 'win-tab' || c.group === 'win-url')
  const covered = (c: Cell) => slabs.some(b => Math.abs(c.p[0] - b.p[0]) + 0.5 <= b.s![0] / 2 + 1e-6 && Math.abs(c.p[1] - b.p[1]) + 0.5 <= b.s![1] / 2 + 0.35)
  P.cells = P.cells.filter(c => !(c.group === 'win' && c.s && c.s[2] === 1 && covered(c)))
  const sc = win0.screen
  P.cells.push({ p: [(sc.x0 + sc.x1) / 2, (sc.y0 + sc.y1) / 2, sc.zFront - 0.24], s: [sc.x1 - sc.x0 - 0.1, sc.y1 - sc.y0 - 0.1, 0.12], tone: TONE.SCREEN, group: 'win-screen' })
  P.cells.forEach(c => {
    if (c.group === 'win-screen' && c.s![2] !== 0.12) { c.wave = true; c.s = [Math.round(c.s![0] / 0.97 * 100) / 100, Math.round(c.s![1] / 0.97 * 100) / 100, c.s![2]] }
    else if (c.group === 'win' && c.s && c.s[2] === 1) c.s = [1, 1, 1]
  })
  const groups: Record<string, GroupInfo> = P.groups
  const texts = P.texts

  // header: board title + the filter pill (changes on click: the filter state is the chip; tiles follow)
  texts.push({ id: 'title', at: [SCREEN.x0 + 1.1, SCREEN.y1 - 1.75, Z + 0.05], text: BOARD_TITLE, font: 'title', size: 0.82, tone: 'ink' })
  texts.push({ id: 'sub', at: [SCREEN.x0 + 1.1, SCREEN.y1 - 2.85, Z + 0.05], text: 'Acme · last 14 days', font: 'mono', size: 0.48, tone: 'muted' })
  const pillW = 7.4, pillX1 = SCREEN.x1 - 1, pillY = SCREEN.y1 - 1.9
  const filtered = s.day != null || s.region != null
  P.cells.push({ p: [pillX1 - pillW / 2, pillY, Z + 0.2], s: [pillW, 1.5, 0.3], tone: filtered ? TONE.ACCENT_SOFT : TONE.SHEET, group: 'pill', wave: true })
  texts.push({ id: 'pill', at: [pillX1 - pillW / 2, pillY - 0.26, Z + 0.37], text: (st: BoardState) => (st.day != null || st.region != null ? `${filterLabel(st)}  ×` : 'All 14 days'), font: 'ui', size: 0.52, tone: 'ink', align: 'centre' })
  texts.push({ id: 'pill-k', at: [pillX1 - pillW - 0.6, pillY - 0.22, Z + 0.05], text: 'FILTER', font: 'mono', size: 0.44, tone: 'muted', align: 'right' })

  // KPI strip: four cards; numerals are fixed dot-matrices
  const kNow = kpiValues(now), kWas = kpiValues(before)
  KPI_IDS.forEach((id, i) => {
    const g = `kpi-${id}`, x0 = kpiX(i)
    P.cells.push(...plate(g, x0, KPI_TOP, KPI_W, KPI_H, 1))
    texts.push({ id: `${g}-cap`, at: [x0 + 0.8, KPI_TOP - 1.2, ON + 0.02], text: KPI_CAPTIONS[i], font: 'mono', size: 0.5, tone: 'muted', maxWidth: KPI_W - 1.4 })
    P.cells.push(...matrix({ group: `${g}-value`, at: [x0 + 0.8, KPI_TOP - 2.1, ON + KPI_PX * 0.55], px: KPI_PX, cols: KPI_COLS[i], text: kNow[i], before: kWas[i], align: 'left' }))
    groups[g] = { hover: true }
    groups[`${g}-value`] = { hover: true }
    P.anchors[g] = [x0 + KPI_W / 2, KPI_TOP, ON]
  })

  // revenue per day: 14 bars of fixed slots; region filter = part-of-whole (INK part, PAPER remainder)
  P.cells.push(...plate('card-bars', BARS.x0, ROW_Y1, BARS.x1 - BARS.x0, ROW_Y1 - ROW_Y0, 2))
  texts.push({ id: 'cap-bars', at: [BARS.x0 + 0.8, ROW_Y1 - 1.45, ON + 0.02], text: 'Revenue per day', font: 'ui', size: 0.6, tone: 'ink' })
  texts.push({ id: 'sub-bars', at: [BARS.x0 + 0.8, ROW_Y1 - 2.55, ON + 0.02], text: 'Click a bar to filter every tile', font: 'mono', size: 0.46, tone: 'muted' })
  texts.push({ id: 'ax-1', at: [barX(0), BAR_BASE - 1.1, ON + 0.02], text: 'Day 1', font: 'mono', size: 0.46, tone: 'muted', align: 'left' })
  texts.push({ id: 'ax-14', at: [barX(DAYS - 1) + BAR_W / 2, BAR_BASE - 1.1, ON + 0.02], text: '14', font: 'mono', size: 0.46, tone: 'muted', align: 'right' })
  P.cells.push({ p: [BAR_LEFT + BAR_SPAN / 2, BAR_BASE - 0.1, ON + 0.12], s: [BAR_SPAN + 0.8, 0.14, 0.24], tone: TONE.INK, group: 'bar-axis', wave: true })
  const vNow = dayValues(now)
  for (let i = 0; i < DAYS; i++) {
    const g = `bar-${i}`, sel = s.day === i, full = fullLit(i)
    const part = now.region == null ? full : barLit(vNow, i)
    const z = ON + BAR_DEPTH / 2 - (sel ? BAR_PRESS : 0)
    for (let u = 0; u < BAR_SLOTS; u++) {
      const on = u < full
      const tone = sel ? (u < part ? TONE.ACCENT : TONE.ACCENT_SOFT) : u >= part ? TONE.PAPER : s.day != null ? TONE.PAPER : TONE.INK
      P.cells.push({ p: [barX(i), BAR_BASE + u + 0.5, z], s: on ? [BAR_W * 0.92, 0.94, BAR_DEPTH] : ZERO, tone, group: g, wave: true, kind: on ? 'slide' : 'scale' })
    }
    groups[g] = { hover: true, click: true, liftOnHover: [0, 0, 0.8] }
    P.anchors[g] = [barX(i), BAR_BASE + full, z + BAR_DEPTH / 2]
  }

  // revenue by region: 4 bars of fixed slots (axis rescales), values as right-aligned dot-matrices
  P.cells.push(...plate('card-region', REG.x0, ROW_Y1, REG.x1 - REG.x0, ROW_Y1 - ROW_Y0, 2))
  texts.push({ id: 'cap-region', at: [REG.x0 + 0.8, ROW_Y1 - 1.45, ON + 0.02], text: 'Revenue by region', font: 'ui', size: 0.6, tone: 'ink' })
  texts.push({ id: 'sub-region', at: [REG.x0 + 0.8, ROW_Y1 - 2.55, ON + 0.02], text: 'USD · click a region', font: 'mono', size: 0.46, tone: 'muted' })
  const rNow = regionValues(now), rWas = regionValues(before)
  REGIONS.forEach((name, r) => {
    const g = `region-${r}`, y = regY(r)
    const n = regLit(rNow, r), nWas = regLit(rWas, r)
    const tone = s.region == null ? TONE.INK : s.region === r ? TONE.ACCENT : TONE.PAPER
    for (let u = 0; u < REG_SLOTS; u++) {
      const on = u < n
      P.cells.push({ p: [REG_BAR_X + u + 0.5, y, ON + REG_DEPTH / 2], s: on ? [0.94, REG_BAR_H, REG_DEPTH] : ZERO, tone, group: g, wave: true, kind: kindOf(on, u < nWas) })
    }
    texts.push({ id: `${g}-label`, at: [REG_LABEL_X, y - 0.27, ON + 0.02], text: name, font: 'mono', size: 0.52, tone: 'ink' })
    P.cells.push(...matrix({ group: `${g}-value`, at: [REG_VAL_RIGHT, y + 2.5 * REG_PX, ON + REG_PX * 0.55], px: REG_PX, cols: REG_COLS, text: fmtMoneyK(rNow[r]), before: fmtMoneyK(rWas[r]), align: 'right' }))
    groups[g] = { hover: true, click: true, liftOnHover: [0, 0, 0.6] }
    groups[`${g}-value`] = { hover: true }
    P.anchors[g] = [REG_BAR_X + n, y, ON + REG_DEPTH]
  })

  // the filter mark (zero size): the board-level callout points at the filter pill's top edge, only while filtered
  const origin = originOf(s.o, s)
  if (filtered) P.cells.push({ p: [pillX1 - pillW / 2, pillY + 0.75, Z + 0.35], s: ZERO, tone: TONE.INK, group: 'sel' })
  // order sentinels (zero size): pin the radial normalisation to [0, ORDER_REACH] around the origin
  P.cells.push({ p: origin, s: ZERO, tone: TONE.INK, group: '' }, { p: [origin[0], origin[1], origin[2] - reachOf(origin)], s: ZERO, tone: TONE.INK, group: '' })

  return Object.assign(P, { groups, origin })
}

/** Accent outlines for the tiles a filter changed (reduced motion: replaces the wave). [x0, y0, x1, y1] on the plate front. */
export function updatedTiles(s: BoardState): [number, number, number, number][] {
  const f = shown(s)
  if (f.day == null && f.region == null) return []
  const out: [number, number, number, number][] = KPI_IDS.map((_, i) => [kpiX(i), KPI_TOP - KPI_H, kpiX(i) + KPI_W, KPI_TOP])
  if (f.day != null) out.push([REG.x0, ROW_Y0, REG.x1, ROW_Y1])
  if (f.region != null) out.push([BARS.x0, ROW_Y0, BARS.x1, ROW_Y1])
  return out
}

/** Where the cursor goes on the `click` beat: the front of day 10's bar, mid-height. */
export function cursorTarget(day = 9): Vec3 {
  return [barX(day) + 0.1, BAR_BASE + fullLit(day) * 0.62, ON + BAR_DEPTH - BAR_PRESS]
}

export const BOARD_PLATE_FRONT = ON
/** Top-centre of KPI card i (hover callouts). */
export const kpiAnchor = (i: number): Vec3 => [kpiX(i) + KPI_W / 2, KPI_TOP, ON]
export type { Layout }
