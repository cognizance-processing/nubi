/**
 * 05 AGENTS: which traces glow and where the tool-call packets are (pure; drawn by Extras.tsx).
 * A function of (beat, m, state, ambient time, reduced), so a frame is reproducible from the debug hook.
 *
 * Scroll morphs replay the calls on the shared clock (timeline.ts). At rest, packets either loop (ambient, ≤ 5 s
 * after the last input) or sit at a composed position with their label (reduced motion, frozen ambient, idle).
 */
import type { Vec3 } from '../../stage/types'
import { BUILD, CALLS, IN, LIVE, QUERY_TOOLS, TILE_FLIGHT, TOOL_LEG, arriveT, leg, toolT, type AgentsState } from './timeline'
import { ROUTE_A, ROUTE_B, ROUTE_P, routeTo } from './layout'

export interface PacketSpec { key: string; path: Vec3[]; t: number; label?: string; short?: string }
export interface TraceState { a: boolean; b: boolean; p: boolean; show: boolean; aOn: boolean; bOn: boolean; pOn: boolean }
export interface Frame { traces: TraceState; packets: PacketSpec[] }

const A_BACK = [...ROUTE_A].reverse()
const ROUTES = [0, 1, 2, 3].map(routeTo)
const callPk = (k: number, t: number, back = false): PacketSpec => ({
  key: `c${k}${back ? 'b' : ''}`, path: back ? A_BACK : ROUTE_A, t,
  label: back ? CALLS[k].result : CALLS[k].id, short: back ? undefined : CALLS[k].short,
})
const tilePk = (k: number, t: number, label = true): PacketSpec => ({ key: `t${k}`, path: ROUTES[k], t, label: label ? CALLS[4].id : undefined, short: label ? CALLS[4].short : undefined })
const promotePk = (t: number): PacketSpec => ({ key: 'p', path: ROUTE_P, t, label: CALLS[5].id, short: CALLS[5].short })

/** The composed still of each beat (reduced motion, idle, poster-equivalent). */
function still(beat: string, s: AgentsState): PacketSpec[] {
  if (beat === 'tools' && s.step >= 1 && s.step <= QUERY_TOOLS) return [callPk(Math.max(0, Math.min(s.call, s.step - 1)), 0.58)]
  if (beat === 'build' && s.step === 5) return [{ ...tilePk(3, 0.3, false), path: ROUTE_B }]   // unlabeled: a chip here would cover the KPI row
  return []
}

export function frameAt(beat: string, m: number, s: AgentsState, ambientT: number, reduced: boolean): Frame {
  const traces: TraceState = { show: true, a: false, b: false, p: false, aOn: false, bOn: false, pOn: false }
  const packets: PacketSpec[] = []
  if (beat === 'ask') {
    traces.show = m >= IN.prompt[0][0] - 0.04
    return { traces, packets }
  }
  const moving = m < 1 && !reduced
  if (beat === 'tools') {
    traces.a = true
    if (moving) {
      for (let k = 0; k < QUERY_TOOLS; k++) {
        const t0 = toolT(k)
        const o = leg(m, t0 + TOOL_LEG.out[0], t0 + TOOL_LEG.out[1]), b = leg(m, t0 + TOOL_LEG.back[0], t0 + TOOL_LEG.back[1])
        if (o >= 0) packets.push(callPk(k, o))
        if (b >= 0) packets.push(callPk(k, b, true))
      }
    } else if (ambientT > 0 && !reduced) {
      // loop the four calls: out 0.7 s, back 0.7 s, then the next one
      const T = ambientT % (QUERY_TOOLS * 1.6), k = Math.floor(T / 1.6), u = T - k * 1.6
      if (u < 0.7) packets.push(callPk(k, u / 0.7)); else if (u < 1.4) packets.push(callPk(k, (u - 0.7) / 0.7, true))
    } else packets.push(...still('tools', s))
  } else if (beat === 'build') {
    traces.b = true
    if (moving) {
      traces.a = m < BUILD.out[1] + 0.02
      const o = leg(m, BUILD.out[0], BUILD.out[1])
      if (o >= 0) packets.push(callPk(4, o))
      for (let k = 0; k < 4; k++) { const u = leg(m, arriveT(k) - TILE_FLIGHT, arriveT(k)); if (u >= 0) packets.push(tilePk(k, u)) }
    } else if (ambientT > 0 && !reduced && s.step === 5) {
      const T = ambientT % 4.8, k = Math.floor(T / 1.2), u = (T - k * 1.2) / 0.9
      if (u <= 1) packets.push(tilePk(k, u))
    } else packets.push(...still('build', s))
  } else if (beat === 'live') {
    if (moving) {
      traces.a = m < LIVE.out[1] + 0.02
      traces.p = m >= LIVE.out[1] - 0.01 && m <= LIVE.up[1] + 0.04
      const o = leg(m, LIVE.out[0], LIVE.out[1]), u = leg(m, LIVE.up[0], LIVE.up[1])
      if (o >= 0) packets.push(callPk(5, o))
      if (u >= 0) packets.push(promotePk(u))
    }
  }
  // a trace that has carried its packets stays drawn solid in ink (the route exists); idle ones are dashed
  traces.aOn = beat !== 'ask'
  traces.bOn = beat === 'build' || beat === 'live'
  traces.pOn = beat === 'live' && (m >= LIVE.up[1] || !moving)
  return { traces, packets }
}
