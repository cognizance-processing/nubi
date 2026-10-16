/**
 * 01 COLUMNS: pure layouts (storyboard §4.2). No three, no DOM, no randomness.
 *
 * One Arrow table, three shapes made of the same cells:
 *   table  - 8 columns × 24 rows lying on the floor, an INK header row at the back (cells 4×1, .4 tall, .3 gap)
 *   towers - each column's 24 rows stacked into a 4 × 2 × 12 pillar (rows 2j, 2j+1 = layer j); its header is the lid
 *   read   - one pillar stays solid (ACCENT, every value cube); the others become hidden-line slabs, one per record
 *            batch (the batches that are skipped), and a small chart window in front shows what the chart computed.
 *
 * Cells of one column are emitted in the same canonical sequence in every layout (header/lid, one cube per batch,
 * the rest), so the engine's group-then-rank matching pairs table cell (r, i) with pillar cell (r, i) exactly, and
 * each hidden-line slab with a cube of its own batch.
 */
import { TONE, type Cell, type GroupInfo, type Layout, type Vec3 } from '../../stage/types'
import type { Kit, Part } from '../../stage/kit'
import { DAYS, FIELDS, FIELD_BYTES, ORD_TOTAL_K, REGIONS, REV_TOTAL_K, fieldByteShare, fmtMoneyK, fmtPct, fmtThousands } from '../../stage/data'

export interface ColumnsState { read: number }

/* ───────────── geometry (world units) ───────────── */

export const N_COLS = 8, N_ROWS = 24, CELL_W = 4, COL_GAP = 0.3
export const COL_PITCH = CELL_W + COL_GAP
export const TABLE_W = N_COLS * CELL_W + (N_COLS - 1) * COL_GAP          // 34.1
export const TABLE_X0 = -TABLE_W / 2
/** Header row centre z; data rows follow toward the viewer (+z). The table lies in front of where the hero tab stood. */
export const Z_HEAD = 2.5
export const rowZ = (r: number) => Z_HEAD + 1 + r
export const TABLE_Z1 = rowZ(N_ROWS - 1) + 0.5                          // front edge, 27
export const colLeft = (c: number) => TABLE_X0 + c * COL_PITCH
export const colMid = (c: number) => colLeft(c) + CELL_W / 2

/** A column stands up as a pillar: 4 cells wide, TOWER_D rows deep, one layer per TOWER_D rows. */
export const TOWER_D = 2
export const LAYERS = N_ROWS / TOWER_D                                   // 12
/** Arrow record batches in the picture: 4 rows each (6 per column), banded PAPER / SHEET in every shape. */
export const BATCH = 4, BATCHES = N_ROWS / BATCH
export const TOWER_PITCH = 6.4
/** Pillars stand on the footprint of the first TOWER_D rows, so the first layer only slides apart on x. */
export const Z_TOWER = rowZ((TOWER_D - 1) / 2)                           // 4
export const towerX = (c: number) => (c - (N_COLS - 1) / 2) * TOWER_PITCH
export const LID_Y = LAYERS + 0.4, LID_H = 0.8
export const TOWER_TOP = LID_Y + LID_H / 2                               // 12.8

/** The chart's window, in the foreground right of the row: what the chart computed from the one column it read. */
export const BOARD: Vec3 = [6, 0, 32]
export const BOARD_W = 16, BOARD_H = 9

/* ───────────── field facts ───────────── */

/** Arrow physical type per field (byte widths match FIELD_BYTES). */
export const FIELD_TYPES = ['int32', 'dictionary<uint8>', 'uint16', 'uint8', 'int32', 'int32', 'float64', 'float32']
export const READ_DEFAULT = FIELDS.indexOf('revenue')                    // 6
/** Local figures the shared data module does not carry (requested for stage/data.ts; see the report). */
export const STORES = 32
export const HOURS = 24
export const ITEMS_PER_ORDER = 2.4
export const MARGIN_AVG = 0.309

/** The aggregate a one-column chart runs on each field: SQL, the KPI tile's value and caption. */
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

export const byteShare = (c: number) => fmtPct(fieldByteShare(c))
/** The copy's code line for the column being read (the revenue line is the round-1 line, unchanged). */
export function codeLine(c: number): string {
  const q = `SELECT ${aggregate(c).sql} FROM orders;`
  return q.length <= 34 ? `${q}  -- reads 1 of ${N_COLS} columns` : `${q} -- 1 of ${N_COLS} columns`
}
export const readOf = (s: ColumnsState) => (s.read >= 0 ? s.read : READ_DEFAULT)

/* ───────────── canonical cell sequence per column ───────────── */

/** (row, i) per slot: the header (row -1), then one cube per layer (row 4j, i 0), then every other cell. */
export const SEQ: Array<[number, number]> = (() => {
  const out: Array<[number, number]> = []
  for (let i = 0; i < CELL_W; i++) out.push([-1, i])
  for (let b = 0; b < BATCHES; b++) out.push([BATCH * b, 0])
  for (let r = 0; r < N_ROWS; r++) for (let i = 0; i < CELL_W; i++) if (!(i === 0 && r % BATCH === 0)) out.push([r, i])
  return out
})()

const HOVER: GroupInfo = { hover: true, click: true, liftOnHover: [0, 0.6, 0] }
export const groupOf = (c: number, s: ColumnsState) => (c === s.read ? 'read' : `col-${c}`)
/** Rows 4j..4j+3 form one record batch (one tower layer); batches alternate PAPER / SHEET so the fold is legible. */
export const batchTone = (r: number) => (Math.floor(r / BATCH) % 2 ? TONE.SHEET : TONE.PAPER)

/* ───────────── table ───────────── */

/**
 * The table lying on the floor. `s.read` (an interaction on this beat) paints that column's values ACCENT.
 * `layDown` marks the lay-down from the hero: the table prints itself, header first, then row after row toward
 * the viewer, each row left → right like a print head. Cells cut in crisply (a parked 'flip' would show as a
 * standing card before its turn, and 'scale' reads as a field of dots while the cells are tiny).
 */
export function tableLayout(s: ColumnsState, layDown = false): Layout {
  const cells: Cell[] = []
  for (let c = 0; c < N_COLS; c++) {
    const g = groupOf(c, s)
    for (const [r, i] of SEQ) {
      const x = colLeft(c) + i + 0.5
      const cell: Cell = r < 0
        ? { p: [x, 0.3, Z_HEAD], s: [0.96, 0.6, 0.92], tone: TONE.INK, group: g }
        : { p: [x, 0.2, rowZ(r)], s: [0.96, 0.4, 0.92], tone: c === s.read ? TONE.ACCENT : batchTone(r), group: g }
      if (layDown) {
        cell.kind = 'cut'
        // the scene's `in` (spread .7) lands a cut at m = .15 + .7·order: the header at ~.29, the last row at ~.85
        cell.order = 0.2 + 0.75 * ((r + 1) / N_ROWS) + 0.05 * ((c * CELL_W + i) / (N_COLS * CELL_W))
      }
      cells.push(cell)
    }
  }
  return { cells }
}

/* ───────────── towers ───────────── */

function towerCell(c: number, r: number, i: number): { p: Vec3; j: number } {
  const j = Math.floor(r / TOWER_D), k = r % TOWER_D
  return { p: [towerX(c) - 1.5 + i, j + 0.5, Z_TOWER - (TOWER_D - 1) / 2 + k], j }
}

/**
 * The eight towers. With `s.read >= 0` that tower stays solid (ACCENT cubes) and the rest become hidden-line
 * slabs (GHOST, one per layer, INK lid kept). `withBoard` adds the KPI board the chart fills.
 * Per-cell `order` drives the split: a wave across the columns, each column stacking bottom → top, lid last.
 */
export function towersLayout(kit: Kit, s: ColumnsState, withBoard: boolean): Layout {
  const cells: Cell[] = []
  const groups: Record<string, GroupInfo> = {}
  const reading = s.read >= 0
  for (let c = 0; c < N_COLS; c++) {
    const g = groupOf(c, s)
    groups[g] = HOVER
    const ghost = reading && c !== s.read
    const wave = (c / (N_COLS - 1)) * 0.5
    for (const [r, i] of SEQ) {
      if (r < 0) {
        cells.push({ p: [towerX(c) - 1.5 + i, LID_Y, Z_TOWER], s: [0.96, LID_H, TOWER_D - 0.08], tone: TONE.INK, group: g, order: reading ? 1 : wave + 0.5 })
        continue
      }
      const { p, j } = towerCell(c, r, i)
      const order = wave + 0.5 * (j / LAYERS)
      if (ghost) {
        // one hidden-line slab per record batch (paired with that batch's representative cube)
        const per = BATCH / TOWER_D
        if (i === 0 && r % BATCH === 0) cells.push({ p: [towerX(c), j + per / 2, Z_TOWER], s: [3.92, per - 0.08, TOWER_D - 0.08], tone: TONE.GHOST, group: g, order: j / LAYERS })
        continue
      }
      cells.push({ p, s: [0.9, 0.9, 0.9], tone: c === s.read ? TONE.ACCENT : batchTone(r), group: g, order: reading ? j / LAYERS : order })
    }
  }
  return kit.merge({ cells, groups, anchors: {}, texts: [] }, withBoard && reading && board(kit, s))
}

/* ───────────── the chart's window ───────────── */

/** A small browser window holding one KPI: the value the chart computed from the column it read. */
export function board(kit: Kit, s: ColumnsState): Part {
  const a = aggregate(readOf(s))
  const [bx, by, bz] = BOARD
  const win = kit.browserWindow({ id: 'readout-win', at: [bx, by, bz], w: BOARD_W, h: BOARD_H, titleBar: 1, small: true })
  const { x0, y1, zFront } = win.screen
  const { x1 } = win.screen
  const px = Math.min(0.75, (x1 - x0 - 2) / kit.measureVoxelText(a.value, 1))
  const num = kit.voxelText({ text: a.value, at: [x0 + 1, y1 - 2.1, zFront + 0.1], px, tone: TONE.INK, group: 'readout-value' })
  const P = kit.merge(win, num)
  // the window rises out of the floor once the tower has turned; numerals last
  for (const c of P.cells) { c.kind = 'rise'; c.order = 0.4 + 0.35 * Math.min(1, c.p[1] / (BOARD_H + 1)) + (c.group === 'readout-value' ? 0.2 : 0) }
  return P
}
export const BOARD_SCREEN = { x0: BOARD[0] - BOARD_W / 2 + 1, y1: BOARD[1] + BOARD_H - 1, z: BOARD[2] + 0.65 }

/** Floor route from the read tower's front face to the back of the chart's window (orthogonal; chamfered by the Extras). */
export function tracePoints(c: number): Vec3[] {
  const y = 0.06, x = towerX(c), z0 = Z_TOWER + TOWER_D / 2 + 0.3, zBus = (Z_TOWER + 2 + BOARD[2]) / 2 + 1, xb = BOARD[0] + 2, zb = BOARD[2] - 0.7
  return Math.abs(x - xb) < 0.01 ? [[x, y, z0], [xb, y, zb]] : [[x, y, z0], [x, y, zBus], [xb, y, zBus], [xb, y, zb]]
}

/** Text for hovering a tower: field, Arrow type and width, byte share. */
export function hoverText(c: number, s: ColumnsState) {
  const reading = c === s.read
  return {
    text: `${FIELDS[c]} · ${FIELD_TYPES[c]}`,
    sub: reading ? `being read · ${byteShare(c)} of bytes` : `${FIELD_BYTES[c]} B per value · ${byteShare(c)} of bytes`,
  }
}
