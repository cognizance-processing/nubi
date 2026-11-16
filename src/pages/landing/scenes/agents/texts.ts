/**
 * 05 AGENTS: words printed on the objects (pure). Each text is declared once with the beat it ENTERS on and the
 * window of m in which it types there; it is then emitted as one SurfaceText per beat (see `typed()` in timeline.ts):
 *   - on its entry beat it types in its window, in step with the object it sits on;
 *   - on every other beat it is held (fully shown from m ≈ 0.04), so labels on objects that do not move never blink
 *     out during a morph.
 * Texts on objects that exist only in some states return '' for the others (interaction variants stay consistent).
 */
import type { SurfaceText, Vec3 } from '../../stage/types'
import { AGENT } from '../../stage/data'
import { BEATS, BUILD, CALLS, HOLD, IN, LIVE, QUERY_TOOLS, TOOL_LEG, arriveT, toolT, typed, type AgentsState, type BeatId } from './timeline'
import { BUBBLE, CHAT, MARGINS, REPLY, REV, SLOTS, TAG, TOWER, WIN, codeOf, rowY, socketPos, tableRowY } from './layout'

type Font = SurfaceText['font']; type Tone = SurfaceText['tone']
interface Def {
  id: string; at: Vec3; text: string | ((s: AgentsState) => string); font: Font; size: number; tone: Tone
  align?: SurfaceText['align']; enter: BeatId; win: [number, number]
}

const CX0 = CHAT.cx - CHAT.w / 2, CX1 = CHAT.cx + CHAT.w / 2, CZ = CHAT.z
const TZF = TOWER.z + TOWER.d / 2
const WX0 = WIN.cx - WIN.w / 2
const SZ = WIN.z + 0.4                                    // the board's screen front
/** The request, broken where a chat client would wrap it inside the bubble. */
export const PROMPT_LINES = ['Build a board for the EMEA', 'review: revenue by country', 'for Q3, margin next to it.']
export const PROMPT = PROMPT_LINES.join(' ')

const defs: Def[] = []
const add = (d: Def) => defs.push(d)

/* chat */
add({ id: 'chat-title', at: [CX0 + 0.9, CHAT.h - 1.28, CZ + 0.31], text: 'New chat', font: 'ui', size: 0.6, tone: 'ink', enter: 'ask', win: [0.36, 0.42] })
PROMPT_LINES.forEach((line, i) => add({ id: `ask-${i}`, at: [BUBBLE.x0 + 0.6, 22.05 - i * 1.05, CZ + 0.7], text: line, font: 'ui', size: 0.52, tone: 'onInk', enter: 'ask', win: IN.prompt[i] }))
CALLS.forEach((c, k) => {
  const enter: BeatId = k < QUERY_TOOLS ? 'tools' : k === 4 ? 'build' : 'live'
  const w: [number, number] = k < QUERY_TOOLS ? [toolT(k) + TOOL_LEG.type[0], toolT(k) + TOOL_LEG.type[1]] : k === 4 ? [BUILD.type[0], BUILD.type[1]] : [LIVE.type[0], LIVE.type[1]]
  add({ id: `row-${k}`, at: [CX0 + 1.9, rowY(k) - 0.25, CZ + 0.6], text: s => (s.step > k ? c.id : ''), font: 'mono', size: 0.5, tone: 'ink', enter, win: w })
  add({ id: `row-${k}-res`, at: [CX1 - 1.0, rowY(k) - 0.22, CZ + 0.6], text: s => (s.step > k ? c.result : ''), font: 'mono', size: 0.47, tone: 'muted', align: 'right', enter, win: [w[1] - 0.03, Math.min(0.865, w[1] + 0.01)] })
})
add({ id: 'reply', at: [REPLY.x0 + 0.5, REPLY.y - 0.26, CZ + 0.62], text: s => (s.step >= 6 ? `Live at /d/${AGENT.board}` : ''), font: 'ui', size: 0.5, tone: 'ink', enter: 'live', win: [LIVE.replyType[0], LIVE.replyType[1]] })
add({ id: 'input', at: [CX0 + 2.2, 1.98, CZ + 0.62], text: 'Reply…', font: 'mono', size: 0.47, tone: 'muted', enter: 'ask', win: [0.4, 0.44] })

/* MCP server */
add({ id: 'mcp', at: [TOWER.cx, 11.55, TZF + 0.02], text: 'MCP', font: 'mono-strong', size: 1.3, tone: 'onInk', align: 'centre', enter: 'ask', win: [0.6, 0.64] })
add({ id: 'mcp-sub', at: [TOWER.cx, socketPos(14)[1] - 1.55, TZF + 0.02], text: '15 TOOLS', font: 'mono-strong', size: 0.47, tone: 'onInk', align: 'centre', enter: 'ask', win: [0.62, 0.66] })

/* board window chrome (title, URL) and the publish tag */
const TB_Y = WIN.h - 1.5
add({ id: 'win-title', at: [WX0 + 5.6, TB_Y - 0.55, WIN.z + 0.71], text: 'EMEA review', font: 'ui', size: 0.62, tone: 'ink', enter: 'ask', win: [0.4, 0.46] })
add({ id: 'win-url', at: [WX0 + 16.2, TB_Y - 0.25, WIN.z + 0.71], text: `/d/${AGENT.board}`, font: 'mono', size: 0.5, tone: 'muted', enter: 'ask', win: [0.44, 0.5] })
add({ id: 'tag-draft', at: [TAG.x1 - 2.5, TAG.y0 + TAG.h / 2 - 0.25, TAG.z + 0.32], text: s => (s.step < 6 ? 'DRAFT' : ''), font: 'mono-strong', size: 0.5, tone: 'muted', align: 'centre', enter: 'ask', win: [0.84, 0.865] })
add({ id: 'tag-live', at: [TAG.x0 + 0.65, TAG.y0 + TAG.h / 2 - 0.28, TAG.z + 0.32], text: s => (s.step >= 6 ? `LIVE · /d/${AGENT.board}` : ''), font: 'mono-strong', size: 0.52, tone: 'onInk', enter: 'live', win: [LIVE.tagType[0], LIVE.tagType[1]] })

/* tiles (present once the board is built) */
const built = (t: string) => (s: AgentsState) => (s.step >= 5 ? t : '')
const capWin = (k: number): [number, number] => [arriveT(k) + 0.05, arriveT(k) + 0.1]
SLOTS.forEach((sl, k) => {
  const yt = sl.y0 + sl.h
  const cap = ['EMEA revenue · Q3', 'Margin · Q3', 'Revenue by country · $M', 'Margin by country'][k]
  add({ id: `cap-${sl.id}`, at: [sl.x0 + 0.75, yt - 1.15, SZ + 0.42], text: built(cap), font: k < 2 ? 'mono' : 'ui', size: 0.5, tone: k < 2 ? 'muted' : 'ink', enter: 'build', win: capWin(k) })
})
{
  const sl = SLOTS[2], step = 1.1 + 0.55
  REV.forEach((_, i) => add({ id: `bar-code-${i}`, at: [sl.x0 + 0.9 + i * step + 0.55, sl.y0 + 0.72, SZ + 0.42], text: built(codeOf(AGENT.byCountry[i][0])), font: 'mono', size: 0.47, tone: 'muted', align: 'centre', enter: 'build', win: capWin(2) }))
}
{
  const sl = SLOTS[3], yt = sl.y0 + sl.h, w = capWin(3)
  add({ id: 'tbl-h0', at: [sl.x0 + 0.95, yt - 2.3 - 0.25, SZ + 0.7], text: built('COUNTRY'), font: 'mono-strong', size: 0.5, tone: 'onInk', enter: 'build', win: w })
  add({ id: 'tbl-h1', at: [sl.x0 + sl.w - 0.95, yt - 2.3 - 0.25, SZ + 0.7], text: built('MARGIN'), font: 'mono-strong', size: 0.5, tone: 'onInk', align: 'right', enter: 'build', win: w })
  MARGINS.forEach((mg, r) => {
    const y = tableRowY(r) - 0.22, rw: [number, number] = [Math.min(0.84, w[0] + 0.004 * r), Math.min(0.865, w[1] + 0.004 * r)]
    add({ id: `tbl-c${r}`, at: [sl.x0 + 0.95, y, SZ + 0.42], text: built(codeOf(AGENT.byCountry[r][0])), font: 'mono', size: 0.47, tone: 'ink', enter: 'build', win: rw })
    add({ id: `tbl-v${r}`, at: [sl.x0 + sl.w - 0.8, y, SZ + 0.42], text: built(`${mg.toFixed(1)}%`), font: 'mono', size: 0.47, tone: 'muted', align: 'right', enter: 'build', win: rw })
  })
}

const resolve = (t: Def['text'], s: AgentsState) => (typeof t === 'function' ? t(s) : t)

/** One SurfaceText per (text, beat): typed in its window on its entry beat, held on the others. */
export const SURFACE: SurfaceText[] = defs.flatMap(d => BEATS.map(b => {
  const [a, z] = b === d.enter ? d.win : HOLD
  const { enter: _e, win: _w, text, ...rest } = d
  return { ...rest, id: `${d.id}@${b}`, text: (s: AgentsState) => typed(resolve(text, s), a, z), reveal: 'type' as const, beats: [b] }
}))
export const TEXT_DEFS = defs
