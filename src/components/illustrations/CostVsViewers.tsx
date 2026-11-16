/**
 * CostVsViewers — cost as the audience grows. A per-seat tool rises with every
 * viewer; Nubi stays flat because viewers compute in their own tab. Illustrative.
 */
import { FigSvg, Label, type FigProps } from './kit'

const X0 = 48, X1 = 456, Y0 = 28, Y1 = 218

export default function CostVsViewers({ className, title, compact }: FigProps) {
  // The chart carries no data labels in the accessible tree, so spell the axis and both series out.
  const label = title && `${title}. Horizontal axis: viewers, 0 to 10,000. Per-seat pricing rises with every viewer; Nubi stays flat at $0 per viewer.`
  return (
    <FigSvg fig="19" name="Cost vs viewers" className={className} title={label} compact={compact}>
      <polygon points={`${X0},${Y1 - 6} ${X1},${Y1 - 6} ${X1},${Y0 + 8}`} className="ns fs" />
      <polyline points={`${X0},${Y0} ${X0},${Y1} ${X1},${Y1}`} className="k1" />
      {[0, 1, 2, 3].map(i => <line key={i} x1={X0 + (i + 1) * 102} y1={Y1} x2={X0 + (i + 1) * 102} y2={Y1 + 5} className="k2" />)}
      <line x1={X0} y1={Y1 - 6} x2={X1} y2={Y1 - 6} className="ka w3" />
      <line x1={X0} y1={Y1 - 6} x2={X1} y2={Y0 + 8} className="k1 w2" />
      {!compact && (
        <>
          <Label x={X0 + 6} y={Y0 + 10} keep>Cost ↑</Label>
          <Label x={X1} y={Y1 + 40} anchor="end" keep>Viewers →</Label>
          {['0', '2.5k', '5k', '7.5k', '10k'].map((t, i) => <Label key={t} x={X0 + i * 102} y={Y1 + 22} anchor={i === 0 ? 'start' : 'middle'} keep>{t}</Label>)}
          <Label x={300} y={Y0 + 40} anchor="end" tone="ink" keep>Per-seat pricing</Label>
          <Label x={X1} y={Y1 - 16} anchor="end" tone="accent" keep>Nubi · $0 per viewer</Label>
        </>
      )}
    </FigSvg>
  )
}
