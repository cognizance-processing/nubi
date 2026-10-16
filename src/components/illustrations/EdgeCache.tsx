/**
 * EdgeCache — content-hashed result cache: many viewers collapse to one
 * warehouse query. Viewers fan in to a cache block, one line goes out.
 */
import { FigSvg, Box, Cylinder, Label, Link, makeIso, type FigProps } from './kit'

export default function EdgeCache({ className, title, compact }: FigProps) {
  const P = makeIso(238, 92, 28)
  return (
    <FigSvg fig="05" name="Edge cache" className={className} title={title} compact={compact}>
      {[0, 1, 2, 3, 4].map(i => {
        const y = 36 + i * 46
        return (
          <g key={i}>
            <rect x={20} y={y} width={26} height={20} rx={2} className="k1 fp" />
            <line x1={20} y1={y + 6} x2={46} y2={y + 6} className="k1" />
            <line x1={46} y1={y + 10} x2={182} y2={133} className="k3" />
          </g>
        )
      })}
      <Box iso={P} x={0} y={0} w={2.4} d={2.4} h={1.6} weight={2} />
      <Label x={238} y={176} anchor="middle" tone="ink">cache</Label>
      <text x={238} y={190} textAnchor="middle" className="lc">sha256 · a3f9…</text>
      <Link x1={298} y1={118} x2={346} y2={118} tone="ka" weight={2} head />
      <Label x={322} y={108} anchor="middle" tone="accent">1 query</Label>
      <Cylinder x={350} y={84} w={74} h={78} rings={2} />
      <Label x={387} y={182} anchor="middle" tone="ink">warehouse</Label>
      {!compact && <Label x={20} y={20}>1,800 viewers</Label>}
    </FigSvg>
  )
}
