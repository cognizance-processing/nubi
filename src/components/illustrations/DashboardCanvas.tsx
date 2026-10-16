/**
 * DashboardCanvas — "Compose a dashboard, embed it anywhere."
 * Widget palette, a dotted canvas with KPI / chart / table tiles, and one tile
 * mid-drag over its dashed drop target (the accent).
 */
import { FigSvg, Window, Label } from './kit'

export default function DashboardCanvas({ className, title, compact }: { className?: string; title?: string; compact?: boolean }) {
  return (
    <FigSvg fig="03" name="Dashboard canvas" className={className} title={title} compact={compact}>
      <Window x={16} y={16} w={448} h={228} />
      {/* palette rail */}
      <line x1={64} y1={32} x2={64} y2={244} className="k1" />
      {[0, 1, 2, 3].map(i => <rect key={i} x={30} y={48 + i * 32} width={20} height={20} rx={2} className="k2" />)}
      <polyline points="33,63 38,57 43,60 47,53" className="k2" />
      {/* dotted canvas */}
      {Array.from({ length: 10 }).map((_, i) => <line key={i} x1={74} y1={44 + i * 20} x2={454} y2={44 + i * 20} className="dg" />)}
      {/* KPI tiles */}
      {[0, 1, 2].map(i => (
        <g key={i}>
          <rect x={78 + i * 96} y={40} width={88} height={42} rx={2} className="k1 fp" />
          <Label x={86 + i * 96} y={56}>{['Revenue', 'Orders', 'Margin'][i]}</Label>
          <text x={86 + i * 96} y={74} className="t1 big">{['$4.2M', '38.1k', '31.4%'][i]}</text>
        </g>
      ))}
      {/* chart tile */}
      <rect x={78} y={94} width={186} height={138} rx={2} className="k1 fp" />
      <Label x={86} y={110}>Revenue · 90 days</Label>
      <line x1={88} y1={216} x2={254} y2={216} className="k3" />
      <polyline points="88,210 112,200 136,204 160,180 184,186 208,162 232,154 256,132" className="k1 w2" />
      {/* table tile */}
      <rect x={366} y={40} width={88} height={192} rx={2} className="k1 fp" />
      <line x1={366} y1={62} x2={454} y2={62} className="k1 w2" />
      {[0, 1, 2, 3, 4, 5, 6, 7].map(i => <line key={i} x1={374} y1={80 + i * 19} x2={446} y2={80 + i * 19} className="k3 w1" />)}
      {/* drop target + tile in flight */}
      <rect x={276} y={94} width={80} height={138} rx={2} className="ka w2 dd" />
      <g>
        <rect x={290} y={112} width={80} height={62} rx={2} className="k1 fp w2" />
        <rect x={300} y={146} width={9} height={16} className="k1 fl" /><rect x={314} y={138} width={9} height={24} className="k1 fl" />
        <rect x={328} y={128} width={9} height={34} className="k1 fl" /><rect x={342} y={142} width={9} height={20} className="k1 fl" />
        <polygon points="352,160 352,184 358,178 364,190 369,188 363,176 371,176" className="k1 fi" />
      </g>
    </FigSvg>
  )
}
