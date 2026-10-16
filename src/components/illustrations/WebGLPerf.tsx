/**
 * WebGLPerf — a million points at 60 fps on the GPU: a dense, seeded scatter
 * with one brushed selection (the accent) for cross-filtering.
 */
import { FigSvg, Label, seeded, type FigProps } from './kit'

const X0 = 44, X1 = 456, Y0 = 30, Y1 = 224
const BRUSH = { x: 262, y: 74, w: 112, h: 84 }

export default function WebGLPerf({ className, title, compact }: FigProps) {
  const rnd = seeded(1337)
  const g = () => (rnd() + rnd() + rnd() + rnd() - 2) / 2 // ~gaussian in [-1, 1]
  const base: string[] = [], hit: string[] = []
  for (let i = 0; i < 620; i++) {
    const t = rnd()
    const cx = X0 + 20 + t * (X1 - X0 - 40)
    const cy = Y1 - 22 - t * (Y1 - Y0) * 0.55 + g() * 38
    const x = Math.round(cx + g() * 22), y = Math.round(cy)
    if (x < X0 + 4 || x > X1 - 4 || y < Y0 + 2 || y > Y1 - 4) continue
    const inside = x >= BRUSH.x && x <= BRUSH.x + BRUSH.w && y >= BRUSH.y && y <= BRUSH.y + BRUSH.h
    ;(inside ? hit : base).push(`M${x} ${y}h2v2h-2z`)
  }
  return (
    <FigSvg fig="06" name="1M points · 60 fps" className={className} title={title} compact={compact}>
      <polyline points={`${X0},${Y0} ${X0},${Y1} ${X1},${Y1}`} className="k1" />
      {[0, 1, 2, 3, 4, 5].map(i => <line key={i} x1={X0 + 40 + i * 74} y1={Y1} x2={X0 + 40 + i * 74} y2={Y1 + 5} className="k2" />)}
      <path d={base.join('')} className="ns fm" />
      <path d={hit.join('')} className="ns fa" />
      <rect x={BRUSH.x} y={BRUSH.y} width={BRUSH.w} height={BRUSH.h} className="ka w2 dd" />
      {!compact && (
        <>
          <Label x={X0 + 8} y={Y0 + 10} tone="ink">1,048,576 points</Label>
          <Label x={X1} y={Y0 + 10} anchor="end">60 fps · GPU</Label>
          <Label x={BRUSH.x} y={BRUSH.y - 8} tone="accent">Brush · cross-filter</Label>
          <Label x={X1} y={Y1 + 18} anchor="end">Order value →</Label>
        </>
      )}
    </FigSvg>
  )
}
