/**
 * HeroIllustration — "BI that runs in your browser."
 * A board drawn as an axonometric sheet: a slab (the tab) with a field of bars
 * rising from it, one series in the accent. Same construction as the landing's
 * cube field, small enough for the docs quickstart.
 */
import { FigSvg, Box, makeIso, Label, Link, seeded, type FigProps } from './kit'

export default function HeroIllustration({ className, title, compact }: FigProps) {
  const P = makeIso(212, 88, 34)
  const rnd = seeded(11)
  const COLS = 6, ROWS = 4, ACC = 2
  const bars = []
  for (let j = 0; j < ROWS; j++) for (let i = 0; i < COLS; i++) {
    const h = j === ACC ? 0.7 + i * 0.42 : 0.4 + rnd() * 1.7
    bars.push({ i, j, x: 0.5 + i * 0.95, y: 0.5 + j * 0.92, h })
  }
  bars.sort((a, b) => a.x + a.y - (b.x + b.y))
  const tip = bars.find(b => b.j === ACC && b.i === COLS - 1)!
  const [tx, ty] = P(tip.x + 0.3, tip.y + 0.3, 0.35 + tip.h)
  return (
    <FigSvg fig="01" name="Board in a tab" className={className} title={title} compact={compact}>
      <Box iso={P} x={0} y={0} w={6.2} d={4.4} h={0.35} tone="paper" weight={2} />
      {bars.map(b => (
        <Box key={`${b.i}-${b.j}`} iso={P} x={b.x} y={b.y} z={0.35} w={0.6} d={0.6} h={b.h} tone={b.j === ACC ? 'accent' : 'paper'} />
      ))}
      {!compact && (
        <>
          <Link x1={tx + 8} y1={ty - 2} x2={tx + 44} y2={ty - 30} tone="k2" lead />
          <Label x={tx + 50} y={ty - 32} tone="accent">Your series</Label>
          <Label x={tx + 50} y={ty - 20}>store 21 · revenue</Label>
          <Label x={0} y={14} tone="ink">1,048,576 rows</Label>
          <Label x={0} y={27}>in this tab</Label>
        </>
      )}
    </FigSvg>
  )
}
