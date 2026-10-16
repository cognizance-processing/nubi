/**
 * TrustBoundary — the browser is untrusted, the server is trusted. Only signed
 * claims go up and only scoped Arrow comes back, through one gate (the accent).
 */
import { FigSvg, Window, Cylinder, Label, Route, type FigProps } from './kit'

export default function TrustBoundary({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="13" name="Trust boundary" className={className} title={title} compact={compact}>
      <rect x={16} y={22} width={204} height={226} rx={2} className="k2 dd" />
      <rect x={260} y={22} width={204} height={226} rx={2} className="k1 w2" />
      {!compact && (
        <>
          <Label x={28} y={42}>Browser · untrusted</Label>
          <Label x={272} y={42} tone="ink">Server · trusted</Label>
        </>
      )}
      <Window x={36} y={80} w={164} h={110} bar={14} />
      <Label x={46} y={116}>DuckDB-WASM</Label>
      <polyline points="46,172 74,160 100,164 128,144 158,150 188,128" className="k1 w2" />
      <Cylinder x={280} y={78} w={60} h={62} rings={2} />
      <Label x={310} y={156} anchor="middle">Data</Label>
      <rect x={364} y={86} width={80} height={54} rx={2} className="k1 fp" />
      <text x={404} y={110} textAnchor="middle" className="t1">Secrets</text>
      {[0, 1, 2, 3, 4, 5].map(i => <rect key={i} x={374 + i * 11} y={120} width={5} height={5} className="ns fm" />)}
      <Route points={[[200, 116], [280, 116]]} />
      <Route points={[[262, 152], [200, 152]]} tone="k2" />
      <rect x={236} y={98} width={8} height={72} className="ns fa" />
      <Label x={240} y={190} anchor="middle" tone="accent">Gate</Label>
      <Label x={36} y={220}>Signed claims ↑</Label>
      <Label x={36} y={234}>Scoped Arrow ↓</Label>
    </FigSvg>
  )
}
