/**
 * NotFoundFigure — the 404 figure: a small field of bars with one cube missing,
 * drawn as a dashed ghost in the accent.
 */
import { FigSvg, Box, makeIso, Label, Link, seeded, type FigProps } from './kit'

const COLS = 6, ROWS = 3, GAP = { i: 5, j: 2 }

export default function NotFoundFigure({ className, title, compact }: FigProps) {
  const P = makeIso(208, 84, 38)
  const rnd = seeded(404)
  const bars: { i: number; j: number; h: number }[] = []
  for (let j = 0; j < ROWS; j++) for (let i = 0; i < COLS; i++) {
    if (i === GAP.i && j === GAP.j) continue
    bars.push({ i, j, h: 0.5 + rnd() * 1.3 })
  }
  bars.sort((a, b) => a.i + a.j - (b.i + b.j))
  const ghost = { x: GAP.i + 0.1, y: GAP.j + 0.1, w: 0.8, h: 1.2 }
  const [gx, gy] = P(ghost.x + ghost.w, ghost.y + ghost.w, 0.3 + 0.2)
  const [ex, ey] = P(COLS + 0.9, ROWS - 0.6, 0.3)
  const before = bars.filter(b => b.i + b.j < GAP.i + GAP.j), after = bars.filter(b => b.i + b.j >= GAP.i + GAP.j)
  const cell = (b: { i: number; j: number; h: number }) => <Box key={`${b.i}-${b.j}`} iso={P} x={b.i + 0.12} y={b.j + 0.12} z={0.3} w={0.76} d={0.76} h={b.h} />
  return (
    <FigSvg fig="404" name="Not found" className={className} title={title} compact={compact}>
      <Box iso={P} x={-0.3} y={-0.3} w={COLS + 0.6} d={ROWS + 0.6} h={0.3} weight={2} />
      {before.map(cell)}
      <Box iso={P} x={ghost.x} y={ghost.y} z={0.3} w={ghost.w} d={ghost.w} h={ghost.h} tone="ghost-accent" weight={2} />
      {after.map(cell)}
      {!compact && (
        <>
          <Link x1={gx - 2} y1={gy - 18} x2={ex} y2={ey} tone="k2" lead />
          <Label x={ex + 8} y={ey + 3} tone="accent">Missing</Label>
          <Label x={ex + 8} y={ey + 16}>no page here</Label>
        </>
      )}
    </FigSvg>
  )
}
