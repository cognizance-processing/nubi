/**
 * HERO — "Your board, running in this tab" (storyboard §4.1).
 * Beat `board`: a browser tab holding the Acme store-performance board (4 KPI cards, revenue bars, orders line),
 * built from the ruled paper on page load. Beat `inside`: the tab explodes into its layers — your board, the
 * DuckDB-WASM engine and the Arrow table — fanned back-right like an exploded technical drawing.
 * Pure data + pure functions (no three, no extras: every pixel of this scene is the shared cube stage).
 */
import { kit } from '../../stage/kit'
import type { Beat, SceneModule } from '../../stage/types'
import { DAYS, REV_K, ORD_K, fmtInt, fmtThousands, kpis } from '../../stage/data'
import {
  ENGINE_CHIP_BOTTOM, HERO_ANCHORS, HERO_INITIAL, PANES, PANE_H, heroBoard, heroBoardCamera, heroInside, heroInsideCamera, heroUi, type HeroState,
} from './layouts'

export type { HeroState } from './layouts'
export { heroBoardLayout, heroBoardCamera, heroInsideCamera, heroBoard, heroInside } from './layouts'

const clampDay = (d: number) => Math.min(DAYS - 1, Math.max(0, Math.round(d)))

const beats: Beat<HeroState>[] = [
  { id: 'board', caption: 'Your board, drawn in this tab', len: 0.6, camera: heroBoardCamera, layout: (_k, s) => heroBoard(s) },
  {
    id: 'inside', caption: 'Inside: the engine and the data', len: 0.6, camera: heroInsideCamera,
    morph: { kind: 'slide', len: 1.0, assign: 'group', spread: 0.7, order: 'x-asc' },
    layout: (_k, s) => heroInside(s),
  },
]

/* surface text: the tab's own text on both beats; the engine label and the table's region chips once exploded */
const uiTexts = heroUi().texts
const insideOnly = heroInside().texts.filter(t => !uiTexts.some(u => u.id === t.id))

const hero: SceneModule<HeroState> = {
  id: 'hero', index: '00', kicker: 'TAB',
  copy: {
    title: 'BI that runs in your browser. <em>Viewers are free.</em>',
    body: 'Open-source BI for teams that put dashboards in front of a lot of people. The query engine runs in each viewer’s tab, so an extra viewer costs about nothing.',
    facts: [['DuckDB', 'WASM kernel in the tab'], ['Arrow', 'at every boundary'], ['17', 'chart types'], ['$0', 'per extra viewer']],
    try: 'Hover a bar to read its value, or step through the days:',
    spec: { href: '/docs/architecture-and-economics', text: 'How it works' },
    hint: { board: 'Scroll to look inside the tab', inside: 'Engine and data, both in this tab. Keep scrolling to follow one query.' },
  },
  summary: 'A browser tab showing a store-performance dashboard, computed in the tab.',
  initialState: HERO_INITIAL,
  build: { morph: { kind: 'rise', len: 2600, spread: 0.8, order: 'y-desc' } },
  beats,
  callouts: [
    { id: 'computed', anchor: HERO_ANCHORS.status, text: 'Computed in this tab', sub: 'DuckDB-WASM · 38 ms', short: 'IN THIS TAB', tone: 'accent', side: 's', lead: 30, priority: 2, beats: ['board'] },
    { id: 'ui', anchor: [-19, 34.2, 0.5], text: 'Your board', sub: 'drawn in this tab', short: 'UI', side: 'n', lead: 26, priority: 1, beats: ['inside'] },
    { id: 'engine', anchor: ENGINE_CHIP_BOTTOM, text: 'DuckDB-WASM', sub: 'runs the SQL in this tab', short: 'ENGINE', tone: 'accent', side: 's', lead: 40, priority: 3, beats: ['inside'] },
    { id: 'data', anchor: [PANES.data.inside[0] + 6, PANE_H, PANES.data.inside[2] + 0.2], text: 'Arrow table', sub: `${fmtInt(1_048_576)} rows`, short: 'DATA', side: 'n', lead: 22, priority: 2, beats: ['inside'] },
  ],
  surface: [...kit.surface(uiTexts, ['board', 'inside']), ...kit.surface(insideOnly, ['inside'])],
  controls: [
    {
      id: 'day', kind: 'range', label: 'Day', min: 1, max: DAYS, step: 1,
      get: s => clampDay(s.day) + 1,
      set: (_s, v) => ({ day: clampDay(+v - 1), live: true }),
      announce: s => `Day ${clampDay(s.day) + 1}: revenue $${REV_K[clampDay(s.day)]}k, ${fmtThousands(ORD_K[clampDay(s.day)])} orders`,
    },
  ],
  pick: {
    hover(group) {
      let m = /^bar-(\d+)$/.exec(group)
      if (m) { const d = +m[1]; return { anchor: HERO_ANCHORS.bar(d), text: `Day ${d + 1} · $${REV_K[d]}K`, sub: 'revenue · Acme, all regions' } }
      m = /^line-(\d+)$/.exec(group)
      if (m) { const d = +m[1]; return { anchor: HERO_ANCHORS.line(d), text: `Orders · day ${d + 1}`, sub: `${fmtInt(ORD_K[d] * 1000)} orders` } }
      const i = kpis().findIndex(k => group === `kpi-${k.id}`)
      if (i >= 0) { const [head, value] = kpis()[i].full.split(': '); return { anchor: HERO_ANCHORS.kpi(i), text: head, sub: value } }
      return null
    },
    click(group) {
      const m = /^(?:bar|line)-(\d+)$/.exec(group)
      return m ? { day: +m[1], live: true } : null
    },
  },
  onState: () => ({ kind: 'cut', len: 360, spread: 0, toneAt: 0.35 }),
  poster: 'board',
}
export default hero
