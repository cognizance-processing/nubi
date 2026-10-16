/**
 * 03 SECURITY — pure geometry (storyboard §4.4). No three, no DOM, no randomness except the seeded rng.
 *
 *   ☺ [AMER]  ┌▪▪▪ ▣ region = 'AMER' ┐   ┌▪▪▪ ▣ region = 'EMEA' ┐  [EMEA] ☺
 *   viewer A  │REGION  STORE  REVENUE │   │REGION  STORE  REVENUE │  viewer B
 *             │■AMER ▬▬  ▬▬▬▬▬▬       │   │┆┆┆┆┆┆┆┆ hidden line ┆┆┆│
 *             │┆┆┆┆┆┆ hidden line ┆┆┆ │   │■EMEA ▬▬  ▬▬▬▬▬        │
 *
 * Two browser windows show the same cached table, sorted by region into four bands of three rows. Each viewer
 * wears an ID badge (an ACCENT card on a lanyard) that is their signed claim; a token chip from the badge docks in
 * the window's query bar, and rows outside the claim collapse to flat GHOST outlines (an engineer's hidden lines).
 *
 * Motion is encoded per cell (Cell.kind / Cell.order) from `s.motion`, so every transition — scroll or click —
 * moves only what should move: the token arcs, rows re-slice band by band, the badges trade places mid-air
 * (the card landing on B arcs over the top, the card landing on A slides straight across underneath).
 */
import { kit, type Part } from '../../stage/kit'
import { TONE, type Cell, type MorphKind, type Vec3 } from '../../stage/types'
import { REGIONS, REGION_REV_K, type Region } from '../../stage/data'

export type Claim = Region | 'None'
export type Side = 'a' | 'b'
/** Which transition the layout is the destination of (drives per-cell kind/order). */
export type Motion = 'enter' | 'token' | 'rows' | 'cards'
export interface SecurityState {
  a: Claim; b: Claim
  claimed: boolean          // the token chips have docked in the query bars
  filtered: boolean         // the planner's predicate is applied
  swapped: boolean          // physical card parity: card-1 hangs on viewer B
  motion: Motion
}
export const INITIAL: SecurityState = { a: 'AMER', b: 'EMEA', claimed: false, filtered: false, swapped: false, motion: 'enter' }

/* ───────────── dimensions (world units) ───────────── */

export const ROWS = 12
export const BANDS = REGIONS.length                      // 4 bands …
export const PER_BAND = ROWS / BANDS                     // … of 3 rows
export const regionOfRow = (r: number): Region => REGIONS[Math.floor(r / PER_BAND)]
export const bandOf = (c: Claim) => (c === 'None' ? -1 : REGIONS.indexOf(c))
/** Rows a claim lets through, out of ROWS. */
export const rowsFor = (c: Claim) => (c === 'None' ? 0 : PER_BAND)

export const WIN = { w: 18, h: 19, tb: 2 }               // window: frame 1, title bar 2 rows (holds the query bar)
export const WIN_X = 9.9                                 // window centres at ±WIN_X (gap 1.8 between frames)
export const PERSON = { x: 20.7, z: 4.8, scale: 1.5, turn: 0.35 }
const Z_SCREEN = 0.4                                     // screen plate front
const TB_Y = WIN.h - WIN.tb / 2                          // title-bar centre line

/** Table on the screen: left/top padding, header, row pitch, gap between region bands. */
const T = { padX: 0.75, padTop: 0.55, head: 1.0, headGap: 0.42, pitch: 1.0, bandGap: 0.42, rowH: 0.78 }
/** Columns, from the table's left edge: REGION chip, STORE (greeked id), REVENUE (data bar). */
const COL = { chip: [0, 2.7], store: [3.2, 6.4], rev: [6.9, 14.5] } as const
const TABLE_W = COL.rev[1]

export const winCx = (side: Side) => (side === 'a' ? -WIN_X : WIN_X)
const tableX0 = (side: Side) => winCx(side) - WIN.w / 2 + 1 + T.padX
const tableTop = WIN.h - WIN.tb - T.padTop
export const rowY = (r: number) => tableTop - T.head - T.headGap - (r + 0.5) * T.pitch - Math.floor(r / PER_BAND) * T.bandGap
export const bandY = (b: number) => (rowY(b * PER_BAND) + rowY(b * PER_BAND + PER_BAND - 1)) / 2

/** Query bar (SHEET slab in the title bar) and the token slot at its left end. */
const BAR = { x0: (side: Side) => winCx(side) - WIN.w / 2 + 4.1, x1: (side: Side) => winCx(side) + WIN.w / 2 - 1.1, h: 1.24 }
/** Each token docks at the end of the bar facing its viewer (A: left end, B: right end), so its flight never
 *  crosses its own predicate text. */
export const slotAt = (side: Side): Vec3 => [side === 'a' ? BAR.x0(side) + 0.95 : BAR.x1(side) - 0.95, TB_Y, 0.78]
const TOKEN_S: Vec3 = [1.3, 0.78, 0.24]

/* ───────────── people and badges ───────────── */

const personX = (side: Side) => (side === 'a' ? -PERSON.x : PERSON.x)
const turnOf = (side: Side) => (side === 'a' ? PERSON.turn : -PERSON.turn)
/** The kit's person transform (local offset → world), so the badge sits exactly where kit.person hangs it. */
const local = (side: Side) => {
  const cx = personX(side), cz = PERSON.z, s = PERSON.scale, t = turnOf(side)
  return (dx: number, y: number, dz: number): Vec3 => [cx + (dx * Math.cos(t) + dz * Math.sin(t)) * s, y * s, cz + (-dx * Math.sin(t) + dz * Math.cos(t)) * s]
}
const BADGE = { w: 1.6, h: 2.1, d: 0.12, y: 5.2, dz: 0.74 }     // kit 'l' card, worn at chest height
export const badgeAt = (side: Side): Vec3 => local(side)(0, BADGE.y, BADGE.dz)
/** Where the region is printed on a worn card (baseline-left) and the card's plane. */
export const badgeText = (side: Side) => {
  const t = turnOf(side), s = PERSON.scale
  return { at: local(side)(-BADGE.w * 0.46, BADGE.y - BADGE.h * 0.3, BADGE.dz + 0.08), plane: { right: [Math.cos(t), 0, -Math.sin(t)] as Vec3, up: [0, 1, 0] as Vec3 }, size: 0.34 * s, maxWidth: BADGE.w * 0.94 * s }
}

/** The physical card hanging on `side` (card-1 starts on A). */
export const cardOn = (side: Side, s: SecurityState) => ((side === 'a') !== s.swapped ? 'card-1' : 'card-2')

function card(id: string, side: Side, kind: MorphKind | undefined, order: number | undefined): Cell[] {
  const t = turnOf(side), s = PERSON.scale, L = local(side)
  const extra: Partial<Cell> = { yaw: t, ...(kind ? { kind } : {}), ...(order != null ? { order } : {}) }
  const c = badgeAt(side)
  const chip = L(0, BADGE.y + BADGE.h * 0.27, BADGE.dz + 0.08)
  return [
    { p: c, s: [BADGE.w * s, BADGE.h * s, BADGE.d * s], tone: TONE.ACCENT, group: id, ...extra },
    { p: chip, s: [BADGE.w * 0.5 * s, BADGE.h * 0.15 * s, 0.06 * s], tone: TONE.SHEET, group: id, ...extra },
  ]
}

function lanyard(side: Side, order?: number): Cell[] {
  const t = turnOf(side), s = PERSON.scale, L = local(side)
  return [-1, 1].map(sx => ({ p: L(sx * 0.42, BADGE.y + 1.5, 0.72), s: [0.08 * s, 1.3 * s, 0.06 * s] as Vec3, tone: TONE.MID, group: `lanyard-${side}`, yaw: t, roll: -sx * 0.38, ...(order != null ? { order } : {}) }))
}

/* ───────────── the window ───────────── */

function viewerWindow(side: Side, s: SecurityState): Part {
  const id = `station-${side}`
  const cx = winCx(side), x0 = cx - WIN.w / 2
  const P = kit.merge()
  const enter = s.motion === 'enter'
  const ord = (o: number): Partial<Cell> => (enter ? { kind: 'scale', order: o } : {})
  for (let i = 0; i < WIN.w; i++) for (let j = 0; j < WIN.h; j++) {
    if (!(i === 0 || i === WIN.w - 1 || j === 0 || j >= WIN.h - WIN.tb)) continue
    // the frame draws itself in from the bottom-outer corner, like the station stepping forward
    const outer = side === 'a' ? i / WIN.w : 1 - i / WIN.w
    P.cells.push({ p: [x0 + i + 0.5, j + 0.5, 0], s: [0.97, 0.97, 1], tone: TONE.MID, group: id, ...ord(0.42 + 0.14 * (j / WIN.h) + 0.06 * outer) })
  }
  const sy0 = 1, sy1 = WIN.h - WIN.tb
  P.cells.push({ p: [cx, (sy0 + sy1) / 2, -0.2], s: [WIN.w - 2, sy1 - sy0, 0.6], tone: TONE.MID, group: id, ...ord(0.5) })
  P.cells.push({ p: [cx, (sy0 + sy1) / 2, 0.25], s: [WIN.w - 2.1, sy1 - sy0 - 0.1, 0.3], tone: TONE.SCREEN, group: `${id}-screen`, ...ord(0.56) })
  for (let k = 0; k < 3; k++) P.cells.push({ p: [x0 + 1.25 + k * 0.92, TB_Y, 0.62], s: [0.56, 0.56, 0.3], tone: TONE.INK, group: id, ...ord(0.6) })
  const bx0 = BAR.x0(side), bx1 = BAR.x1(side)
  P.cells.push({ p: [(bx0 + bx1) / 2, TB_Y, 0.6], s: [bx1 - bx0, BAR.h, 0.2], tone: TONE.SHEET, group: `${id}-bar`, ...ord(0.62) })
  // an empty token slot: dashed while unclaimed; with no claim at all it is the anchor of the NO CLAIM callout
  const claim = s[side]
  if (!s.claimed || claim === 'None') {
    const g = claim === 'None' && s.claimed ? `void-${side}` : `slot-${side}`
    P.cells.push({ p: slotAt(side), s: [TOKEN_S[0], TOKEN_S[1], 0.08], tone: TONE.GHOST, group: g, ...ord(0.66) })
  }
  return P
}

/* ───────────── the table (sorted by region: four bands of three rows) ───────────── */

/** Per-row values, derived from the 14-day revenue by region (stage/data) with a seeded ±12% spread per store. */
const VALUES = (() => {
  const r = kit.rng(0x5ec)
  const rev = REGIONS.flatMap((_, b) => Array.from({ length: PER_BAND }, () => (REGION_REV_K[b] / PER_BAND) * (0.88 + 0.24 * r())))
  const max = Math.max(...rev)
  return rev.map((v, i) => ({ rev: v / max, store: 2 + Math.round(r() * 1.2) + (i % 2) * 0 }))
})()
const REV_CUBES = 7

export function isVisible(side: Side, r: number, s: SecurityState): boolean {
  if (!s.filtered) return true
  const c = s[side]
  return c !== 'None' && regionOfRow(r) === c
}

function table(side: Side, s: SecurityState): Part {
  const P = kit.merge()
  const x0 = tableX0(side), z = Z_SCREEN
  const enter = s.motion === 'enter'
  // header: unit cubes (INK) across the table, labels are surface text (onInk)
  const hy = tableTop - T.head / 2
  for (let i = 0; i < Math.round(TABLE_W); i++) {
    P.cells.push({ p: [x0 + i + 0.5, hy, z + 0.25], s: [0.95, T.head, 0.5], tone: TONE.INK, group: `table-${side}-head`, ...(enter ? { kind: 'scale' as const, order: 0.64 + 0.06 * (i / TABLE_W) } : {}) })
  }
  const band = bandOf(s[side])
  for (let r = 0; r < ROWS; r++) {
    const g = `table-${side}-r${r}`, y = rowY(r)
    const extra = rowMotion(side, r, s)
    const cells: Cell[] = []
    const visible = isVisible(side, r, s)
    const v = VALUES[r]
    const nS = v.store, nR = Math.max(1, Math.round(v.rev * REV_CUBES))
    const storeX = (i: number) => x0 + COL.store[0] + i + 0.5, revX = (i: number) => x0 + COL.rev[0] + i + 0.5
    if (visible) {
      cells.push({ p: [x0 + 1.35, y, z + 0.4], s: [2.7, T.rowH, 0.8], tone: TONE.ACCENT, group: g })
      // STORE: a greeked id (2–3 short cubes); REVENUE: a data bar of unit cubes
      for (let i = 0; i < nS; i++) cells.push({ p: [storeX(i), y, z + 0.25], s: [0.92, 0.34, 0.5], tone: TONE.INK, group: g })
      for (let i = 0; i < nR; i++) cells.push({ p: [revX(i), y, z + 0.35], s: [0.92, 0.5, 0.7], tone: TONE.INK, group: g })
    }
    // Hidden line: the row's cells as flat, recessed, dashed outlines. They share the screen's group so the
    // contour pass leaves them alone (only their own dashed edges draw). Every row always carries its five
    // segments, so instance matching stays row-to-row; on a visible row they shrink to specks inside the first
    // cube of each column, and hiding the row unrolls the outline from there.
    const ghostG = `station-${side}-screen`
    const seg = (a: number, w: number, hideAt: number): Cell => visible
      ? { p: [hideAt, y, z + 0.3], s: [0.01, 0.01, 0.01], tone: TONE.GHOST, group: ghostG }
      : { p: [x0 + a + w / 2, y, z + 0.07], s: [w - 0.04, T.rowH, 0.14], tone: TONE.GHOST, group: ghostG }
    const revW = (COL.rev[1] - COL.rev[0]) / 3
    cells.push(seg(COL.chip[0], COL.chip[1] - COL.chip[0], x0 + 1.35), seg(COL.store[0], COL.store[1] - COL.store[0], storeX(0)))
    for (let k = 0; k < 3; k++) cells.push(seg(COL.rev[0] + k * revW, revW, revX(0)))
    for (const c of cells) Object.assign(c, extra)
    P.cells.push(...cells)
  }
  return P
}

/** Arrival order of row r of `side` for the transition this layout is the destination of (0..1). */
function rowOrder(side: Side, r: number, s: SecurityState): number | undefined {
  const b = Math.floor(r / PER_BAND), band = bandOf(s[side])
  switch (s.motion) {
    case 'enter': return 0.7 + 0.3 * (r / (ROWS - 1))                                   // tile in, top to bottom
    case 'rows': {                                                                       // farthest band first
      const d = band < 0 ? BANDS - 1 - b : Math.abs(b - band)
      return Math.min(1, 1 - d / (BANDS - 1) + (r % PER_BAND) * 0.04)
    }
    case 'cards':                                                                        // re-slice as each card lands
      return (side === 'a' ? 0.5 : 0.78) + (band < 0 ? 0 : Math.abs(b - band) / (BANDS - 1)) * 0.14 + (r % PER_BAND) * 0.02
    default: return undefined
  }
}
function rowMotion(side: Side, r: number, s: SecurityState): Partial<Cell> {
  const order = rowOrder(side, r, s)
  return order == null ? {} : { kind: 'scale', order }
}

/**
 * MorphSpec.order for a transition into state `s`. Cells that LEAVE (a row's bars when it hides) have no
 * destination cell, so the engine times them with the spec's order key; this key gives them the same order as
 * the row's arriving cells. Cards key 0 and lanyards key 1 pin the normalisation to the identity.
 */
export function orderKey(s: SecurityState): (c: Cell) => number {
  return c => {
    if (c.group.startsWith('card-')) return 0
    if (c.group.startsWith('lanyard-')) return 1
    const side: Side = c.p[0] < 0 ? 'a' : 'b'
    if (Math.abs(c.p[0] - winCx(side)) < WIN.w / 2 - 1) {
      for (let r = 0; r < ROWS; r++) if (Math.abs(c.p[1] - rowY(r)) < 0.3) return Math.min(1, rowOrder(side, r, s) ?? 0.5)
    }
    return 0.5
  }
}

/**
 * MorphSpec.order for the `in` ("step forward" from the cache): everything that is not ours sinks in the
 * first ~60% of the morph, farthest first, so the crowd has cleared the floor before the big stations build.
 * Our own cells carry explicit orders (.42 → 1); this key only ranks the leavers and pins the range.
 */
const OURS = /^(station-|table-|viewer-|lanyard-|card-|token-|slot-|void-)/
export function enterOrderKey(c: Cell): number {
  if (OURS.test(c.group)) return c.order ?? 1
  return 0.42 * Math.min(1, Math.max(0, (c.p[2] + 16) / 30))
}

/* ───────────── a viewer: person + lanyard + card + token ───────────── */

/**
 * A dignified pictogram in the Aicher manner: the head floats free of the shoulders, the arms hang clear of the
 * torso, the legs stand apart. No face, no hands. Height 9.3 × scale; it wears the kit's 'l' badge at chest height.
 */
function figure(side: Side, enter: boolean): Cell[] {
  const L = local(side), s = PERSON.scale, t = turnOf(side), g = `viewer-${side}`
  const out: Cell[] = []
  const B = (dx: number, y: number, dz: number, sx: number, sy: number, sz: number, roll = 0, order = 0) =>
    out.push({ p: L(dx, y, dz), s: [sx * s, sy * s, sz * s], tone: TONE.INK, group: g, yaw: t, ...(roll ? { roll } : {}), ...(enter ? { kind: 'scale' as const, order } : {}) })
  B(-0.5, 2.0, 0, 0.84, 4.0, 0.9, 0, 0.5); B(0.5, 2.0, 0, 0.84, 4.0, 0.9, 0, 0.52)           // legs, apart
  B(0, 5.78, 0, 2.3, 3.5, 1.16, 0, 0.56)                                                      // torso
  B(-1.6, 5.62, 0, 0.6, 3.3, 0.66, -0.035, 0.6); B(1.6, 5.62, 0, 0.6, 3.3, 0.66, 0.035, 0.6) // arms, clear of the torso
  B(0, 8.6, 0, 1.4, 1.5, 1.4, 0, 0.64)                                                        // head, floating free
  return out
}

function viewer(side: Side, s: SecurityState): Part {
  const claim = s[side]
  const enter = s.motion === 'enter'
  const P = kit.merge({ cells: figure(side, enter), groups: { [`viewer-${side}`]: { hover: true, click: true, liftOnHover: [0, 0, 0] } }, anchors: {}, texts: [] })
  if (claim === 'None') return P
  P.cells.push(...lanyard(side, enter ? 0.72 : undefined))
  // the card landing on B arcs over the top; the one landing on A slides straight across beneath it
  const cardKind: MorphKind | undefined = s.motion === 'cards' ? (side === 'b' ? 'arc' : 'slide') : enter ? 'scale' : undefined
  const id = cardOn(side, s)
  P.cells.push(...card(id, side, cardKind, s.motion === 'cards' ? 0 : enter ? 0.76 : undefined))
  P.groups[id] = { hover: true, click: true, liftOnHover: [0, 0, 0.6] }
  // the token: tucked inside the card until claimed, then docked in the query bar's slot
  const tk: Partial<Cell> = s.motion === 'token' ? { kind: 'arc', order: 0 } : enter ? { kind: 'scale', order: 0.76 } : {}
  if (!s.claimed) P.cells.push({ p: badgeAt(side), s: [BADGE.w * PERSON.scale * 0.7, BADGE.h * PERSON.scale * 0.3, 0.06], tone: TONE.ACCENT, group: `token-${side}`, yaw: turnOf(side), ...tk })
  else P.cells.push({ p: slotAt(side), s: TOKEN_S, tone: TONE.ACCENT, group: `token-${side}`, ...tk })
  return P
}

/* ───────────── the scene ───────────── */

export function securityLayout(s: SecurityState) {
  return kit.merge(viewerWindow('a', s), table('a', s), viewer('a', s), viewerWindow('b', s), table('b', s), viewer('b', s))
}

/* ───────────── surface text (positions are static; text is a function of state) ───────────── */

export const HEAD_LABELS: [number, string][] = [[COL.chip[0], 'REGION'], [COL.store[0], 'STORE'], [COL.rev[0], 'REVENUE']]

export function texts() {
  const out: { id: string; at: Vec3; text: string | ((s: SecurityState) => string); font: 'mono' | 'mono-strong' | 'ui'; size: number; tone: 'ink' | 'muted' | 'accent' | 'onInk'; plane?: { right: Vec3; up: Vec3 }; maxWidth?: number; reveal?: 'type'; when: 'all' | 'same' | 'claimed' }[] = []
  for (const side of ['a', 'b'] as Side[]) {
    const x0 = tableX0(side)
    const hy = tableTop - T.head / 2
    for (const [cx, label] of HEAD_LABELS) out.push({ id: `${side}-h-${label}`, at: [x0 + cx + 0.26, hy - 0.25, Z_SCREEN + 0.52], text: label, font: 'mono-strong', size: 0.5, tone: 'onInk', when: 'all' })
    for (let r = 0; r < ROWS; r++) {
      const region = regionOfRow(r)
      out.push({ id: `${side}-chip-${r}`, at: [x0 + 0.22, rowY(r) - 0.25, Z_SCREEN + 0.82], text: (s: SecurityState) => (isVisible(side, r, s) ? region : ''), font: 'mono-strong', size: 0.5, tone: 'onInk', maxWidth: 2.55, when: 'all' })
    }
    // query bar: the table name while unclaimed; the predicate once the token docks (typed in on `claim`)
    const qx = side === 'a' ? slotAt(side)[0] + TOKEN_S[0] / 2 + 0.42 : BAR.x0(side) + 0.5
    const qmax = side === 'a' ? BAR.x1(side) - qx - 0.3 : slotAt(side)[0] - TOKEN_S[0] / 2 - 0.3 - qx
    const pred = (s: SecurityState) => (!s.claimed ? '' : s[side] === 'None' ? 'no claim: deny' : `region = '${s[side]}'`)
    out.push({ id: `${side}-q-table`, at: [qx, TB_Y - 0.24, 0.72], text: (s: SecurityState) => (s.claimed ? '' : 'FROM orders'), font: 'mono', size: 0.5, tone: 'muted', when: 'all' })
    out.push({ id: `${side}-q-same`, at: [qx, TB_Y - 0.24, 0.72], text: pred, font: 'mono', size: 0.5, tone: 'ink', maxWidth: qmax, when: 'same' })
    out.push({ id: `${side}-q-pred`, at: [qx, TB_Y - 0.24, 0.72], text: pred, font: 'mono', size: 0.5, tone: 'ink', maxWidth: qmax, reveal: 'type', when: 'claimed' })
    // the region printed on the worn card
    const bt = badgeText(side)
    out.push({ id: `${side}-badge`, at: bt.at, plane: bt.plane, text: (s: SecurityState) => (s[side] === 'None' ? '' : s[side]), font: 'mono-strong', size: bt.size, tone: 'onInk', maxWidth: bt.maxWidth, when: 'all' })
  }
  return out
}

/* ───────────── anchors for callouts ───────────── */

export const ANCHOR = {
  between: [0, WIN.h + 0.2, 0.5] as Vec3,
  winTopRight: (side: Side): Vec3 => [winCx(side) + WIN.w / 2 - 1.5, WIN.h, 0.5],
  badge: badgeAt,
  /** Highest point of the token's flight on `claim` (straight line + arc·4k(1−k) in y). */
  claimApex: (side: Side, arc: number): Vec3 => {
    const a = badgeAt(side), b = slotAt(side)
    let best: Vec3 = b
    for (let i = 0; i <= 64; i++) {
      const k = i / 64, p: Vec3 = [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k + arc * 4 * k * (1 - k), a[2] + (b[2] - a[2]) * k]
      if (p[1] > best[1]) best = p
    }
    return best
  },
  feet: (side: Side): Vec3 => [personX(side), 0, PERSON.z + 1],
}
