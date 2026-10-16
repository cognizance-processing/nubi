/**
 * KernelInBrowser — the compute kernel (DuckDB-WASM) runs inside the viewer's
 * tab: a chip drawn inside a browser window, SQL going in, a chart coming out.
 */
import { FigSvg, Window, Label, Link, type FigProps } from './kit'

export default function KernelInBrowser({ className, title, compact }: FigProps) {
  const top = [0, 1, 2, 3, 4, 5]
  return (
    <FigSvg fig="04" name="Kernel in the tab" className={className} title={title} compact={compact}>
      <Window x={56} y={14} w={368} h={244} />
      <Label x={72} y={48} tone="ink">DuckDB-WASM</Label>
      <Label x={408} y={48} anchor="end">in this tab</Label>
      {/* pins */}
      {top.map(i => (
        <g key={i}>
          <line x1={196 + i * 18} y1={72} x2={196 + i * 18} y2={84} className="k2" />
          <line x1={196 + i * 18} y1={168} x2={196 + i * 18} y2={180} className="k2" />
        </g>
      ))}
      {[0, 1, 2, 3, 4].map(i => (
        <g key={i}>
          <line x1={166} y1={90 + i * 18} x2={178} y2={90 + i * 18} className="k2" />
          <line x1={298} y1={90 + i * 18} x2={310} y2={90 + i * 18} className="k2" />
        </g>
      ))}
      <rect x={178} y={84} width={120} height={84} rx={2} className="k1 w2 fp" />
      <rect x={198} y={100} width={80} height={52} rx={2} className="ns fa" />
      {[0, 1, 2, 3].map(c => [0, 1].map(r => (
        <rect key={`${c}${r}`} x={206 + c * 18} y={110 + r * 20} width={12} height={10} className="ns fp" />
      )))}
      {/* sql in, chart out */}
      <Link x1={88} y1={126} x2={162} y2={126} head />
      <Label x={88} y={118}>SQL</Label>
      <Link x1={238} y1={184} x2={238} y2={202} head />
      <polyline points="150,244 190,232 224,238 262,216 300,224 332,206" className="k1 w2" />
      <Label x={408} y={246} anchor="end">Arrow → chart</Label>
    </FigSvg>
  )
}
