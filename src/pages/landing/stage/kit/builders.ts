/**
 * Voxel kit builders (storyboard §3.1). Pure TypeScript: no three, no DOM, no
 * randomness except the seeded rng. Every builder returns a Part
 * { cells, groups, anchors, texts }; compose parts with merge().
 *
 * Conventions (world units, y up, floor y = 0, +z toward the viewer):
 * - Upright objects face +z. `at` is documented per builder (usually bottom-centre or top-left).
 * - Group names are `${id}` for the body and `${id}-<part>` for sub-parts, so pass a stable
 *   `id` per object and the same id on every beat where the object persists.
 * - `texts` are surface-text seeds (no beats); turn them into SurfaceText with kit.surface().
 * Proportions are the ones validated in look-dev (r2/tools-ad/lookdev/lookdev.js).
 */
import { TONE } from '../types'
import type { Cell, GroupInfo, Layout, SurfaceText, Vec3 } from '../types'
import { FONT, measureVoxelText } from './font'

export type TextSeed = Omit<SurfaceText, 'beats'>
export interface Part { cells: Cell[]; groups: Record<string, GroupInfo>; anchors: Record<string, Vec3>; texts: TextSeed[] }

const part = (): Part => ({ cells: [], groups: {}, anchors: {}, texts: [] })
const cell = (P: Part, p: Vec3, s: Vec3, tone: number, group: string, extra?: Partial<Cell>) => {
  const c: Cell = { p, s, tone, group }
  if (extra) Object.assign(c, extra)
  P.cells.push(c)
  return c
}

/** One cube (escape hatch for scene-specific details). */
export function box(o: { p: Vec3; s?: Vec3; tone: number; group?: string } & Partial<Cell>): Part {
  const P = part()
  const { p, s = [0.95, 0.95, 0.95], tone, group = '', ...rest } = o
  cell(P, p, s, tone, group, rest)
  return P
}

/** Compose parts. Later anchors/groups with the same name win. */
export function merge(...parts: Array<Part | Cell[] | null | undefined | false>): Part & Layout {
  const out = part()
  for (const p of parts) {
    if (!p) continue
    if (Array.isArray(p)) { out.cells.push(...p); continue }
    out.cells.push(...p.cells)
    Object.assign(out.groups, p.groups)
    Object.assign(out.anchors, p.anchors)
    out.texts.push(...p.texts)
  }
  return out
}

/** Translate a part (cells, anchors and text seeds). */
export function offset(P: Part, d: Vec3): Part {
  const add = (v: Vec3): Vec3 => [v[0] + d[0], v[1] + d[1], v[2] + d[2]]
  return {
    cells: P.cells.map(c => ({ ...c, p: add(c.p) })),
    groups: P.groups,
    anchors: Object.fromEntries(Object.entries(P.anchors).map(([k, v]) => [k, add(v)])),
    texts: P.texts.map(t => ({ ...t, at: add(t.at) })),
  }
}

/** Re-tone every cell of a part (or only cells whose group passes `which`). */
export function retone(P: Part, tone: number, which: (group: string) => boolean = () => true): Part {
  return { ...P, cells: P.cells.map(c => (which(c.group) ? { ...c, tone } : c)) }
}

/** Surface-text seeds → SurfaceText entries visible on `beats`. */
export function surface(texts: TextSeed[], beats: string[], patch?: Partial<SurfaceText>): SurfaceText[] {
  return texts.map(t => ({ ...t, ...patch, beats }))
}

/* ───────────── voxel numerals ───────────── */

/**
 * Voxel text in the 3×5 font (0-9 . , : % k M $). `at` = top-left of the first glyph.
 * plane 'front': upright, facing +z (cells at z = at[2]). plane 'top': lying flat, rows run toward +z.
 * Each pixel is a cube of .9px × .9px × 1.1px (1.1 along the plane normal).
 */
export function voxelText(o: { text: string; at: Vec3; px?: number; tone?: number; plane?: 'front' | 'top'; group?: string; id?: string }): Part & { width: number } {
  const { text, at, px = 0.5, tone = TONE.INK, plane = 'front' } = o
  const group = o.group ?? o.id ?? ''
  const P = part()
  let x = at[0]
  for (const ch of text) {
    const g = FONT[ch]
    if (!g) { x += px * 2; continue }
    g.forEach((row, r) => [...row].forEach((b, c) => {
      if (b !== '1') return
      const cx = x + (c + 0.5) * px
      if (plane === 'front') cell(P, [cx, at[1] - (r + 0.5) * px, at[2]], [px * 0.9, px * 0.9, px * 1.1], tone, group)
      else cell(P, [cx, at[1], at[2] + (r + 0.5) * px], [px * 0.9, px * 1.1, px * 0.9], tone, group)
    }))
    x += (g[0].length + 1) * px
  }
  const width = measureVoxelText(text, px)
  P.anchors.end = plane === 'front' ? [at[0] + width, at[1], at[2]] : [at[0] + width, at[1], at[2]]
  return Object.assign(P, { width })
}

/* ───────────── browser window ───────────── */

export interface WindowPart extends Part { screen: { x0: number; x1: number; y0: number; y1: number; zFront: number } }

/**
 * Upright browser window facing +z. `at` = bottom-centre [cx, y, z].
 * Groups: `${id}` frame + chrome, `${id}-screen` screen tiles, `${id}-tab`, `${id}-url`.
 * Hero size 52×34; viewer stations 6×4.4 (`small`, titleBar 1). `tiles` = screen tile pitch.
 */
export function browserWindow(o: { id?: string; at?: Vec3; w?: number; h?: number; titleBar?: number; small?: boolean; tiles?: number; title?: string; url?: string; screenTone?: number; frameTone?: number }): WindowPart {
  const { id = 'win', at = [0, 0, 0], w = 52, h = 34, titleBar: tb = 3, small = false, tiles = 1, title, url, screenTone = TONE.SCREEN, frameTone = TONE.MID } = o
  const [cx, by, z] = at
  const P = part()
  const x0 = cx - w / 2
  const W = Math.round(w), H = Math.round(h)
  const sx = w / W, sy = h / H                                   // stretch for non-integer sizes (small stations)
  for (let i = 0; i < W; i++) for (let j = 0; j < H; j++) {
    const inTitle = j >= H - Math.round(tb / sy), inFrame = i === 0 || i === W - 1 || j === 0
    if (inTitle || inFrame) cell(P, [x0 + (i + 0.5) * sx, by + (j + 0.5) * sy, z], [0.97 * sx, 0.97 * sy, 1], frameTone, id)
  }
  const sx0 = x0 + sx, sx1 = x0 + w - sx, sy0 = by + sy, sy1 = by + h - Math.round(tb / sy) * sy
  cell(P, [(sx0 + sx1) / 2, (sy0 + sy1) / 2, z - 0.2], [sx1 - sx0, sy1 - sy0, 0.6], frameTone, id)        // back plate
  const gs = `${id}-screen`
  if (small) cell(P, [(sx0 + sx1) / 2, (sy0 + sy1) / 2, z + 0.25], [sx1 - sx0 - 0.1, sy1 - sy0 - 0.1, 0.3], screenTone, gs)
  else {
    for (let x = sx0; x < sx1 - 1e-6; x += tiles) for (let y = sy0; y < sy1 - 1e-6; y += tiles) {
      const tw = Math.min(tiles, sx1 - x), th = Math.min(tiles, sy1 - y)
      cell(P, [x + tw / 2, y + th / 2, z + 0.25], [0.97 * tw, 0.97 * th, 0.3], screenTone, gs)
    }
  }
  const ty = by + h - (Math.round(tb / sy) * sy) / 2
  if (!small) {
    for (let k = 0; k < 3; k++) cell(P, [x0 + 1.6 + k * 1.25, ty, z + 0.6], [0.7, 0.7, 0.3], TONE.INK, id)
    cell(P, [x0 + 9.5, ty - 0.25, z + 0.58], [9, tb - 0.9, 0.22], TONE.SCREEN, `${id}-tab`)
    const ux0 = x0 + 15.5, ux1 = x0 + w - 2
    cell(P, [(ux0 + ux1) / 2, ty, z + 0.58], [ux1 - ux0, 1.3, 0.2], TONE.SHEET, `${id}-url`)
    if (title) P.texts.push({ id: `${id}-title`, at: [x0 + 5.6, ty - 0.55, z + 0.71], text: title, font: 'ui', size: 0.62, tone: 'ink', maxWidth: 7.6 })
    if (url) P.texts.push({ id: `${id}-url`, at: [ux0 + 0.7, ty - 0.25, z + 0.71], text: url, font: 'mono', size: 0.5, tone: 'muted', maxWidth: ux1 - ux0 - 1.4 })
    P.anchors.title = [x0 + 5.6, ty, z + 0.7]
    P.anchors.url = [ux0 + 0.7, ty, z + 0.7]
  } else {
    for (let k = 0; k < 3; k++) cell(P, [x0 + 0.9 + k * 0.8, ty, z + 0.55], [0.42, 0.42, 0.2], TONE.INK, id)
    P.anchors.title = [x0 + 0.9, ty, z + 0.6]
    P.anchors.url = [cx, ty, z + 0.6]
  }
  P.anchors.status = [sx0 + 0.8, sy0 + 0.6, z + 0.45]
  P.anchors.top = [cx, by + h, z]
  P.anchors.centre = [cx, by + h / 2, z + 0.5]
  P.anchors.screen = [(sx0 + sx1) / 2, (sy0 + sy1) / 2, z + 0.45]
  P.groups[gs] = {}
  return Object.assign(P, { screen: { x0: sx0, x1: sx1, y0: sy0, y1: sy1, zFront: z + 0.4 } })
}

/* ───────────── dashboard pieces ───────────── */

/**
 * KPI tile on an upright screen. `at` = top-left [x0, yTop, zScreenFront].
 * Plate of unit cells (SHEET), value in voxel numerals (px .52), caption as surface text.
 * Groups: `${id}` plate (hover), `${id}-value` numerals.
 */
export function kpiTile(o: { id?: string; at: Vec3; w?: number; h?: number; value: string; caption: string; accent?: boolean; px?: number; hover?: boolean; tooltip?: string }): Part {
  const { id = 'kpi', at, w = 11, h = 5.4, value, caption, accent = false, px = 0.52, hover = true, tooltip } = o
  const [x0, yt, z] = at
  const P = part()
  const W = Math.round(w), H = Math.ceil(h - 1e-6)
  const sx = w / W, sy = h / H
  for (let i = 0; i < W; i++) for (let j = 0; j < H; j++) cell(P, [x0 + (i + 0.5) * sx, yt - (j + 0.5) * sy, z + 0.2], [0.96 * sx, 0.96 * sy, 0.4], TONE.SHEET, id)
  const v = voxelText({ text: value, at: [x0 + 0.8, yt - 1.8, z + 0.55], px, tone: accent ? TONE.ACCENT : TONE.INK, group: `${id}-value` })
  P.cells.push(...v.cells)
  P.texts.push({ id: `${id}-caption`, at: [x0 + 0.75, yt - 1.15, z + 0.42], text: caption, font: 'mono', size: 0.5, tone: 'muted', maxWidth: w - 1.4 })
  P.groups[id] = { hover, tooltip }
  P.groups[`${id}-value`] = { hover, tooltip }
  P.anchors[id] = [x0 + w / 2, yt, z + 0.4]
  P.anchors[`${id}-front`] = [x0 + w / 2, yt - h / 2, z + 0.9]
  return P
}

/**
 * Bar chart standing on an upright screen; bars extrude toward +z.
 * `at` = [x0, yBase, zScreenFront]. Group per bar `${id}-${i}` (hover, lift .8 toward the viewer), axis `${id}-axis`.
 * The selected bar is ACCENT; with `dim`, unselected bars are PAPER.
 */
export function barChart(o: { id?: string; at: Vec3; values: number[]; max: number; height: number; barW?: number; gap?: number; depth?: number; sel?: number; dim?: boolean; click?: boolean; wave?: boolean }): Part {
  const { id = 'bar', at, values, max, height, barW = 1, gap = 0.85, depth = 1.3, sel = -1, dim = false, click = false, wave = false } = o
  const [x0, yb, z] = at
  const P = part()
  const span = values.length * (barW + gap) - gap
  cell(P, [x0 + span / 2, yb - 0.1, z + 0.15], [span + 0.6, 0.14, 0.3], TONE.INK, `${id}-axis`)
  values.forEach((v, b) => {
    const g = `${id}-${b}`
    const hh = Math.max(1, Math.round((v / max) * height))
    const tone = b === sel ? TONE.ACCENT : (dim && sel >= 0 ? TONE.PAPER : TONE.INK)
    const x = x0 + b * (barW + gap) + barW / 2
    for (let u = 0; u < hh; u++) cell(P, [x, yb + u + 0.5, z + depth / 2], [barW * 0.9, 0.92, depth], tone, g, wave ? { wave: true } : undefined)
    P.groups[g] = { hover: true, click, liftOnHover: [0, 0, 0.8] }
    P.anchors[g] = [x, yb + hh, z + depth]
  })
  P.anchors[`${id}-axis`] = [x0, yb, z]
  return P
}

/** Stepped line chart (ACCENT, depth .7) with an area (ACCENT_SOFT, depth .2). `at` = [x0, yBase, zScreenFront]. Groups `${id}`, `${id}-area`. */
export function lineChart(o: { id?: string; at: Vec3; values: number[]; max: number; height: number; area?: boolean; step?: number }): Part {
  const { id = 'line', at, values, max, height, area = true, step = 1 } = o
  const [x0, yb, z] = at
  const P = part()
  const hOf = (v: number) => Math.max(1, Math.round((v / max) * height))
  values.forEach((v, i) => {
    const hh = hOf(v), x = x0 + i * step + step / 2
    if (area) for (let u = 0; u < hh - 1; u++) cell(P, [x, yb + u + 0.5, z + 0.12], [0.97 * step, 0.97, 0.2], TONE.ACCENT_SOFT, `${id}-area`)
    cell(P, [x, yb + hh - 0.5, z + 0.45], [0.97 * step, 0.97, 0.7], TONE.ACCENT, id)
    const prev = i ? hOf(values[i - 1]) : hh
    for (let u = Math.min(prev, hh); u < Math.max(prev, hh) - 1; u++) cell(P, [x, yb + u + 0.5, z + 0.45], [0.97 * step, 0.97, 0.7], TONE.ACCENT, id)
    P.anchors[`${id}-${i}`] = [x, yb + hh, z + 0.8]
  })
  P.groups[id] = { hover: true, liftOnHover: [0, 0, 0.5] }
  return P
}

/**
 * Horizontal bars with surface-text labels. `at` = [x0, yTopRow, zScreenFront].
 * Bars 1.6 tall, depth 1.2, starting at x0 + labelW. Group per bar `${id}-${i}`.
 */
export function hbarChart(o: { id?: string; at: Vec3; items: [string, number][]; max: number; len: number; pitch?: number; labelW?: number; sel?: number; dim?: boolean; wave?: boolean }): Part {
  const { id = 'hbar', at, items, max, len, pitch = 3.2, labelW = 3.6, sel = -1, dim = false, wave = false } = o
  const [x0, yt, z] = at
  const P = part()
  items.forEach(([label, v], r) => {
    const g = `${id}-${r}`, y = yt - r * pitch, n = Math.max(1, Math.round((v / max) * len))
    const tone = r === sel ? TONE.ACCENT : (dim && sel >= 0 ? TONE.PAPER : TONE.INK)
    for (let u = 0; u < n; u++) cell(P, [x0 + labelW + u + 0.5, y, z + 0.6], [0.92, 1.6, 1.2], tone, g, wave ? { wave: true } : undefined)
    P.texts.push({ id: g, at: [x0, y - 0.25, z + 0.2], text: label, font: 'mono', size: 0.5, tone: 'ink', maxWidth: labelW - 0.3 })
    P.groups[g] = { hover: true, liftOnHover: [0, 0, 0.6] }
    P.anchors[g] = [x0 + labelW + n, y, z + 1.2]
  })
  return P
}

/* ───────────── tables ───────────── */

/**
 * plane 'floor': a table lying on the floor. `at` = centre [cx, 0, cz]. Cells of cellW × 1 cubes, .4 tall;
 * header row .6 tall, INK, names as surface text (onInk, plane 'top'). Group per column `${id}-c${c}`.
 *
 * plane 'screen': an upright table. `at` = top-left [x0, yTop, zScreenFront]. A header row (INK), then `rows`
 * rows at `pitch`. A visible row: region chip (2.7 wide, chipTone, raised) + `cols` value bars (INK, raised .8).
 * A hidden row (hidden(r) true) is flat (.14), GHOST and recessed. Group per row `${id}-r${r}`, header `${id}-head`.
 */
export function table(o: {
  id?: string; at: Vec3; plane?: 'floor' | 'screen'; cols?: number; rows?: number; cellW?: number; colGap?: number; header?: boolean
  names?: string[]; rowTone?: (r: number, c: number) => number; hidden?: (r: number) => boolean
  valueBars?: (r: number, c: number) => number; chip?: (r: number) => string; chipTone?: (r: number) => number; pitch?: number; width?: number
}): Part {
  const { id = 'table', at, plane = 'floor', header = true } = o
  const P = part()
  if (plane === 'floor') {
    const { cols = 8, rows = 24, cellW = 4, colGap = 0.3, names, rowTone } = o
    const W = cols * cellW + (cols - 1) * colGap, x0 = at[0] - W / 2, z0 = at[2] - rows / 2
    for (let c = 0; c < cols; c++) {
      const g = `${id}-c${c}`
      for (let r = header ? -1 : 0; r < rows; r++) for (let i = 0; i < cellW; i++) {
        const x = x0 + c * (cellW + colGap) + i + 0.5, z = z0 + r + 0.5
        if (r < 0) cell(P, [x, 0.3, z], [0.96, 0.6, 0.92], TONE.INK, g)
        else cell(P, [x, 0.2, z], [0.96, 0.4, 0.92], rowTone ? rowTone(r, c) : TONE.PAPER, g)
      }
      const cxc = x0 + c * (cellW + colGap) + cellW / 2
      P.groups[g] = { hover: true, click: true }
      P.anchors[g] = [cxc, 0.6, z0 - 1]
      P.anchors[`${g}-front`] = [cxc, 0.4, z0 + rows]
      if (names?.[c] && header) P.texts.push({ id: g, at: [x0 + c * (cellW + colGap) + 0.3, 0.61, z0 - 0.25], plane: 'top', text: names[c], font: 'mono-strong', size: 0.45, tone: 'onInk', maxWidth: cellW - 0.4 })
    }
    P.anchors.corner = [x0 + W, 0.4, z0 + rows]
    P.anchors.centre = [at[0], 0.4, at[2]]
    return P
  }
  const { cols = 5, rows = 12, cellW = 3, pitch = 1.05, hidden, valueBars, chip, chipTone, width = 4.6 + cols * cellW - 1 } = o
  const [x0, yt, z] = at
  if (header) for (let i = 0; i < Math.round(width); i++) cell(P, [x0 + i + 0.5, yt - 0.5, z + 0.25], [0.95, 1, 0.45], TONE.INK, `${id}-head`)
  const top = header ? yt - 1.5 : yt
  for (let r = 0; r < rows; r++) {
    const g = `${id}-r${r}`, y = top - 0.5 - r * pitch
    if (hidden?.(r)) {
      for (let i = 0; i < Math.round(width); i++) cell(P, [x0 + i + 0.5, y, z + 0.12], [0.95, 0.86 * pitch, 0.14], TONE.GHOST, g)
    } else {
      cell(P, [x0 + 1.15 + 1.35, y, z + 0.45], [2.7, 0.9 * pitch, 0.8], chipTone ? chipTone(r) : TONE.ACCENT, g)
      for (let c = 0; c < cols; c++) {
        const v = valueBars ? valueBars(r, c) : 0.6
        const n = Math.max(1, Math.round(v * (cellW - 0.2)))
        for (let i = 0; i < n; i++) cell(P, [x0 + 4.6 + c * cellW + i + 0.5, y, z + 0.45], [0.92, 0.82 * pitch, 0.8], TONE.INK, g)
      }
      if (chip) P.texts.push({ id: g, at: [x0 + 1.4, y - 0.2, z + 0.86], text: chip(r), font: 'mono-strong', size: 0.45, tone: 'onInk', maxWidth: 2.4 })
    }
    P.groups[g] = { hover: true }
    P.anchors[g] = [x0 + width, y, z + 0.9]
  }
  P.anchors.head = [x0, yt, z + 0.5]
  return P
}

/** Arrow column towers. `at` = centre [cx, 0, cz]. Top layer INK, the read tower ACCENT, other bodies PAPER (GHOST when `ghost`). Group per tower `${id}-${c}`. */
export function columns(o: { id?: string; at?: Vec3; n?: number; w?: number; d?: number; h?: number; gap?: number; read?: number; ghost?: boolean }): Part {
  const { id = 'col', at = [0, 0, 0], n = 8, w = 4, d = 4, h = 6, gap = 6.4, read = -1, ghost = false } = o
  const P = part()
  for (let c = 0; c < n; c++) {
    const g = `${id}-${c}`, cx = at[0] + (c - (n - 1) / 2) * gap
    for (let i = 0; i < w; i++) for (let k = 0; k < d; k++) for (let j = 0; j < h; j++) {
      const tone = j === h - 1 ? TONE.INK : c === read ? TONE.ACCENT : ghost && read >= 0 ? TONE.GHOST : TONE.PAPER
      cell(P, [cx + i - (w - 1) / 2, j + 0.5, at[2] + k - (d - 1) / 2], [0.9, 0.9, 0.9], tone, g)
    }
    P.groups[g] = { hover: true, click: true, liftOnHover: [0, 0.6, 0] }
    P.anchors[g] = [cx, h + 0.2, at[2]]
    P.anchors[`${g}-front`] = [cx, h / 2, at[2] + d / 2]
  }
  return P
}

/* ───────────── infrastructure ───────────── */

/** Database cylinder: rings of n cells yawed to the tangent, INK band rings, concentric cap. `at` = base centre. Group `${id}`. */
export function cylinder(o: { id?: string; at?: Vec3; r?: number; h?: number; n?: number; bands?: number[]; bandTone?: number }): Part {
  const { id = 'db', at = [0, 0, 0], r = 5.2, h = 9, n = 30, bands = [0, 3, 6], bandTone = TONE.INK } = o
  const [cx, by, cz] = at
  const P = part()
  const tw = (2 * Math.PI * r / n) * 1.04
  for (let j = 0; j < h; j++) for (let k = 0; k < n; k++) {
    const a = (k / n) * Math.PI * 2
    cell(P, [cx + r * Math.cos(a), by + j + 0.5, cz + r * Math.sin(a)], [tw, 0.94, 0.9], bands.includes(j) ? bandTone : TONE.PAPER, id, { yaw: -a + Math.PI / 2 })
  }
  for (const rr of [r - 0.9, r - 2, r - 3.1]) {
    if (rr <= 0.5) continue
    const nn = Math.max(6, Math.round((n * rr) / r))
    for (let k = 0; k < nn; k++) {
      const a = ((k + 0.5) / nn) * Math.PI * 2
      cell(P, [cx + rr * Math.cos(a), by + h - 0.2, cz + rr * Math.sin(a)], [(2 * Math.PI * rr / nn) * 1.04, 0.5, 0.95], TONE.PAPER, id, { yaw: -a + Math.PI / 2 })
    }
  }
  cell(P, [cx, by + h - 0.2, cz], [1.6, 0.5, 1.6], TONE.PAPER, id)
  P.groups[id] = { hover: true }
  P.anchors.top = [cx, by + h + 0.4, cz]
  P.anchors.front = [cx, by + h / 2, cz + r]
  P.anchors.base = [cx, by, cz + r]
  return P
}

/** Edge-cache rack: MID shell, open front, three drawers (SHEET, ACCENT when stored) with INK handles and LED cells. `at` = base centre. */
export function rack(o: { id?: string; at?: Vec3; w?: number; h?: number; d?: number; stored?: number; led?: number }): Part {
  const { id = 'rack', at = [0, 0, 0], w = 7, h = 9, d = 4, stored = 0, led = TONE.INK } = o
  const [cx, by, cz] = at
  const P = part()
  const x0 = cx - w / 2
  for (let i = 0; i < w; i++) for (let j = 0; j < h; j++) for (let k = 0; k < d; k++) {
    const shell = i === 0 || i === w - 1 || j === 0 || j === h - 1 || k === 0 || k === d - 1
    if (!shell) continue
    const front = k === d - 1
    if (front && i > 0 && i < w - 1 && j > 0 && j < h - 1) continue
    cell(P, [x0 + i + 0.5, by + j + 0.5, cz - d / 2 + k + 0.5], [0.97, 0.97, 0.97], TONE.MID, id)
  }
  for (let s = 0; s < 3; s++) {
    const y = by + 1.6 + s * 2.4, g = `${id}-drawer-${s}`, on = s < stored
    cell(P, [cx, y + 0.55, cz + d / 2 - 0.5], [w - 2, 1.9, 0.5], on ? TONE.ACCENT : TONE.SHEET, g)
    cell(P, [x0 + w - 1.7, y + 0.55, cz + d / 2 - 0.05], [0.3, 0.3, 0.2], TONE.INK, g)
    cell(P, [x0 + 1.6, y + 0.55, cz + d / 2 - 0.05], [0.3, 0.3, 0.2], on ? TONE.ACCENT : led, `${id}-led-${s}`)
    P.groups[g] = { hover: true }
    P.anchors[g] = [cx, y + 0.55, cz + d / 2]
  }
  P.groups[id] = { hover: true }
  P.anchors.front = [cx, by + h / 2, cz + d / 2]
  P.anchors.top = [cx, by + h + 0.3, cz]
  return P
}

/** Rotate a local offset by `turn` about y (positive = toward -x of the facing direction, as in look-dev). */
const turnP = (cx: number, cz: number, s: number, turn: number) => (dx: number, y: number, dz: number, by = 0): Vec3 =>
  [cx + (dx * Math.cos(turn) + dz * Math.sin(turn)) * s, by + y * s, cz + (-dx * Math.sin(turn) + dz * Math.cos(turn)) * s]

/** ID badge card (+ chip window) with the region as surface text. `at` = card centre. Group `${id}`. Sizes: m 1.1×1.45, l 1.6×2.1. */
export function badge(o: { id?: string; at: Vec3; region?: string; size?: 'm' | 'l'; tone?: number; turn?: number; scale?: number }): Part {
  const { id = 'badge', at, region, size = 'm', tone = TONE.ACCENT, turn = 0, scale: sc = 1 } = o
  const P = part()
  const [w, h] = size === 'l' ? [1.6, 2.1] : [1.1, 1.45]
  const T = turnP(at[0], at[2], sc, turn)
  const card = T(0, 0, 0); card[1] = at[1]
  cell(P, card, [w * sc, h * sc, 0.12 * sc], tone, id, { yaw: turn })
  const chip = T(0, 0, 0.08); chip[1] = at[1] + h * 0.27 * sc
  cell(P, chip, [w * 0.5 * sc, h * 0.15 * sc, 0.06 * sc], TONE.SHEET, id, { yaw: turn })
  if (region) {
    const tx = T(-w * 0.42, 0, 0.1); tx[1] = at[1] - h * 0.32 * sc
    P.texts.push({ id, at: tx, plane: { right: [Math.cos(turn), 0, -Math.sin(turn)], up: [0, 1, 0] }, text: region, font: 'mono-strong', size: (size === 'l' ? 0.36 : 0.26) * sc, tone: 'onInk', maxWidth: w * 0.84 * sc })
  }
  P.groups[id] = { hover: true, click: true, liftOnHover: [0, 0, 0.6] }
  P.anchors[id] = [card[0], card[1], card[2] + 0.1]
  P.anchors[`${id}-top`] = [card[0], card[1] + (h / 2) * sc, card[2]]
  return P
}

/**
 * A person as a pictogram (no face, no hands). `at` = feet centre [cx, 0, cz]. Height 9.4 × scale.
 * turn ±.35 toward the person's screen. Optional badge on a lanyard (group `${id}-badge`).
 */
export function person(o: { id?: string; at: Vec3; scale?: number; turn?: number; tone?: number; badge?: { region?: string; size?: 'm' | 'l'; tone?: number } }): Part {
  const { id = 'person', at, scale: s = 1, turn = 0, tone = TONE.INK } = o
  const P = part()
  const T = turnP(at[0], at[2], s, turn)
  const B = (dx: number, y: number, dz: number, sx: number, sy: number, sz: number, roll = 0) =>
    cell(P, T(dx, y, dz, at[1]), [sx * s, sy * s, sz * s], tone, id, { yaw: turn, ...(roll ? { roll } : {}) })
  B(-0.55, 1.95, 0, 0.95, 3.9, 0.95); B(0.55, 1.95, 0, 0.95, 3.9, 0.95)          // legs
  B(0, 5.65, 0, 2.6, 3.5, 1.3)                                                    // torso
  B(-1.72, 5.55, 0, 0.68, 3.3, 0.7); B(1.72, 5.55, 0, 0.68, 3.3, 0.7)             // arms
  B(0, 7.55, 0, 0.62, 0.35, 0.62)                                                 // neck
  B(0, 8.55, 0, 1.55, 1.7, 1.55)                                                  // head
  P.groups[id] = { hover: true, click: !!o.badge }
  P.anchors.head = T(0, 9.6, 0, at[1])
  P.anchors.chest = T(0, 6.4, 0.7, at[1])
  if (o.badge) {
    const L = o.badge.size === 'l'
    const by = L ? 5.2 : 5.5
    const b = badge({ id: `${id}-badge`, at: T(0, by, 0.72, at[1]), region: o.badge.region, size: o.badge.size, tone: o.badge.tone, turn, scale: s })
    for (const sx of [-1, 1]) {                                                    // lanyard rods (MID)
      const p = T(sx * 0.42, by + (L ? 1.5 : 1.25), 0.7, at[1])
      cell(P, p, [0.08 * s, 1.3 * s, 0.06 * s], TONE.MID, id, { yaw: turn, roll: -sx * 0.38 })
    }
    P.cells.push(...b.cells); Object.assign(P.groups, b.groups); Object.assign(P.anchors, b.anchors); P.texts.push(...b.texts)
    P.anchors.badge = b.anchors[`${id}-badge`]
  }
  return P
}

/** MCP port: 3×4×3 INK block with ACCENT sockets on both sides; "MCP" printed on the front. `at` = base centre. */
export function mcpPort(o: { id?: string; at: Vec3; sub?: boolean }): Part {
  const { id = 'mcp', at } = o
  const [cx, by, cz] = at
  const P = part()
  for (let i = 0; i < 3; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 3; k++) cell(P, [cx - 1 + i, by + j + 0.5, cz - 1 + k], [0.94, 0.94, 0.94], TONE.INK, id)
  for (const sx of [-1, 1]) for (const y of [1.5, 2.5]) cell(P, [cx + sx * 1.75, by + y, cz], [0.5, 0.5, 0.5], TONE.ACCENT, `${id}-socket`)
  P.texts.push({ id: `${id}-label`, at: [cx - 1.1, by + 2.2, cz + 1.48], text: 'MCP', font: 'mono-strong', size: 0.62, tone: 'onInk' })
  if (o.sub !== false) P.texts.push({ id: `${id}-sub`, at: [cx - 2.6, by - 1.1, cz + 1.5], text: '15 tools · OAuth', font: 'mono', size: 0.5, tone: 'muted' })
  P.groups[id] = { hover: true }
  P.anchors.left = [cx - 2, by + 2, cz]; P.anchors.right = [cx + 2, by + 2, cz]; P.anchors.top = [cx, by + 4.2, cz]
  return P
}

/** Engine chip: 7×7×1 INK plate with 5 MID pins per side, "DuckDB" printed on its face. Upright (faces +z) by default. `at` = centre. */
export function engineChip(o: { id?: string; at: Vec3; flat?: boolean; label?: string }): Part {
  const { id = 'engine', at, flat = false, label = 'DuckDB' } = o
  const [cx, cy, cz] = at
  const P = part()
  const put = (u: number, v: number, s: Vec3, tone: number, g: string) =>
    cell(P, flat ? [cx + u, cy, cz - v] : [cx + u, cy + v, cz], flat ? [s[0], s[2], s[1]] : s, tone, g)
  for (let i = 0; i < 7; i++) for (let j = 0; j < 7; j++) put(i - 3, j - 3, [0.96, 0.96, 1], TONE.INK, id)
  for (let k = 0; k < 5; k++) {
    const t = k - 2
    put(t * 1.2, 4.1, [0.3, 1.2, 0.3], TONE.MID, `${id}-pins`); put(t * 1.2, -4.1, [0.3, 1.2, 0.3], TONE.MID, `${id}-pins`)
    put(4.1, t * 1.2, [1.2, 0.3, 0.3], TONE.MID, `${id}-pins`); put(-4.1, t * 1.2, [1.2, 0.3, 0.3], TONE.MID, `${id}-pins`)
  }
  P.texts.push(flat
    ? { id: `${id}-label`, at: [cx - 2.6, cy + 0.52, cz + 0.3], plane: 'top', text: label, font: 'mono-strong', size: 0.8, tone: 'onInk' }
    : { id: `${id}-label`, at: [cx - 2.6, cy - 0.4, cz + 0.52], text: label, font: 'mono-strong', size: 0.8, tone: 'onInk' })
  P.groups[id] = { hover: true }
  P.anchors[id] = flat ? [cx, cy + 0.5, cz] : [cx, cy + 3.5, cz + 0.5]
  return P
}

export interface ChatMessage { role: 'user' | 'tool' | 'agent'; text: string }

/**
 * Chat panel, upright, facing +z. `at` = bottom-centre. 2×2 SCREEN tiles, MID header row, the first user message
 * as an INK bubble (onInk text), tool rows as SHEET slabs with an ACCENT mark (mono text), an input bar with a caret.
 * Groups `${id}`, `${id}-user`, `${id}-tool-${k}`, `${id}-input`. Anchors user, tool-k, input.
 */
export function chatPanel(o: { id?: string; at?: Vec3; w?: number; h?: number; messages?: ChatMessage[]; tools?: number }): Part {
  const { id = 'chat', at = [0, 0, 0], w = 16, h = 24, messages = [] } = o
  const [cx, by, z] = at
  const P = part()
  const x0 = cx - w / 2
  for (let i = 0; i < w; i += 2) for (let j = 0; j < h; j += 2) cell(P, [x0 + i + 1, by + j + 1, z], [1.94, 1.94, 0.6], j >= h - 2 ? TONE.MID : TONE.SCREEN, id)
  const user = messages.find(m => m.role === 'user')
  const bw = w - 5.6
  cell(P, [x0 + w - 1 - bw / 2, by + h - 5.2, z + 0.5], [bw, 3.2, 0.35], TONE.INK, `${id}-user`)
  if (user) P.texts.push({ id: `${id}-user`, at: [x0 + w - 1 - bw + 0.6, by + h - 4.6, z + 0.7], text: user.text, font: 'ui', size: 0.5, tone: 'onInk', maxWidth: bw - 1.2 })
  P.anchors.user = [x0 + w - 1 - bw / 2, by + h - 5.2, z + 0.7]
  const tools = messages.filter(m => m.role !== 'user')
  const nRows = Math.max(o.tools ?? tools.length, 0)
  for (let k = 0; k < nRows; k++) {
    const g = `${id}-tool-${k}`, y = by + h - 9.2 - k * 2.1
    cell(P, [x0 + 1.4, y, z + 0.5], [0.5, 1.5, 0.3], TONE.ACCENT, g)
    cell(P, [x0 + 2 + (w - 4) / 2 + 0.4, y, z + 0.45], [w - 4, 1.5, 0.25], TONE.SHEET, g)
    if (tools[k]) P.texts.push({ id: g, at: [x0 + 2.4, y - 0.25, z + 0.6], text: tools[k].text, font: 'mono', size: 0.48, tone: tools[k].role === 'agent' ? 'ink' : 'ink', maxWidth: w - 4.4 })
    P.groups[g] = { hover: true }
    P.anchors[`tool-${k}`] = [x0 + w - 1.5, y, z + 0.6]
  }
  cell(P, [cx, by + 1.6, z + 0.45], [w - 2, 1.8, 0.3], TONE.SHEET, `${id}-input`)
  cell(P, [x0 + 2.2, by + 1.6, z + 0.7], [0.2, 1.1, 0.2], TONE.INK, `${id}-input`)
  P.groups[id] = {}
  P.anchors.input = [cx, by + 1.6, z + 0.6]
  P.anchors.top = [cx, by + h, z]
  P.anchors.right = [x0 + w, by + h * 0.6, z + 0.4]
  return P
}

/** A flat pane of 2×2 tiles with a MID border (exploded layers, empty boards). `at` = bottom-centre; faces +z. Group `${id}`. */
export function pane(o: { id?: string; at?: Vec3; w?: number; h?: number; tone?: number; border?: number; depth?: number; wave?: boolean }): Part {
  const { id = 'pane', at = [0, 0, 0], w = 30, h = 24, tone = TONE.SCREEN, border = TONE.MID, depth = 0.4, wave = false } = o
  const [cx, by, z] = at
  const P = part()
  const x0 = cx - w / 2
  for (let i = 0; i < w; i += 2) for (let j = 0; j < h; j += 2) {
    const edge = i === 0 || j === 0 || i >= w - 2 || j >= h - 2
    cell(P, [x0 + i + 1, by + j + 1, z], [1.94, 1.94, depth], edge ? border : tone, id, wave ? { wave: true } : undefined)
  }
  P.groups[id] = {}
  P.anchors.top = [cx, by + h, z]; P.anchors.topLeft = [x0, by + h, z + depth / 2]; P.anchors.topRight = [x0 + w, by + h, z + depth / 2]
  P.anchors.front = [cx, by + h / 2, z + depth / 2]
  return P
}

/** XRAY dashed outline of a tile (an empty slot). `at` = bottom-left [x0, y0, z]. Group `${id}`. */
export function placeholder(o: { id?: string; at: Vec3; w: number; h: number; depth?: number }): Part {
  const { id = 'slot', at, w, h, depth = 0.5 } = o
  const [x0, y0, z] = at
  const P = part()
  const W = Math.round(w), H = Math.round(h)
  for (let i = 0; i < W; i++) for (let j = 0; j < H; j++) {
    if (i === 0 || j === 0 || i === W - 1 || j === H - 1) cell(P, [x0 + i + 0.5, y0 + j + 0.5, z + depth / 2], [0.9, 0.9, depth], TONE.XRAY, id)
  }
  P.groups[id] = {}
  P.anchors[id] = [x0 + w / 2, y0 + h / 2, z + depth]
  return P
}
