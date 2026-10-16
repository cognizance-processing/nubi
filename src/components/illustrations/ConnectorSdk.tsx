/**
 * ConnectorSdk — SQL-first connector SDK. Different sources fan in to one SDK
 * module that returns a uniform, queryable result.
 */
import { FigSvg, Window, Label, Link, Cylinder, type FigProps } from './kit'

export default function ConnectorSdk({ className, title, compact }: FigProps) {
  const srcs = [{ y: 30, t: 'SQL' }, { y: 100, t: 'ƒ()' }, { y: 170, t: 'REST' }]
  return (
    <FigSvg fig="09" name="Connector SDK" className={className} title={title} compact={compact}>
      {srcs.map((s, i) => (
        <g key={s.t}>
          <rect x={16} y={s.y} width={96} height={52} rx={2} className="k1 fp" />
          {i === 0 ? <Cylinder x={26} y={s.y + 12} w={26} h={30} tone="k2" /> : null}
          <text x={i === 0 ? 62 : 28} y={s.y + 31} className="t1 big">{s.t}</text>
          <Link x1={112} y1={s.y + 26} x2={146} y2={126} tone="k2" />
        </g>
      ))}
      <rect x={150} y={96} width={100} height={60} rx={2} className="ns fa" />
      <text x={200} y={131} textAnchor="middle" className="tp big">SDK { '{ }' }</text>
      <Link x1={250} y1={126} x2={296} y2={126} head />
      <Label x={200} y={174} anchor="middle" tone="ink">Connector</Label>
      <Window x={302} y={68} w={162} h={116} />
      <polyline points="314,166 338,152 360,158 386,136 412,142 450,112" className="k1 w2" />
      {[0, 1, 2].map(i => <line key={i} x1={314} y1={104 + i * 12} x2={400 - i * 18} y2={104 + i * 12} className="k3 w1" />)}
      <Label x={302} y={204}>Arrow result</Label>
    </FigSvg>
  )
}
