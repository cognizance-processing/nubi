/**
 * Security extras (inside the R3F canvas, lazy). Pure functions of the props: no timers.
 *
 * The claim path: a 2 px line from each viewer's badge (it emerges from behind the card) to the token docked in their window's query bar, on the
 * exact parabola the token flies (the engine's 'arc' morph: straight line + arc·4k(1−k) in y). It draws itself
 * behind the token while it flies (accent), stays accent while `claim` rests, then idles as a dashed ink line
 * on `filter` and `swap`: this badge feeds this query. A viewer with no claim has no line.
 */
import { useMemo } from 'react'
import type { ExtrasProps, Vec3 } from '../../stage/types'
import { Trace } from '../../stage/extras'
import { badgeAt, slotAt, type SecurityState, type Side } from './build'
import { CLAIM_ARC, CLAIM_SPREAD } from './index'

const N = 28
const cubicInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

function arcPath(side: Side): Vec3[] {
  const a = badgeAt(side), b = slotAt(side)
  const pts: Vec3[] = []
  for (let i = 0; i <= N; i++) {
    const k = i / N
    pts.push([a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k + CLAIM_ARC * 4 * k * (1 - k), a[2] + (b[2] - a[2]) * k])
  }
  return pts
}
const PATHS: Record<Side, Vec3[]> = { a: arcPath('a'), b: arcPath('b') }

function ClaimPath({ side, beat, m, state }: { side: Side; beat: string; m: number; state: SecurityState }) {
  // while the token flies on `claim`, the line is drawn up to the token (its e with order 0)
  const k = beat === 'claim' ? cubicInOut(Math.min(1, m / (1 - CLAIM_SPREAD))) : 1
  const n = Math.max(1, Math.round(k * N))
  const pts = useMemo(() => PATHS[side].slice(0, n + 1), [side, n])
  if (state[side] === 'None' || !state.claimed || beat === 'same' || n < 2) return null
  return <Trace points={pts} active={beat === 'claim'} dashed={beat !== 'claim'} width={2} />
}

export default function SecurityExtras({ beat, m, state }: ExtrasProps<SecurityState>) {
  return (<>
    <ClaimPath side="a" beat={beat} m={m} state={state} />
    <ClaimPath side="b" beat={beat} m={m} state={state} />
  </>)
}
