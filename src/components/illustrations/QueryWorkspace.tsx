/**
 * QueryWorkspace — "Write SQL, see results instantly, in the browser."
 * SQL pane on the left (one {{param}} in the accent), Run key, and the result
 * (a small bar chart over a ruled table) on the right.
 */
import { FigSvg, Window, Label, Link, type FigProps } from './kit'

const ROWS = [['AMER', '1.24M'], ['EMEA', '0.97M'], ['APAC', '0.71M'], ['LATAM', '0.28M']]
const BARS = [34, 46, 58, 40, 70]

export default function QueryWorkspace({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="02" name="Query workspace" className={className} title={title} compact={compact}>
      {/* editor */}
      <Window x={16} y={20} w={226} h={222} />
      {[0, 1, 2, 3, 4, 5].map(i => <text key={i} x={28} y={56 + i * 18} className="ff" style={{ fill: 'var(--fig-faint)' }}>{i + 1}</text>)}
      <text x={44} y={56} className="lc"><tspan className="t1">select</tspan> region, sum(usd)</text>
      <text x={44} y={74} className="lc"><tspan className="t1">from</tspan> orders</text>
      <text x={44} y={92} className="lc"><tspan className="t1">where</tspan> region =</text>
      <rect x={44} y={98} width={64} height={16} className="ka w2" />
      <text x={50} y={110} className="ta lc">{'{{region}}'}</text>
      <text x={44} y={128} className="lc"><tspan className="t1">group by</tspan> 1</text>
      <text x={44} y={146} className="lc"><tspan className="t1">order by</tspan> 2 <tspan className="t1">desc</tspan></text>
      <rect x={28} y={206} width={62} height={22} rx={2} className="ns fi" />
      <text x={38} y={221} className="tp">Run</text>
      <Label x={226} y={221} anchor="end">Ctrl + Enter · 12 ms</Label>
      {/* result */}
      <Link x1={242} y1={131} x2={262} y2={131} tone="k1" head />
      <Window x={262} y={20} w={202} h={222} />
      {BARS.map((h, i) => (
        <rect key={i} x={278 + i * 22} y={118 - h} width={14} height={h} rx={0}
          className={i === 4 ? 'ns fa' : 'k1 fl'} />
      ))}
      <line x1={274} y1={118} x2={452} y2={118} className="k1" />
      <line x1={274} y1={134} x2={452} y2={134} className="k1 w2" />
      <Label x={278} y={149} tone="ink">Region</Label>
      <Label x={452} y={149} anchor="end" tone="ink">USD</Label>
      {ROWS.map(([r, v], i) => (
        <g key={r}>
          <line x1={274} y1={158 + i * 20} x2={452} y2={158 + i * 20} className="k3 w1" />
          <Label x={278} y={173 + i * 20}>{r}</Label>
          <Label x={452} y={173 + i * 20} anchor="end">{v}</Label>
        </g>
      ))}
    </FigSvg>
  )
}

