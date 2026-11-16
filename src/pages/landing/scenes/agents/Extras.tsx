/**
 * 05 AGENTS extras (lazy, inside the stage canvas): the air traces chat → MCP → board and MCP → tag, the labelled
 * tool-call packets (positions from packets.ts, a pure function of the props), and the Replay controller.
 *
 * Replay plays the scene's own timeline: it rewinds T to just before the request types, then advances it to
 * `agents.live` over ~6.5 s, so every cube, word and packet runs exactly as it does under scroll. The page does not
 * scroll meanwhile, so wheel / touch / keys / Escape cancel it and hand T back to where the scroll is. Under reduced
 * motion it steps through the four composed beats instead.
 */
import { useEffect, useRef } from 'react'
import type { ExtrasProps } from '../../stage/types'
import { Packets, Trace, chamfer, useStage } from '../../stage/extras'
import { labelT } from '../../stage/story'
import { REPLAY_FROM, REPLAY_MS, REPLAY_STEP_MS, type AgentsState } from './timeline'
import { ROUTE_A, ROUTE_B, ROUTE_P } from './layout'
import { frameAt } from './packets'

const A = chamfer(ROUTE_A, 0.8), B = chamfer(ROUTE_B, 0.8), P = chamfer(ROUTE_P, 0.8)

function useReplay(replay: number) {
  const { director } = useStage()
  const seen = useRef(replay)
  const running = useRef<(() => void) | null>(null)
  const quietUntil = useRef(0)                                   // our own setT can bring the press override back
  useEffect(() => () => running.current?.(), [])                 // unmount: stop (the scroll owns T again)
  useEffect(() => {
    // The press is an interaction override on the current beat; it is dropped as soon as T reaches another
    // beat's rest, so `replay` falls back to 0 mid-playback. Only an increase starts a run; nothing here stops it.
    const prev = seen.current
    seen.current = replay
    if (!(replay > prev) || performance.now() < quietUntil.current) return
    running.current?.()
    const story = director.story
    const si = story.scenes.findIndex(s => s.id === 'agents')
    if (si < 0) return
    const span = story.spans[si], inLen = story.scenes[si].in?.len ?? 1.2
    const L = (b: string) => labelT(story, 'agents', b)!
    const morph = (b: string) => (story.scenes[si].beats.find((x: { id: string }) => x.id === b)?.morph?.len ?? 0)
    const home = director.T                                      // where the scroll is; restored on cancel
    const reduced = director.reduced
    // keyframes (ms → T): rewind, then forward through the scene with per-segment pacing
    const k: [number, number][] = []
    let t = 0
    const at = (ms: number, T: number) => { t += ms; k.push([t, T]) }
    if (reduced) {
      for (const b of ['ask', 'tools', 'build', 'live']) { k.push([t, L(b)]); t += REPLAY_STEP_MS }
    } else {
      k.push([0, home])
      const start = span.t0 + inLen * REPLAY_FROM
      at(REPLAY_MS.rewind, start)
      at(REPLAY_MS.in, L('ask'))
      at(REPLAY_MS.ask, L('tools') - morph('tools'))
      at(REPLAY_MS.tools, L('tools'))
      at(REPLAY_MS.toolsRest, L('build') - morph('build'))
      at(REPLAY_MS.build, L('build'))
      at(REPLAY_MS.buildRest, L('live') - morph('live'))
      at(REPLAY_MS.live, L('live'))
    }
    const t0 = performance.now()
    let raf = 0, done = false
    const ease = (u: number) => (u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2)
    const tick = () => {
      const e = performance.now() - t0
      let T = k[k.length - 1][1]
      for (let i = 0; i < k.length - 1; i++) {
        if (e < k[i + 1][0]) {
          if (reduced) { T = k[i][1]; break }
          const u = (e - k[i][0]) / Math.max(1, k[i + 1][0] - k[i][0])
          T = k[i][1] + (k[i + 1][1] - k[i][1]) * (i === 0 ? ease(u) : u)   // the rewind eases; the playback is linear in T
          break
        }
      }
      director.setT(T)
      if (e < (reduced ? t : k[k.length - 1][0])) raf = requestAnimationFrame(tick)
      else stop(false)
    }
    const cancel = (ev: Event) => {
      if (ev instanceof KeyboardEvent && ['Tab', 'Shift', 'Control', 'Alt', 'Meta', 'Enter', ' '].includes(ev.key) && !(ev.key === ' ' && ev.target === document.body)) return
      stop(true)
    }
    const evs = ['wheel', 'touchmove', 'keydown'] as const
    function stop(restore: boolean) {
      if (done) return
      done = true
      cancelAnimationFrame(raf)
      evs.forEach(n => window.removeEventListener(n, cancel, true))
      quietUntil.current = performance.now() + 1200
      if (restore) director.setT(home)
    }
    // listen from the next task, so the click/keypress that started Replay does not cancel it
    const arm = setTimeout(() => { if (!done) evs.forEach(n => window.addEventListener(n, cancel, { capture: true, passive: true })) }, 0)
    raf = requestAnimationFrame(tick)
    running.current = () => { clearTimeout(arm); stop(false) }
  }, [replay, director])
}

export default function AgentsExtras({ beat, m, state, ambientT, reduced }: ExtrasProps<AgentsState>) {
  useReplay(state.replay)
  const { invalidate } = useStage()
  useEffect(() => { invalidate() })                              // packets move on props; the stage renders on demand
  const f = frameAt(beat, m, state, ambientT, reduced)
  const tr = f.traces
  return (
    <>
      {tr.show && <Trace points={A} active={tr.a || tr.aOn} tone={tr.a ? 'accent' : tr.aOn ? 'ink' : undefined} dashed={!tr.a && !tr.aOn} />}
      {tr.show && <Trace points={B} active={tr.b || tr.bOn} tone={tr.b ? 'accent' : tr.bOn ? 'ink' : undefined} dashed={!tr.b && !tr.bOn} />}
      {tr.show && <Trace points={P} active={tr.p || tr.pOn} tone={tr.p ? 'accent' : tr.pOn ? 'ink' : undefined} dashed={!tr.p && !tr.pOn} />}
      {f.packets.map(p => <Packets key={p.key} path={p.path} t={p.t} label={p.label} short={p.short} />)}
    </>
  )
}
