/**
 * 01 COLUMNS: pure layouts (storyboard §4.2). No three, no DOM, no randomness.
 *
 * One Arrow table, three shapes made of the same cells (one cell = one value):
 *   table  - ONE spreadsheet on the floor: a continuous INK header row with the field names printed on it, then
 *            24 striped rows across 8 columns that touch (no gaps: it must read as one table before it splits)
 *   towers - each column stacks its 24 values into a pillar (row r = layer r), its header on top as the INK lid.
 *            A pillar's DEPTH is its bytes per value (FIELD_BYTES): same rows, different widths, so the
 *            "28.6% of bytes" claim is visible (revenue, float64, is the deepest).
 *   read   - one pillar is read (ACCENT, every value); the others collapse to hidden-line boxes, one per record
 *            batch, and a small browser window in front shows what the chart computed from that one column.
 *
 * Cells of one column are emitted in the same canonical sequence in every layout (header/lid, one value per
 * record batch, then the rest), so the engine's group-then-rank matching pairs table cell r with pillar layer r,
 * and each hidden-line box with the first value of its own batch.
 */
import { TONE, type Cell, type GroupInfo, type Layout, type Vec3 } from '../../stage/types'
import type { Kit, Part } from '../../stage/kit'
import { DAYS, FIELDS, FIELD_BYTES, ORD_TOTAL_K, REGIONS, REV_TOTAL_K, fieldByteShare, fmtMoneyK, fmtPct, fmtThousands } from '../../stage/data'

export interface ColumnsState { read: number }

/* ───────────── geometry (world units) ───────────── */

export const N_COLS = 8, N_ROWS = 24
/** Table: columns touch (pitch = width); the 2 px object contour between column groups draws the column rules. */
export const COL_W = 4.4, ROW_D = 0.9, ROW_H = 0.4
export const TABLE_W = N_COLS * COL_W                                    // 35.2
export const TABLE_X0 = -TABLE_W / 2, TABLE_X1 = TABLE_W / 2
/** The header row stands 1.5 tall so the names print upright on its front face (legible on desktop and mobile). */
export const HEAD_H = 1.5, HEAD_D = 1
export const Z_HEAD = 0                                                  // header centre z
export const Z_FRONT = Z_HEAD + HEAD_D / 2                               // header front face (the names print here)
export const rowZ = (r: number) => Z_FRONT + 0.1 + ROW_D / 2 + r * ROW_D
export const TABLE_Z1 = rowZ(N_ROWS - 1) + ROW_D / 2                     // front edge of the table
export const colLeft = (c: number) => TABLE_X0 + c * COL_W
export const colMid = (c: number) => colLeft(c) + COL_W / 2

/**
 * Pillars: 4 wide, one .5 layer per row, depth ∝ bytes per value. They stand just BEHIND the header row, fronts
 * aligned, so the split never passes a cell through the header: rows file in over it and the header lifts on last.
 */
export const TOWER_W = 4, TOWER_PITCH = 6.4, LAYER_H = 0.5
export const BODY_H = N_ROWS * LAYER_H                                   // 12
export const LID_H = 0.8, LID_Y = BODY_H + 0.1 + LID_H / 2
export const TOWER_TOP = LID_Y + LID_H / 2                               // 12.9
export const DEPTH_PER_BYTE = 0.5
export const towerD = (c: number) => FIELD_BYTES[c] * DEPTH_PER_BYTE     // 2, .5, 1, .5, 2, 2, 4, 2
export const towerX = (c: number) => (c - (N_COLS - 1) / 2) * TOWER_PITCH
export const Z_TFRONT = Z_HEAD - HEAD_D / 2 - 0.15                      // pillars' front face
export const towerZ = (c: number) => Z_TFRONT - towerD(c) / 2

/** Arrow record batches in the picture: 6 rows each (4 per column), the unit a hidden-line box stands for. */
export const BATCH = 6, BATCHES = N_ROWS / BATCH

/** The chart's window: a small browser tab standing in front of the pillars. */
export const WIN_W = 15, WIN_H = 7.4, WIN_BAR = 1.3, WIN_D = 0.6
export const WIN: Vec3 = [9.5, 0, 24]                                     // bottom-centre
export const WIN_FRONT = WIN[2] + WIN_D / 2
export const WIN_X0 = WIN[0] - WIN_W / 2

/* ───────────── field facts ───────────── */

/** Arrow physical type per field (byte widths match FIELD_BYTES). */
export const FIELD_TYPES = ['int32', 'dictionary<uint8>', 'uint16', 'uint8', 'int32', 'int32', 'float64', 'float32']
export const READ_DEFAULT = FIELDS.indexOf('revenue')                    // 6
/** Local figures the shared data module does not carry (requested for stage/data.ts; see the report). */
export const STORES = 32
export const HOURS = 24
export const ITEMS_PER_ORDER = 2.4
export const MARGIN_AVG = 0.309

/** The aggregate a one-column chart runs on each field: SQL, the KPI's value and its caption. */
export function aggregate(c: number): { sql: string; value: string; caption: string } {
  const f = FIELDS[c]
  switch (f) {
    case 'store_id': return { sql: `count(DISTINCT ${f})`, value: String(STORES), caption: 'stores · distinct' }
    case 'region': return { sql: `count(DISTINCT ${f})`, value: String(REGIONS.length), caption: 'regions · distinct' }
    case 'day': return { sql: `count(DISTINCT ${f})`, value: String(DAYS), caption: 'days · distinct' }
    case 'hour': return { sql: `count(DISTINCT ${f})`, value: String(HOURS), caption: 'hours · distinct' }
    case 'orders': return { sql: `sum(${f})`, value: fmtThousands(ORD_TOTAL_K), caption: 'orders · sum' }
    case 'items': return { sql: `sum(${f})`, value: fmtThousands(ORD_TOTAL_K * ITEMS_PER_ORDER), caption: 'items · sum' }
    case 'margin': return { sql: `avg(${f})`, value: fmtPct(MARGIN_AVG), caption: 'margin · avg' }
    default: return { sql: `sum(${f})`, value: fmtMoneyK(REV_TOTAL_K), caption: 'revenue · sum · USD' }
  }
}
/** The value as the window shows it (money gets its sign). */
export const shownValue = (c: number) => (FIELDS[c] === 'revenue' ? '$' : '') + aggregate(c).value

export const byteShare = (c: number) => fmtPct(fieldByteShare(c))
export const skippedShare = (c: number) => fmtPct(1 - fieldByteShare(c))
/** The copy's code line for the column being read (the revenue line is the round-1 line, unchanged). */
export function codeLine(c: number): string {
  const q = `SELECT ${aggregate(c).sql} FROM orders;`
  return q.length <= 34 ? `${q}  -- reads 1 of ${N_COLS} columns` : `${q} -- 1 of ${N_COLS} columns`
}
export const readOf = (s: ColumnsState) => (s.read >= 0 ? s.read : READ_DEFAULT)

/* ───────────── canonical cell sequence per column ───────────── */

/** Row per slot: the header (-1), then the first row of every record batch, then every other row. */
export const SEQ: number[] = (() => {
  const out = [-1]
  for (let b = 0; b < BATCHES; b++) out.push(b * BATCH)
  for (let r = 0; r < N_ROWS; r++) if (r % BATCH) out.push(r)
  return out
})()

const HOVER: GroupInfo = { hover: true, click: true, liftOnHover: [0, 0.6, 0] }
export const groupOf = (c: number) => `col-${c}`
/** Rows alternate PAPER / SHEET right across the table (spreadsheet striping); a row keeps its tone as a layer. */
export const rowTone = (r: number) => (r % 2 ? TONE.SHEET : TONE.PAPER)

/* ───────────── table ───────────── */

/**
 * The table lying on the floor. `s.read` (a chip pressed on this beat) paints that column's values ACCENT.
 * `layDown` marks the lay-down from the hero: the table prints itself, header first, then row after row toward
 * the viewer, each row left → right like a print head (crisp cuts; see the report for why not 'flip').
 */
export function tableLayout(s: ColumnsState, layDown = false): Layout {
  const cells: Cell[] = []
  for (let c = 0; c < N_COLS; c++) {
    const g = groupOf(c)
    for (const r of SEQ) {
      const cell: Cell = r < 0
        ? { p: [colMid(c), HEAD_H / 2, Z_HEAD], s: [COL_W - 0.02, HEAD_H, HEAD_D], tone: TONE.INK, group: g }
        : { p: [colMid(c), ROW_H / 2, rowZ(r)], s: [COL_W - 0.1, ROW_H, ROW_D - 0.12], tone: c === s.read ? TONE.ACCENT : rowTone(r), group: g }
      if (layDown) {
        cell.kind = 'cut'
        // the scene's `in` (spread .7) lands a cut at m = .15 + .7·order: the header at ~.29, the last row at ~.85
        cell.order = 0.2 + 0.75 * ((r + 1) / N_ROWS) + 0.04 * (c / N_COLS)
      }
      cells.push(cell)
    }
  }
  return { cells }
}

/* ───────────── towers ───────────── */

/**
 * The eight pillars. With `s.read >= 0` that pillar stays solid (ACCENT values) and the rest become hidden-line
 * boxes (GHOST, one per record batch, INK lid kept). `withWindow` adds the chart's window.
 * Per-cell `order` drives the split, and it is the story: the table is taken apart ROW BY ROW, back row first.
 * Each row lifts, splits into its eight values and each value is filed onto its own column's pillar (a ripple
 * left → right inside the row); when the last row is filed, the header lifts on top as the lids.
 */
export function towersLayout(kit: Kit, s: ColumnsState, withWindow: boolean): Layout {
  const cells: Cell[] = []
  const groups: Record<string, GroupInfo> = {}
  const reading = s.read >= 0
  for (let c = 0; c < N_COLS; c++) {
    const g = groupOf(c)
    groups[g] = HOVER
    const ghost = reading && c !== s.read
    const x = towerX(c), d = towerD(c), z = towerZ(c)
    const ripple = 0.035 * (c / (N_COLS - 1))                         // left → right inside a row
    for (const r of SEQ) {
      if (r < 0) {
        cells.push({ p: [x, LID_Y, z], s: [TOWER_W, LID_H, d], tone: TONE.INK, group: g, order: reading ? 1 : 0.84 + ripple })
        continue
      }
      if (ghost) {
        // one hidden-line box per record batch (paired with that batch's first value)
        if (r % BATCH === 0) cells.push({ p: [x, (r + BATCH / 2) * LAYER_H, z], s: [TOWER_W - 0.04, BATCH * LAYER_H - 0.1, d - 0.04], tone: TONE.GHOST, group: g, order: r / N_ROWS })
        continue
      }
      cells.push({
        p: [x, (r + 0.5) * LAYER_H, z], s: [TOWER_W - 0.06, LAYER_H - 0.06, d - 0.06],
        tone: c === s.read ? TONE.ACCENT : rowTone(r), group: g,
        order: reading ? r / N_ROWS : 0.8 * (r / (N_ROWS - 1)) + ripple,
      })
    }
  }
  return kit.merge({ cells, groups, anchors: {}, texts: [] }, withWindow && reading && chartWindow(kit))
}

/* ───────────── the chart's window ───────────── */

/**
 * A small browser tab (INK title bar with three dots, white screen) standing in front of the pillars. Its caption
 * and value are surface text (crisp at every size; voxel numerals were too coarse at this scale). The window
 * rises out of the floor once the column has turned.
 */
export function chartWindow(kit: Kit): Part {
  const [cx, , wz] = WIN
  const g = 'readout'
  const screenH = WIN_H - WIN_BAR
  const P = kit.merge(
    kit.box({ p: [cx, screenH / 2, wz], s: [WIN_W, screenH, WIN_D], tone: TONE.SCREEN, group: g }),
    kit.box({ p: [cx, screenH + WIN_BAR / 2, wz], s: [WIN_W, WIN_BAR, WIN_D], tone: TONE.INK, group: g }),
    ...[0, 1, 2].map(k => kit.box({ p: [WIN_X0 + 0.9 + k * 0.85, screenH + WIN_BAR / 2, WIN_FRONT + 0.06], s: [0.45, 0.45, 0.12], tone: TONE.SCREEN, group: g })),
  )
  P.groups[g] = {}
  for (const c of P.cells) { c.kind = 'rise'; c.order = 0.45 + 0.3 * (c.p[1] / WIN_H) }
  return P
}

/** Floor route from the read pillar's front face along the bases, then forward into the back of the chart's window (orthogonal; chamfered by the Extras). */
export function tracePoints(c: number): Vec3[] {
  const y = 0.06, x = towerX(c), z0 = Z_TFRONT + 0.4, zb = WIN[2] - WIN_D / 2 - 0.1
  const xb = Math.max(WIN_X0 + 1, Math.min(WIN_X0 + WIN_W - 1, x))
  const zBus = z0 + 2.6                                                   // run along the pillar bases, clear of the labels
  return Math.abs(x - xb) < 0.01 ? [[x, y, z0], [xb, y, zb]] : [[x, y, z0], [x, y, zBus], [xb, y, zBus], [xb, y, zb]]
}

/** Text for hovering a pillar: field, Arrow type and width, byte share. */
export function hoverText(c: number, s: ColumnsState) {
  const reading = c === s.read
  return {
    text: `${FIELDS[c]} · ${FIELD_TYPES[c]}`,
    sub: reading ? `being read · ${byteShare(c)} of bytes` : `${FIELD_BYTES[c]} B per value · ${byteShare(c)} of bytes`,
  }
}
