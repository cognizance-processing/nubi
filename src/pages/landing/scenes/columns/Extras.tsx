/**
 * Columns extras (lazy, inside the stage canvas): the read path.
 *   - a solid accent floor trace from the pillar being read to the chart's window (PCB route, chamfered)
 *   - Arrow record batches (packets) travelling along it into the window
 *   - a soft-accent read-head ring climbing the pillar (ambient, ≤ 5 s after the last input)
 * Pure function of the props. With no ambient time (idle, reduced motion, screenshots) everything sits at a
 * composed still: packets on the route, the ring parked under the lid (at mid-height it reads as a crack).
 */
import type { ExtrasProps, Vec3 } from '../../stage/types'
import { TONE } from '../../stage/types'
import { InkBoxes, Packets, Trace, chamfer } from '../../stage/extras'
import { BODY_H, N_COLS, READ_DEFAULT, TOWER_W, towerD, towerX, towerZ, tracePoints, type ColumnsState } from './layouts'

/** One route per column, built once (Trace rebuilds its geometry when the array identity changes). */
const ROUTES: Vec3[][] = Array.from({ length: N_COLS }, (_, c) => chamfer(tracePoints(c), 1.4))

const SCAN_SPEED = 4            // units / s (storyboard §3.3)
const PACKET_SPEED = 0.4        // route lengths / s

/** A hollow ring hugging the pillar (a read head), so the pillar stays whole inside it. */
function ring(c: number, y: number) {
  const x = towerX(c), z = towerZ(c)
  const g = 0.28, t = 0.22, h = 0.24, w = TOWER_W + 2 * g + t, d = towerD(c) + 2 * g + t
  const tone = TONE.ACCENT_SOFT
  return [
    { p: [x, y, z + d / 2 - t / 2] as Vec3, s: [w, h, t] as Vec3, tone },
    { p: [x, y, z - d / 2 + t / 2] as Vec3, s: [w, h, t] as Vec3, tone },
    { p: [x - w / 2 + t / 2, y, z] as Vec3, s: [t, h, d - 2 * t] as Vec3, tone },
    { p: [x + w / 2 - t / 2, y, z] as Vec3, s: [t, h, d - 2 * t] as Vec3, tone },
  ]
}

export default function ColumnsExtras({ beat, m, state, reduced, ambientT }: ExtrasProps<ColumnsState>) {
  const c = state.read
  if (beat === 'table' || c < 0 || c >= N_COLS) return null
  // the scroll morph into `read` draws the path once the pillar has turned; a visitor's re-read shows it at once
  // (Extras are not re-rendered when an interaction settles, so `m` must not gate the interactive case)
  if (beat === 'read' && c === READ_DEFAULT && m < 0.92) return null
  const live = !reduced && ambientT > 0
  // read head: climbs from the floor to the top of the values, then starts again; the still parks under the lid
  const top = BODY_H - 0.3, cycle = top / SCAN_SPEED + 0.6
  const yScan = live ? Math.min(top, 0.2 + ((ambientT % cycle) * SCAN_SPEED)) : top
  const a = live ? (ambientT * PACKET_SPEED) % 1 : 0
  const ts = live ? [a, (a + 1 / 3) % 1, (a + 2 / 3) % 1] : [0.28, 0.62]
  return (
    <>
      <Trace points={ROUTES[c]} active />
      <Packets path={ROUTES[c]} t={ts} size={0.6} />
      <InkBoxes boxes={ring(c, yScan)} max={4} />
    </>
  )
}
