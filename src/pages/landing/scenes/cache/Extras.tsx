/**
 * Cache extras (inside the R3F canvas, lazy). Pure functions of the props: no timers.
 *
 *   query   the QUERY packet: first viewer → rack (MISS) → warehouse; the warehouse's top ring flashes on arrival
 *   store   the result slab (an Arrow batch) rides the trunk back and is filed in the rack; a packet serves viewer 1
 *   fanout  live counters while the crowd rises (the callouts take over once the morph settles)
 *   add     the conveyor: one packet per new window
 *   ambient packets stream from the rack to the crowd (≤ 5 s after input, never under reduced motion)
 *
 * The floor traces themselves are layout cells (see geometry.ts), so they also render in the poster.
 */
import { useEffect, useMemo, type ReactElement } from 'react'
import { useFrame } from '@nubi/r3f'
import type { ExtrasProps, Vec3 } from '../../stage/types'
import { Packets, useStage } from '../../stage/extras'
import { project } from '../../stage/camera'
import { fmtInt, viewers } from '../../stage/data'
import { BEAT_LEN, VIEWERS_ANCHOR, type CacheState } from './index'
import {
  ADD, COLS, DB_H, FIRST, IN, Q_RACK, RACK_PASS, ROWS, STORE, TO_DB, WAREHOUSE, feed, pathLen,
} from './geometry'

type P2 = [number, number]
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
const win = (m: number, [a, b]: P2) => clamp01((m - a) / (b - a))
const easeIO = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

/* ── the story clock of this scene (scene-local T of each beat's rest point) ── */
const LABEL: Record<string, number> = {}
let SPAN = BEAT_LEN.in
BEAT_LEN.beats.forEach((b, i) => { SPAN += i === 0 ? 0 : b.morph; LABEL[b.id] = SPAN; SPAN += b.len })

/* ── routes as 3D polylines (packets ride .42 above the floor) ── */
const PY = 0.42
const to3 = (p: P2[], y = PY): Vec3[] => p.map(q => [q[0], y, q[1]])
const QUERY_PATH: P2[] = [...[...feed(FIRST[0], FIRST[1])].reverse(), ...RACK_PASS.slice(1), ...TO_DB.slice(1)]
const QUERY_3 = to3(QUERY_PATH)
const SERVE_3 = to3(feed(FIRST[0], FIRST[1]))
function along(path: Vec3[], t: number): Vec3 {
  const L: number[] = [0]
  for (let i = 1; i < path.length; i++) L.push(L[i - 1] + Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1], path[i][2] - path[i - 1][2]))
  const d = clamp01(t) * L[L.length - 1]
  let i = 1
  while (i < L.length - 1 && L[i] < d) i++
  const u = (d - L[i - 1]) / Math.max(1e-6, L[i] - L[i - 1])
  return [path[i - 1][0] + (path[i][0] - path[i - 1][0]) * u, path[i - 1][1] + (path[i][1] - path[i - 1][1]) * u, path[i - 1][2] + (path[i][2] - path[i - 1][2]) * u]
}

/* ── a DOM chip styled exactly like a callout (used for the live counters during the fan-out morph) ── */
const DIRS: Record<string, [number, number]> = { n: [0, -1], ne: [0.7071, -0.7071], se: [0.7071, 0.7071], s: [0, 1] }
function LiveCallout({ anchor, text, sub, side, lead, accent }: { anchor: Vec3; text: string; sub?: string; side: 'n' | 'ne' | 'se' | 's'; lead: number; accent?: boolean }) {
  const { overlay, view } = useStage()
  const el = useMemo(() => {
    const e = document.createElement('div')
    e.className = 'co'
    e.innerHTML = '<i class="lead"></i><i class="dot"></i><div class="chip"><b></b><span></span></div>'
    return e
  }, [])
  useEffect(() => { overlay()?.appendChild(el); return () => el.remove() }, [el, overlay])
  useFrame(() => {
    const v = view()
    const q = project(v, anchor)
    const narrow = v.w < 480
    const b = el.querySelector('b') as HTMLElement, s = el.querySelector('span') as HTMLElement
    if (b.textContent !== text) b.textContent = text
    const subText = narrow ? '' : sub ?? ''
    if (s.textContent !== subText) { s.textContent = subText; s.style.display = subText ? '' : 'none' }
    const chip = el.querySelector('.chip') as HTMLElement, ld = el.querySelector('.lead') as HTMLElement
    const [dx, dy] = DIRS[side]
    const ex = dx * lead, ey = dy * lead
    const w = chip.offsetWidth, h = chip.offsetHeight
    const cx = dx > 0.1 ? ex : ex - 1, cy = dy < -0.1 ? ey - h : dy > 0.1 ? ey : ey - h / 2
    const lx = cx + 1, ly = dy < -0.1 ? cy + h : dy > 0.1 ? cy : cy + h / 2
    ld.style.width = `${Math.hypot(lx, ly).toFixed(1)}px`
    ld.style.transform = `rotate(${Math.atan2(ly, lx)}rad)`
    chip.style.transform = `translate(${Math.round(cx)}px, ${Math.round(cy)}px)`
    el.style.transform = `translate(${Math.round(q[0])}px, ${Math.round(q[1])}px)`
    el.className = `co${accent ? ' acc' : ''} on`
    void w
  })
  return null
}

const DB_TOP: Vec3 = [WAREHOUSE[0], DB_H + 0.05, WAREHOUSE[2]]

export default function CacheExtras({ local, beat, m, state, reduced, ambientT }: ExtrasProps<CacheState>) {
  if (local > SPAN - 1e-4) return null                       // scrolled past: the next scene owns the stage
  const interacting = m < 1 && local >= (LABEL[beat] ?? 0) - 1e-4
  const scrolling = m < 1 && !interacting
  const nodes: ReactElement[] = []

  /* query: the packet makes the trip (the warehouse's top ring flashes on arrival: layout cells) */
  if (beat === 'query' && !interacting) {
    const u = reduced ? Q_RACK : win(m, IN.packet as P2)
    if (reduced || (m > IN.packet[0] && m < IN.packet[1])) nodes.push(<Packets key="q" path={QUERY_3} t={u} label="QUERY" short="Q" />)
  }

  /* store: the slab and the drawer are layout cells; a packet then serves viewer 1 */
  if (beat === 'store' && scrolling && m > STORE.serve[0] && m < STORE.serve[1]) {
    nodes.push(<Packets key="serve" path={SERVE_3} t={easeIO(win(m, STORE.serve))} label="RESULT" short="R" />)
  }

  /* fanout: live counters while the morph runs (the callouts appear at m ≥ .85) */
  if (beat === 'fanout' && scrolling && m > 0.02 && m < 0.85) {
    const n = Math.round(1 + (viewers(0) - 1) * easeIO(clamp01((m - 0.04) / 0.76)))
    nodes.push(<LiveCallout key="cnt" anchor={VIEWERS_ANCHOR} text={`${fmtInt(n)} viewers`} sub="each reads the edge copy" side="s" lead={30} />)
    nodes.push(<LiveCallout key="wh" anchor={DB_TOP} text="Warehouse" sub="queries: 1" side="ne" lead={26} />)
  }

  /* add (scroll) or any +100 interaction: one packet per new window in the front row */
  if (!reduced && state.crowd && ((beat === 'add' && scrolling) || interacting)) {
    for (let c = 0; c < COLS; c++) {
      const t = win(m, [ADD.packet[0] + c * 0.03, ADD.packet[1] - (COLS - 1 - c) * 0.03] as P2)
      if (t > 0 && t < 1) nodes.push(<Packets key={`add${c}`} path={to3(feed(0, c))} t={easeIO(t)} />)
    }
  }

  /* ambient: packets stream from the rack to the crowd */
  if (!reduced && ambientT > 0 && state.crowd && m >= 1) {
    for (let k = 0; k < ROWS; k++) {
      const c = (k * 3 + 1) % COLS
      const path = to3(feed(k, c))
      const L = pathLen(feed(k, c))
      const ts = [0, 0.5].map(o => ((ambientT * 6) / L + o + k * 0.23) % 1)
      nodes.push(<Packets key={`amb${k}`} path={path} t={ts} />)
    }
  }
  return <>{nodes}</>
}
