/**
 * 01 COLUMNS — "Columns, not rows, at every boundary." (storyboard §4.2).
 *
 * Story: data travels as Arrow columns, so a chart reads only the column it needs.
 *   in     "lay the data down": the hero tab is erased top-down, line by line, while its table prints on the
 *          floor in front of it, header first, then row after row toward the viewer
 *   table  "Every board is a table underneath": 8 named columns × 24 rows, banded in 4-row record batches
 *   split  "Stored column by column": each column lifts batch by batch into a pillar (4 × 2 × 12), its header
 *          landing last as the INK lid; the eight pillars rise left → right in a wave
 *   read   "A chart reads one column": revenue stays solid (accent) and feeds a chart window (5.05M) along an
 *          accent trace; the other seven collapse to hidden-line slabs. Click a pillar (or a chip) to re-read.
 * Pure data + pure functions (no three here): 3D extras live in Extras.tsx, loaded lazily.
 */
import { lazy } from 'react'
import type { Beat, CalloutSpec, CameraKey, SceneModule, SurfaceText } from '../../stage/types'
import { FIELDS, FIELD_SHORT, ROWS_TOTAL, fmtInt } from '../../stage/data'
import {
  BOARD_SCREEN, BOARD_W, N_COLS, READ_DEFAULT, TABLE_X0, TABLE_Z1, TOWER_TOP, Z_HEAD, Z_TOWER,
  aggregate, byteShare, codeLine, colMid, hoverText, readOf, tableLayout, towerX, towersLayout, type ColumnsState,
} from './layouts'

export type { ColumnsState } from './layouts'

/* ───────────── cameras ───────────── */

const TABLE_CZ = (Z_HEAD + TABLE_Z1) / 2
const camTable: CameraKey = { az: -0.62, el: 0.58, target: [0, 0, TABLE_CZ], zoom: 0.86, mobile: { zoom: 1.15, el: 0.62 } }
const camSplit: CameraKey = { az: -0.42, el: 0.36, target: [0, 6.4, Z_TOWER], zoom: 0.88, mobile: { zoom: 1.12, el: 0.4 } }
const camRead: CameraKey = { az: -0.42, el: 0.46, target: [-3, 6, 12], zoom: 0.86, mobile: { zoom: 1.08, el: 0.5 } }

/* ───────────── beats ───────────── */

const beats: Beat<ColumnsState>[] = [
  { id: 'table', caption: 'Every board is a table underneath', len: 0.5, camera: camTable,
    layout: (_k, s) => tableLayout(s, true) },
  { id: 'split', caption: 'Stored column by column', len: 0.5, camera: camSplit,
    morph: { kind: 'lift', len: 0.9, assign: 'group', spread: 0.75, arc: 2.2 },
    layout: (k, s) => towersLayout(k, s, true) },
  { id: 'read', caption: 'A chart reads one column', len: 0.6, camera: camRead, state: { read: READ_DEFAULT },
    morph: { kind: 'cut', len: 0.6, spread: 0.6, order: 'y-asc', toneAt: 0.5 },
    layout: (k, s) => towersLayout(k, s, true), ambient: { kind: 'scan' } },
]

/* ───────────── labels ───────────── */

const callouts: CalloutSpec[] = [
  // table: the header names, and the row count at the front corner
  ...FIELDS.map((f, c): CalloutSpec => ({
    id: `head-${c}`, anchor: [colMid(c), 0.62, Z_HEAD], text: f, short: FIELD_SHORT[c], side: 'n', lead: c % 2 ? 40 : 22, priority: 2, beats: ['table'],
  })),
  { id: 'rows', anchor: [TABLE_X0, 0.4, TABLE_Z1], text: `${fmtInt(ROWS_TOTAL)} rows`, sub: `${N_COLS} columns · one Arrow table`, short: '1M ROWS', side: 'se', lead: 22, priority: 3, beats: ['table'] },
  // towers: the legend (hidden on the tower being read: its group is renamed 'read')
  ...FIELDS.map((f, c): CalloutSpec => ({
    id: `f-${c}`, anchor: { group: `col-${c}`, at: 'top' }, text: f, short: FIELD_SHORT[c], side: 'n', lead: c % 2 ? 34 : 16, priority: 1, beats: ['split', 'read'],
  })),
  { id: 'f-read', anchor: { group: 'read', at: 'top' }, text: s => FIELDS[readOf(s)], tone: 'accent', side: 'n', lead: 30, priority: 3, beats: ['split', 'read'] },
  // the chart's window: what it read, and what that cost
  { id: 'chart', anchor: { group: 'readout-win', at: 'bottom' }, text: s => `Chart reads ${FIELDS[readOf(s)]} only`, sub: s => `${byteShare(readOf(s))} of bytes · 1 of ${N_COLS} columns`,
    tone: 'accent', side: 's', lead: 22, priority: 4, beats: ['split', 'read'] },
]

const surface: SurfaceText[] = [
  { id: 'board-caption', at: [BOARD_SCREEN.x0 + 0.1, BOARD_SCREEN.y1 - 0.75, BOARD_SCREEN.z], text: s => (s.read >= 0 ? aggregate(readOf(s)).caption : ''),
    font: 'mono', size: 0.56, tone: 'muted', maxWidth: BOARD_W - 2.4, beats: ['split', 'read'] },
]

/* ───────────── module ───────────── */

const columns: SceneModule<ColumnsState> = {
  id: 'columns', index: '01', kicker: 'TRANSPORT',
  copy: {
    title: 'Columns, not rows, at every boundary.',
    body: 'Results travel as <b>Arrow</b> record batches from the warehouse to the edge to your tab. Nothing is re-encoded as JSON, so a chart reads only the column it needs.',
    facts: [['1 of 8', 'columns read for this chart'], ['0', 'JSON hops'], ['~1M', 'points stay interactive']],
    code: s => codeLine(readOf(s)),
    try: 'Click a column, or pick one to read:',
    spec: { href: '/docs/architecture-and-economics' },
  },
  summary: 'A table splits into eight columns; only the revenue column is read.',
  initialState: { read: -1 },
  // lay the data down: the hero tab is erased top-down, line by line, while its table prints on the floor in front
  // of it, header first, then row after row toward the viewer. With spread .7 a cut lands at m = .15 + .7·order:
  // big plates (a window's back, hidden behind its screen tiles) at .15, the tab's top at ~.35, its foot at ~.6;
  // the table's own cells carry orders .2 → 1 (header at ~.29, last row at ~.85).
  in: { kind: 'cut', len: 1.2, spread: 0.7, assign: 'group', order: c => (c.group.startsWith('col-') || c.group === 'read' ? 33 : (c.s?.[0] ?? 1) > 8 ? -60 : -c.p[1]) },
  beats,
  callouts,
  surface,
  controls: [
    { id: 'field', kind: 'chips', label: 'Column to read', options: FIELDS.map((f, i) => ({ value: String(i), label: f })),
      get: s => String(s.read), set: (_s, v) => ({ read: +v }),
      announce: s => `Reading ${FIELDS[readOf(s)]}: ${byteShare(readOf(s))} of bytes, 1 of ${N_COLS} columns` },
  ],
  pick: {
    hover(group, s) {
      const c = group === 'read' ? s.read : /^col-(\d)$/.test(group) ? +group.slice(4) : -1
      if (c < 0) return null
      return { anchor: [towerX(c), TOWER_TOP, Z_TOWER], ...hoverText(c, s) }
    },
    click(group) {
      const m = /^col-(\d)$/.exec(group)
      return m ? { read: +m[1] } : null
    },
  },
  // a click re-reads: the chosen tower fills with accent bottom-up while the old one collapses to hidden lines
  onState: () => ({ kind: 'cut', len: 450, spread: 0.6, order: 'y-asc', toneAt: 0.5 }),
  Extras: lazy(() => import('./Extras')),
  poster: 'read',
}
export default columns
