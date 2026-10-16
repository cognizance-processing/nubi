/**
 * Columns extras (lazy, inside the stage canvas): the read path.
 *   - a solid accent floor trace from the tower being read to the KPI board (PCB route, chamfered)
 *   - value packets travelling along it
 *   - a soft-accent scanline ring climbing the tower (ambient, ≤ 5 s after the last input)
 * Pure function of the props. With no ambient time (idle, reduced motion, screenshots) everything sits at a
 * composed still: packets mid-route, the scanline at mid-height.
 */
import type { ExtrasProps, Vec3 } from '../../stage/types'
import { TONE } from '../../stage/types'
import { InkBoxes, Packets, Trace, chamfer } from '../../stage/extras'
import { LAYERS, N_COLS, TOWER_D, Z_TOWER, towerX, tracePoints, type ColumnsState } from './layouts'

/** One route per column, built once (Trace rebuilds its geometry when the array identity changes). */
const ROUTES: Vec3[][] = Array.from({ length: N_COLS }, (_, c) => chamfer(tracePoints(c), 1.4))

const SCAN_SPEED = 4            // units / s (storyboard §3.3)
const PACKET_SPEED = 0.45       // route lengths / s

/** A hollow square ring hugging the pillar (a read head), so the pillar stays whole inside it. */
function ring(x: number, y: number) {
  const g = 0.32, t = 0.2, h = 0.2, w = 4 + 2 * g + t, d = TOWER_D + 2 * g + t
  const tone = TONE.ACCENT_SOFT
  return [
    { p: [x, y, Z_TOWER + d / 2 - t / 2] as Vec3, s: [w, h, t] as Vec3, tone },
    { p: [x, y, Z_TOWER - d / 2 + t / 2] as Vec3, s: [w, h, t] as Vec3, tone },
    { p: [x - w / 2 + t / 2, y, Z_TOWER] as Vec3, s: [t, h, d - 2 * t] as Vec3, tone },
    { p: [x + w / 2 - t / 2, y, Z_TOWER] as Vec3, s: [t, h, d - 2 * t] as Vec3, tone },
  ]
}

export default function ColumnsExtras({ beat, m, state, reduced, ambientT }: ExtrasProps<ColumnsState>) {
  const c = state.read
  if (beat === 'table' || c < 0 || c >= N_COLS || m < 0.92) return null
  const live = !reduced && ambientT > 0
  // scanline: climbs from the floor to just under the lid, rests there, repeats; the composed still is the rest
  const top = LAYERS - 0.35, climb = top / SCAN_SPEED, cycle = climb + 1.2
  const u = live ? ambientT % cycle : cycle
  const yScan = Math.min(top, 0.15 + (u / climb) * (top - 0.15))
  const scan = yScan >= 0 && yScan <= LAYERS ? ring(towerX(c), yScan) : []
  const a = live ? (ambientT * PACKET_SPEED) % 1 : 0
  const ts = live ? [a, (a + 1 / 3) % 1, (a + 2 / 3) % 1] : [0.3, 0.68]
  return (
    <>
      <Trace points={ROUTES[c]} active />
      <Packets path={ROUTES[c]} t={ts} size={0.6} />
      <InkBoxes boxes={scan} max={4} />
    </>
  )
}
