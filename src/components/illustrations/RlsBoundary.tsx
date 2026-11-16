/**
 * RlsBoundary — row-level security. Signed claims become a predicate that is
 * injected server-side; rows outside the claim never leave the warehouse.
 */
import { FigSvg, Label, Route, type FigProps } from './kit'

const ROWS = ['EMEA', 'AMER', 'EMEA', 'APAC', 'EMEA', 'LATAM']

export default function RlsBoundary({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="15" name="Row-level security" className={className} title={title} compact={compact}>
      <rect x={16} y={44} width={168} height={52} rx={2} className="k1 fp" />
      <Label x={28} y={62}>Verified claim</Label>
      <text x={28} y={83} className="lc t1">{'{ region: "EMEA" }'}</text>
      <Route points={[[100, 96], [100, 126]]} />
      <rect x={16} y={128} width={168} height={52} rx={2} className="k1 w2 fp" />
      <Label x={28} y={146}>Injected predicate</Label>
      <text x={28} y={167} className="lc t1">{"AND region = 'EMEA'"}</text>
      <Route points={[[184, 154], [216, 154]]} />
      <Label x={16} y={214} tone="ink">Server-side only.</Label>
      <Label x={16} y={227}>Browser can’t remove it.</Label>
      {/* table */}
      <rect x={220} y={36} width={244} height={206} rx={2} className="k1 fp" />
      <line x1={220} y1={62} x2={464} y2={62} className="k1 w2" />
      <Label x={232} y={53} tone="ink">Region</Label>
      <Label x={452} y={53} anchor="end" tone="ink">USD</Label>
      {ROWS.map((r, i) => {
        const keep = r === 'EMEA', y = 62 + i * 29
        return (
          <g key={i}>
            <line x1={220} y1={y + 29} x2={464} y2={y + 29} className={keep ? 'k3 w1' : 'k3 w1 dd'} />
            {keep && <rect x={220} y={y + 1} width={4} height={28} className="ns fa" />}
            <text x={232} y={y + 19} className={keep ? 't1' : ''}>{r}</text>
            <text x={452} y={y + 19} textAnchor="end" className={keep ? 't1' : ''}>{keep ? ['0.97M', '0.52M', '0.31M'][i / 2 | 0] : '— —'}</text>
          </g>
        )
      })}
    </FigSvg>
  )
}
