/**
 * Board extras (lazy, inside the stage canvas). Pure functions of the props: no timers.
 * - The auto-demo cursor on the `click` beat: travels to day 10's bar, presses, and the click ring plays.
 *   It stays parked on the bar through `follow`, and never shows once the visitor has clicked themselves.
 * - Reduced motion: the tiles a filter changed get an accent outline instead of the wave.
 */
import type { ExtrasProps, Vec3 } from '../../stage/types'
import { TONE } from '../../stage/types'
import { Cursor, InkBoxes, type BoxSpec } from '../../stage/extras'
import { BOARD_PLATE_FRONT, cursorTarget, updatedTiles, type BoardState } from './layout'
import scene from './index'

const TARGET = cursorTarget(9)
const START: Vec3 = [TARGET[0] + 10, TARGET[1] - 8.5, TARGET[2] + 3]
const PRESS: Vec3 = [TARGET[0] - 0.05, TARGET[1] - 0.12, TARGET[2] - 0.35]
const PATH = [START, TARGET, PRESS]
const d = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])
/** Path parameter at which the cursor reaches the bar (the rest of the path is the press). */
const CLICK = d(START, TARGET) / (d(START, TARGET) + d(TARGET, PRESS))
const easeOut = (t: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3)
const easeInOut = (t: number) => { const x = Math.min(1, Math.max(0, t)); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2 }

/** Story length of this scene (beats): past it, the board is the FROM scene of the next transition. */
const SPAN = (scene.in?.len ?? 0) + scene.beats.reduce((a, b) => a + b.len + (b.morph?.len ?? 0), 0)

/** Cursor progress on the `click` beat as a function of its morph m (the bar presses at m ≈ .74). */
function cursorT(m: number): number {
  if (m < 0.08) return 0
  if (m < 0.6) return easeInOut((m - 0.08) / 0.52) * CLICK
  if (m < 0.66) return CLICK
  return CLICK + (1 - CLICK) * easeOut((m - 0.66) / 0.24)
}

const RULE = 0.16
function outline(r: [number, number, number, number]): BoxSpec[] {
  const [x0, y0, x1, y1] = r, z = BOARD_PLATE_FRONT + 0.08, w = x1 - x0, h = y1 - y0
  return [
    { p: [x0 + w / 2, y1 - RULE / 2, z], s: [w, RULE, 0.14], tone: TONE.ACCENT },
    { p: [x0 + w / 2, y0 + RULE / 2, z], s: [w, RULE, 0.14], tone: TONE.ACCENT },
    { p: [x0 + RULE / 2, y0 + h / 2, z], s: [RULE, h - 2 * RULE, 0.14], tone: TONE.ACCENT },
    { p: [x1 - RULE / 2, y0 + h / 2, z], s: [RULE, h - 2 * RULE, 0.14], tone: TONE.ACCENT },
  ]
}

export default function BoardExtras({ beat, m, state, reduced, local }: ExtrasProps<BoardState>) {
  const leaving = local > SPAN + 1e-3
  const auto = !state.user && !leaving
  let t = -1
  if (auto && beat === 'click') t = reduced ? 1 : cursorT(m)
  else if (auto && beat === 'follow' && state.day === 9) t = 1
  const frames = reduced && !leaving ? updatedTiles(state).flatMap(outline) : []
  return (
    <>
      {t > 0 && <Cursor path={PATH} t={t} click={CLICK} />}
      {frames.length > 0 && <InkBoxes boxes={frames} />}
    </>
  )
}
