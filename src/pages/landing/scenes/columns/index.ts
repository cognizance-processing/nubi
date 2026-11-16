/**
 * 01 COLUMNS — "Columns, not rows, at every boundary." (storyboard §4.2).
 *
 * Story: data travels as Arrow columns, so a chart reads only the column it needs.
 *   in     "lay the data down": the hero tab is erased top-down while its table prints on the floor in front of
 *          it, the ink header first, then row after row toward the viewer
 *   table  "Every board is a table underneath": ONE spreadsheet, 8 named columns × 24 striped rows
 *   split  "Stored column by column": each column's header lifts to become a lid and its rows stack under it,
 *          column after column; a pillar's depth is its bytes per value
 *   read   "A chart reads one column": revenue turns accent bottom-up and feeds a browser tab ($5.05M) along an
 *          accent trace; the other seven collapse to dashed hidden lines. Click a pillar (or a chip) to re-read.
 * Pure data + pure functions (no three here): 3D extras live in Extras.tsx, loaded lazily.
 */
import { lazy } from 'react'
import type { Beat, CalloutSpec, CameraKey, SceneModule, SurfaceText } from '../../stage/types'
import { FIELDS, ROWS_TOTAL, fmtInt } from '../../stage/data'
import {
  BODY_H, COL_W, HEAD_H, N_COLS, READ_DEFAULT, ROW_H, TABLE_X0, TABLE_Z1, TOWER_TOP, WIN_BAR, WIN_FRONT, WIN_H, WIN_W, WIN_X0, Z_FRONT, Z_TFRONT,
  aggregate, byteShare, codeLine, colMid, hoverText, readOf, shownValue, skippedShare, tableLayout, towerX, towerZ, towersLayout,
  type ColumnsState,
} from './layouts'

export type { ColumnsState } from './layouts'

/* ───────────── cameras ───────────── */

const camTable: CameraKey = { az: -0.5, el: 0.55, target: [0, 0, 10.5], zoom: 1.0, mobile: { az: -0.12, el: 0.46, target: [0, -5.5, 9.5], zoom: 1.5 } }
const camSplit: CameraKey = { az: -0.42, el: 0.36, target: [0, 6.2, 2], zoom: 0.92, mobile: { az: -0.3, el: 0.4, target: [0, 6.2, 2], zoom: 1.12 } }
const camRead: CameraKey = { az: -0.42, el: 0.5, target: [-1.9, 8.6, 8], zoom: 0.88, mobile: { az: -0.3, el: 0.44, target: [-1.5, 5, 8], zoom: 1.1 } }

/* ───────────── beats ───────────── */

const beats: Beat<ColumnsState>[] = [
  { id: 'table', caption: 'Every board is a table underneath', len: 0.5, camera: camTable,
    layout: (_k, s) => tableLayout(s, true) },
  { id: 'split', caption: 'Stored column by column', len: 0.5, camera: camSplit,
    morph: { kind: 'lift', len: 0.9, assign: 'group', spread: 0.85, arc: 1.2 },
    layout: (k, s) => towersLayout(k, s, true) },
  { id: 'read', caption: 'A chart reads one column', len: 0.6, camera: camRead, state: { read: READ_DEFAULT },
    morph: { kind: 'cut', len: 0.6, spread: 0.6, order: 'y-asc', toneAt: 0.5 },
    layout: (k, s) => towersLayout(k, s, true), ambient: { kind: 'scan' } },
]

/* ───────────── labels ───────────── */

const field = (s: ColumnsState) => FIELDS[readOf(s)]

const callouts: CalloutSpec[] = [
  // table: one callout; the field names are printed on the ink header
  { id: 'rows', anchor: [TABLE_X0 + 2 * COL_W, ROW_H, TABLE_Z1], text: `${fmtInt(ROWS_TOTAL)} rows`, sub: `${N_COLS} columns · one Arrow table`, short: '1M ROWS', side: 's', lead: 26, priority: 3, beats: ['table'] },
  // split: why the pillars differ (the names are printed on the pillars)
  { id: 'bytes-hi', anchor: { group: 'col-6', at: 'top' }, text: '8 bytes per value', sub: 'float64 · the deepest column', short: '8 B/VALUE', side: 'nw', lead: 26, priority: 2, beats: ['split'] },
  { id: 'bytes-lo', anchor: { group: 'col-1', at: 'top' }, text: '1 byte per value', sub: 'dictionary-encoded', short: '1 B/VALUE', side: 'n', lead: 26, priority: 1, beats: ['split'] },
  // read: what the chart read, and what it skipped
  { id: 'chart', anchor: [WIN_X0, WIN_H * 0.45, WIN_FRONT], text: s => `Reads ${field(s)} only`, sub: s => `${byteShare(readOf(s))} of bytes · 1 column`,
    short: 'READS 1 OF 8', tone: 'accent', side: 'w', lead: 26, priority: 4, beats: ['read'] },
  { id: 'skip', anchor: [towerX(0), 0, Z_TFRONT], text: `${N_COLS - 1} columns skipped`, sub: s => `${skippedShare(readOf(s))} of bytes never read`,
    short: '7 SKIPPED', side: 's', lead: 22, priority: 2, beats: ['read'] },
]

/** Field names: upright on the table's ink header, then up the front of each pillar (no floating legend to collide). */
const HEAD_CAP = 0.75, SPINE_CAP = 1.0
const surface: SurfaceText[] = [
  ...FIELDS.map((f, c): SurfaceText => ({
    id: `head-${c}`, at: [colMid(c), ROW_H + (HEAD_H - ROW_H - HEAD_CAP) / 2 + 0.02, Z_FRONT + 0.02], text: f,
    font: 'title', size: HEAD_CAP, tone: 'onInk', align: 'centre', maxWidth: COL_W - 0.3, beats: ['table'],
  })),
  ...FIELDS.flatMap((f, c): SurfaceText[] => {
    const base = { at: [towerX(c) + SPINE_CAP / 2, BODY_H - 0.55, Z_TFRONT + 0.02] as [number, number, number], plane: { right: [0, 1, 0] as [number, number, number], up: [-1, 0, 0] as [number, number, number] }, font: 'title' as const, size: SPINE_CAP, align: 'right' as const, maxWidth: BODY_H - 1.2, beats: ['split', 'read'] }
    return [
      { ...base, id: `spine-${c}`, text: (s: ColumnsState) => (s.read < 0 ? f : ''), tone: 'ink' },
      { ...base, id: `spine-skip-${c}`, text: (s: ColumnsState) => (s.read >= 0 && s.read !== c ? f : ''), tone: 'muted' },
      { ...base, id: `spine-read-${c}`, text: (s: ColumnsState) => (s.read === c ? f : ''), tone: 'onInk' },
    ]
  }),
  { id: 'win-caption', at: [WIN_X0 + 0.9, WIN_H - WIN_BAR - 1.25, WIN_FRONT + 0.02], text: s => (s.read >= 0 ? aggregate(readOf(s)).caption : ''),
    font: 'mono', size: 0.8, tone: 'muted', maxWidth: WIN_W - 1.8, beats: ['split', 'read'] },
  { id: 'win-value', at: [WIN_X0 + 0.8, 1.4, WIN_FRONT + 0.02], text: s => (s.read >= 0 ? shownValue(readOf(s)) : ''),
    font: 'title', size: 2.3, tone: 'ink', maxWidth: WIN_W - 1.6, beats: ['split', 'read'] },
]

/* ───────────── module ───────────── */

const columnOf = (group: string) => (/^col-(\d)$/.test(group) ? +group.slice(4) : -1)

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
  in: { kind: 'cut', len: 1.2, spread: 0.7, assign: 'group', order: c => (c.group.startsWith('col-') ? 33 : (c.s?.[0] ?? 1) > 8 ? -60 : -c.p[1]) },
  beats,
  callouts,
  surface,
  controls: [
    { id: 'field', kind: 'chips', label: 'Column to read', options: FIELDS.map((f, i) => ({ value: String(i), label: f })),
      get: s => String(s.read), set: (_s, v) => ({ read: +v }),
      announce: s => `Reading ${field(s)}: ${byteShare(readOf(s))} of bytes, 1 of ${N_COLS} columns` },
  ],
  pick: {
    hover(group, s) {
      const c = columnOf(group)
      if (c < 0) return null
      return { anchor: [towerX(c), TOWER_TOP, towerZ(c)], ...hoverText(c, s) }
    },
    click(group, s) {
      const c = columnOf(group)
      return c >= 0 && c !== s.read ? { read: c } : null
    },
  },
  // a click re-reads: the chosen pillar fills with accent bottom-up while the old one collapses to hidden lines
  onState: () => ({ kind: 'cut', len: 450, spread: 0.6, order: 'y-asc', toneAt: 0.5 }),
  Extras: lazy(() => import('./Extras')),
  poster: 'read',
}
export default columns
