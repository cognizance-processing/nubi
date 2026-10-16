/**
 * 05 AGENTS: the shared clock of the scene (pure; imported by the layouts, the surface text and Extras.tsx).
 *
 * Every time below is a value of the morph progress m (0..1) of one scroll segment. The layouts turn them into
 * per-cell `order` (start = order · spread), the surface text into typing windows, and the Extras into packet
 * positions, so cubes, words and packets stay in step whether the visitor scrubs, snaps or presses Replay.
 */
import { AGENT } from '../../stage/data'

export type BeatId = 'ask' | 'tools' | 'build' | 'live'
export const BEATS: BeatId[] = ['ask', 'tools', 'build', 'live']

/* ───────────── the six MCP calls of the session ───────────── */

export interface Call { id: string; short: string; result: string; text: string; sub: string }

const ROWS = AGENT.byCountry.length
export const TILE_COUNT = 4

/** The calls, in the order the agent makes them. Callout lines stay within 28 characters. */
export const CALLS: Call[] = [
  { id: 'get_context', short: 'CONTEXT', result: 'context', text: 'get_context', sub: `org ${AGENT.org} · EMEA rows` },
  { id: 'list_metrics', short: 'METRICS', result: 'metrics', text: 'list_metrics', sub: 'revenue · margin · governed' },
  { id: 'query_metric', short: 'QUERY', result: `${ROWS} rows`, text: `query_metric → ${ROWS} rows`, sub: 'EMEA · Q3 · cache hit · 3 ms' },
  { id: 'validate_spec', short: 'VALIDATE', result: 'valid', text: 'validate_spec → valid', sub: `${TILE_COUNT} tiles · ${TILE_COUNT} governed queries` },
  { id: 'upsert_dashboard', short: 'UPSERT', result: 'draft', text: 'upsert_dashboard', sub: `/d/${AGENT.board} · draft` },
  { id: 'promote', short: 'PROMOTE', result: 'live', text: 'promote → live', sub: `/d/${AGENT.board} · for viewers` },
]
export const QUERY_TOOLS = 4                       // get_context … validate_spec happen in the `tools` beat

/** Scene state. step: calls completed (0 = only the request; 4 = tools done; 5 = board built; 6 = live). */
export interface AgentsState { step: number; call: number; replay: number }
export const PRESET: Record<BeatId, Omit<AgentsState, 'replay'>> = {
  ask: { step: 0, call: -1 },
  tools: { step: QUERY_TOOLS, call: 2 },
  build: { step: 5, call: 4 },
  live: { step: 6, call: 5 },
}
export const isPreset = (s: AgentsState, b: BeatId) => s.step === PRESET[b].step && s.call === PRESET[b].call && !s.replay

/* ───────────── morph specs (spread → per-cell duration 1 − spread) ───────────── */

export const SPREAD = { in: 0.7, tools: 0.85, build: 0.85, live: 0.7 } as const
/** cell order for a desired start time in a segment with this spread */
export const orderAt = (start: number, spread: number) => Math.min(1, Math.max(0, start / spread))

/* in (the rewind from the board scene), m of the 1.2-beat segment */
export const IN = {
  sinkEnd: 0.12,                 // previous scene's cells start sinking right → left by this m
  frame: [0.1, 0.28], screen: [0.14, 0.32], chat: [0.12, 0.34], tower: [0.2, 0.34], sockets: [0.36, 0.42],
  slots: [0.32, 0.42], bubble: 0.4, tag: 0.7,
  prompt: [[0.6, 0.69], [0.69, 0.78], [0.78, 0.865]] as [number, number][],
}
/** Replay starts here (m of `in`): every object is in place, the prompt is about to type. */
export const REPLAY_FROM = 0.58

/* tools: four calls, each a row flipping in, a packet out to the MCP server and a result back */
export const toolT = (k: number) => 0.04 + k * 0.235
export const TOOL_LEG = { out: [0.05, 0.13], back: [0.15, 0.23], socket: 0.1, type: [0.03, 0.12] } as const

/* build: one upsert call, then one packet per tile; each arrival builds that tile */
export const BUILD = { row: 0.02, type: [0.04, 0.11], out: [0.03, 0.1], socket: 0.08 } as const
export const arriveT = (k: number) => 0.16 + k * 0.2
export const TILE_FLIGHT = 0.1                     // a tile's packet flies MCP → slot in the 0.1 before its arrival

/* live: the promote call, the packet to the tag, the tag flips, the agent replies */
export const LIVE = { row: 0, type: [0.04, 0.2], out: [0.0, 0.07], up: [0.07, 0.48], socket: 0.1, tag: [0.48, 0.66], tagType: [0.79, 0.865], reply: 0.56, replyType: [0.68, 0.865] } as const

/** Fraction of a leg [a, b] at m, or −1 outside it. */
export const leg = (m: number, a: number, b: number) => (m < a || m > b ? -1 : (m - a) / (b - a))

/* ───────────── typed surface text ───────────── */

/**
 * The label layer types a `reveal: 'type'` text with floor(m · 1.15 · length) characters during the scroll morph
 * into its first beat. Zero-width spaces around the visible text (default-ignorable: no glyph, no advance in SVG
 * or canvas) move that reveal into the window [a, b] of m, so each line types exactly when its object arrives.
 * A window of [0, HOLD] keeps a text on screen through a morph its object does not take part in.
 */
export const ZW = '​'
export const HOLD: [number, number] = [0, 0.04]
const typedMemo = new Map<string, string>()          // the label layer resolves text every frame: no re-allocation
export function typed(text: string, a: number, b: number): string {
  const len = text.length
  if (!len) return ''
  const key = `${a}|${b}|${text}`
  const hit = typedMemo.get(key)
  if (hit !== undefined) return hit
  const hi = Math.min(b, 0.865)
  const total = Math.ceil(len / (1.15 * Math.max(0.005, hi - a)))
  const pre = Math.round(a * 1.15 * total)
  const post = Math.max(0, total - pre - len)
  const out = ZW.repeat(pre) + text + ZW.repeat(post)
  typedMemo.set(key, out)
  return out
}
/** The visible characters of a typed string at m (mirrors the label layer; used by tests). */
export function visibleAt(t: string, m: number): string {
  return t.slice(0, Math.floor(m * 1.15 * t.length)).split(ZW).join('')
}

/* ───────────── replay (time-based playback of ask → live) ───────────── */

/** ms spent per segment of the agents timeline during Replay (sum ≈ 6.5 s, plus a 0.8 s rewind). */
export const REPLAY_MS = { rewind: 800, in: 700, ask: 450, tools: 2000, toolsRest: 300, build: 2200, buildRest: 300, live: 900 }
/** Reduced motion: Replay steps through the composed beats instead (ms per still). */
export const REPLAY_STEP_MS = 1400
