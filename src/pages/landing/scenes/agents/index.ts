/**
 * 05 AGENTS — "Agents lay out the board. SQL supplies the numbers." (storyboard §4.6).
 *
 * Story: you ask for a board in chat; the agent calls Nubi's MCP tools; the board assembles tile by tile from
 * governed queries and goes live. Pure data + pure functions (three.js lives only in the lazy Extras.tsx).
 *
 *   in     rewind   the previous board un-builds right → left; the chat opens like blinds, the MCP server rises, the window
 *                   rises with four dashed (x-ray) slots; then the request types into the ink bubble
 *   ask    rest
 *   tools  1.0      get_context · list_metrics · query_metric · validate_spec: each row grows into the chat, a
 *                   labelled packet runs chat → MCP → back, and that tool's socket flashes
 *   build  1.2      upsert_dashboard: one packet per tile runs MCP → slot; each landing grows that tile
 *                   (KPI, KPI, bars, table) and its placeholder vanishes
 *   live   0.5      promote: a packet climbs to the DRAFT tag, which turns over to LIVE · /d/emea-review
 */
import { lazy } from 'react'
import type { Beat, CameraKey, Cell, Control, SceneModule, Vec3 } from '../../stage/types'
import { AGENT } from '../../stage/data'
import { CALLS, IN, PRESET, SPREAD, TILE_COUNT, type AgentsState, type BeatId } from './timeline'
import { CHAT, KPI_MGN, KPI_REV, MARGINS, REV, SLOTS, agentsLayout, codeOf, rowY } from './layout'
import { SURFACE } from './texts'

export type { AgentsState } from './timeline'

/* ───────────── camera ───────────── */

// Front-left and a touch high so the server's side and the window's depth read; the whole pipeline fits the
// stage safe rect at 1440 × 900 with ~14 px per unit (surface text ≥ 9 px). Mobile frames the same pipeline in the band.
const CAM: Record<BeatId, CameraKey> = {
  // a slow orbit to the right as the session progresses (parallax while scrolling; every key frames the whole pipeline)
  ask: { az: -0.3, el: 0.21, target: [2, 12.6, 0], zoom: 0.86, mobile: { az: -0.3, el: 0.21, target: [2, 12.6, 0], zoom: 1.12 } },
  tools: { az: -0.28, el: 0.2, target: [2, 12.6, 0], zoom: 0.86, mobile: { az: -0.28, el: 0.2, target: [2, 12.6, 0], zoom: 1.12 } },
  build: { az: -0.25, el: 0.19, target: [2, 12.8, 0], zoom: 0.86, mobile: { az: -0.25, el: 0.19, target: [2, 12.8, 0], zoom: 1.12 } },
  live: { az: -0.23, el: 0.18, target: [2, 13.2, 0], zoom: 0.85, mobile: { az: -0.23, el: 0.18, target: [2, 13.2, 0], zoom: 1.1 } },
}

/* ───────────── morph orders ───────────── */

/** In-scene morphs: each cell's explicit order (set by the layout for its beat), else 0. */
const own = (c: Cell) => c.order ?? 0
/** The rewind: the previous board dissolves in place, right → left (reverse of how it was read); ours arrive on their own orders. */
const inOrder = (c: Cell) => (c.group.startsWith('ag-') ? own(c) : (IN.sinkEnd / SPREAD.in) * Math.min(1, Math.max(0, (30 - c.p[0]) / 60)))

const beats: Beat<AgentsState>[] = [
  { id: 'ask', caption: 'You ask in chat', len: 0.4, camera: CAM.ask, state: PRESET.ask,
    layout: (_k, s) => agentsLayout(s, 'ask') },
  { id: 'tools', caption: 'The agent calls Nubi’s tools', len: 0.4, camera: CAM.tools, state: PRESET.tools,
    morph: { kind: 'scale', len: 1.0, spread: SPREAD.tools, assign: 'group', order: own },
    layout: (_k, s) => agentsLayout(s, 'tools'), ambient: { kind: 'packets' } },
  { id: 'build', caption: 'The board builds, tile by tile', len: 0.4, camera: CAM.build, state: PRESET.build,
    morph: { kind: 'scale', len: 1.2, spread: SPREAD.build, assign: 'group', order: own },
    layout: (_k, s) => agentsLayout(s, 'build'), ambient: { kind: 'packets' } },
  { id: 'live', caption: 'Promoted to live', len: 0.6, camera: CAM.live, state: PRESET.live,
    morph: { kind: 'scale', len: 0.5, spread: SPREAD.live, assign: 'group', order: own },
    layout: (_k, s) => agentsLayout(s, 'live') },
]

/* ───────────── hover (tool rows, tiles, the tag) ───────────── */

const ref = agentsLayout({ step: 6, call: -1, replay: 0 })
const topOf = (k: number): Vec3 => { const s = SLOTS[k]; return [s.x0 + s.w / 2, s.y0 + s.h, -2] }
function hover(g: string): { anchor: Vec3; text: string; sub?: string } | null {
  let m = /^ag-row-(\d)$/.exec(g)
  if (m) { const c = CALLS[+m[1]]; return { anchor: [CHAT.cx + CHAT.w / 2 - 0.6, rowY(+m[1]), CHAT.z + 0.6], text: c.text, sub: c.sub } }
  if (g.startsWith('ag-kpi-rev')) return { anchor: topOf(0), text: `EMEA revenue · ${KPI_REV}`, sub: 'governed query · sum of revenue' }
  if (g.startsWith('ag-kpi-mgn')) return { anchor: topOf(1), text: `Margin · ${KPI_MGN}`, sub: 'governed query · margin ÷ revenue' }
  if ((m = /^ag-bar-(\d)$/.exec(g))) { const [name, v] = AGENT.byCountry[+m[1]]; return { anchor: ref.anchors[g] ?? topOf(2), text: `${name} · $${v.toFixed(2)}M`, sub: 'revenue · EMEA · Q3' } }
  if (g === 'ag-bars') return { anchor: topOf(2), text: 'Revenue by country', sub: `${REV.length} rows · one governed query` }
  if ((m = /^ag-tbl-r(\d)$/.exec(g))) { const r = +m[1]; return { anchor: topOf(3), text: `${codeOf(AGENT.byCountry[r][0])} · margin ${MARGINS[r].toFixed(1)}%`, sub: 'same query, your row policy' } }
  if (g.startsWith('ag-tbl')) return { anchor: topOf(3), text: 'Margin by country', sub: `${REV.length} rows · one governed query` }
  if (g === 'ag-live') return { anchor: ref.anchors.liveTop ?? topOf(1), text: `Live · /d/${AGENT.board}`, sub: 'viewers keep their row policies' }
  if (g === 'ag-ask') return { anchor: [CHAT.cx + 3, 23.2, CHAT.z + 0.7], text: 'Your request', sub: 'plain language, no SQL' }
  if (g === 'ag-mcp') return { anchor: ref.anchors.mcpTop, text: 'MCP server', sub: '15 tools · OAuth' }
  return null
}

/* ───────────── controls (DOM twins) ───────────── */

const controls: Control<AgentsState>[] = [
  { id: 'replay', kind: 'button', label: 'Replay', get: s => s.replay, set: s => ({ replay: s.replay + 1 }),
    announce: () => 'Replaying the session: request, tool calls, build, live. Press Escape to stop.' },
  { id: 'call', kind: 'chips', label: 'Tool call', options: CALLS.map((c, i) => ({ value: String(i), label: c.id })),
    get: s => String(s.call), set: (_s, v) => ({ call: +v, step: +v + 1 }),
    announce: s => { const c = CALLS[s.call]; return c ? `${c.text}. ${c.sub}.` : '' } },
]

const agentsScene: SceneModule<AgentsState> = {
  id: 'agents', index: '05', kicker: 'AGENTS · MCP',
  copy: {
    title: 'Agents lay out the board. SQL supplies the numbers.',
    body: 'Claude, ChatGPT and Claude Code connect over OAuth and get fifteen tools. An agent arranges the board. Every value comes from a governed query, under your row policies.',
    facts: [['15', 'MCP tools'], ['OAuth', 'no API keys to paste'], ['Same', 'row policies as you']],
    try: 'Replay the session, or pick a tool call:',
    spec: { href: '/docs/mcp' },
  },
  summary: 'A chat request makes an agent build a dashboard through MCP tools.',
  initialState: { ...PRESET.ask, replay: 0 },
  in: { kind: 'scale', len: 1.2, spread: SPREAD.in, assign: 'group', order: inOrder },
  beats,
  callouts: [
    { id: 'ask', anchor: { group: 'ag-ask', at: 'top' }, text: 'You ask', sub: 'plain language · no SQL', short: 'YOU ASK', side: 'ne', lead: 26, priority: 2, beats: ['ask'] },
    { id: 'mcp', anchor: { group: 'ag-foot', at: 'bottom' }, text: 'MCP server', sub: '15 tools · OAuth', short: 'MCP', side: 's', lead: 28, priority: 1, beats: ['ask'] },
    { id: 'call', anchor: { group: 'ag-call', at: 'bottom' }, text: s => CALLS[s.call]?.text ?? '', sub: s => CALLS[s.call]?.sub ?? '', short: 'MCP CALL',
      tone: 'accent', side: 's', lead: 28, priority: 3, beats: ['ask', 'tools', 'build', 'live'] },
    { id: 'sql', anchor: { group: 'ag-win', at: 'top' }, text: 'Every value is SQL', sub: `${TILE_COUNT} tiles · ${TILE_COUNT} governed queries`, short: '4 QUERIES', side: 'n', lead: 30, priority: 1, beats: ['build'] },
  ],
  surface: SURFACE,
  controls,
  pick: {
    hover: g => hover(g),
    click: (g, s) => { const m = /^ag-row-(\d)$/.exec(g); return m && +m[1] < s.step ? { call: +m[1] } : null },
  },
  onState: (prev, next) => {
    if (prev.step === next.step) return prev.call < 0 || next.call < 0 || prev.replay !== next.replay
      ? { kind: 'cut', len: 1, spread: 0 }
      : { kind: 'slide', len: 380, spread: 0, assign: 'group' }           // the focus ring slides to the new socket
    return { kind: 'scale', len: 760, spread: 0.55, order: 'x-asc', assign: 'group' }
  },
  Extras: lazy(() => import('./Extras')),
  poster: 'build',
}
export default agentsScene
