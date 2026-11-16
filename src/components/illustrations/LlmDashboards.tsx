/**
 * LlmDashboards — an agent authors a board. A prompt goes in, the spark marks
 * the model step, a finished dashboard comes out.
 */
import { FigSvg, Window, Label, Link, type FigProps } from './kit'

export default function LlmDashboards({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="08" name="Prompt to board" className={className} title={title} compact={compact}>
      <rect x={16} y={86} width={140} height={90} rx={2} className="k1 fp" />
      <Label x={26} y={104}>Prompt</Label>
      <text x={26} y={126} className="lc t1">revenue by region,</text>
      <text x={26} y={142} className="lc t1">last 90 days</text>
      <rect x={26} y={150} width={7} height={13} className="ns fi" />
      <Link x1={156} y1={131} x2={184} y2={131} head />
      {/* spark */}
      <polygon points="214,106 222,123 240,131 222,139 214,156 206,139 188,131 206,123" className="k1 w2 fa" />
      <Link x1={240} y1={131} x2={266} y2={131} head />
      <Label x={214} y={180} anchor="middle" tone="ink">Agent</Label>
      <Label x={214} y={193} anchor="middle">via MCP</Label>
      {/* board */}
      <Window x={272} y={36} w={192} h={190} />
      {[0, 1, 2].map(i => (
        <g key={i}>
          <rect x={282 + i * 60} y={62} width={54} height={34} rx={2} className="k1 fp" />
          <line x1={290 + i * 60} y1={86} x2={290 + i * 60 + 22 + i * 6} y2={86} className="k1 w2" />
        </g>
      ))}
      <rect x={282} y={106} width={112} height={108} rx={2} className="k1 fp" />
      <polyline points="292,196 316,184 340,188 364,164 384,152" className="k1 w2" />
      <rect x={402} y={106} width={52} height={108} rx={2} className="k1 fp" />
      {[0, 1, 2, 3, 4].map(i => <line key={i} x1={410} y1={126 + i * 18} x2={446} y2={126 + i * 18} className="k3 w1" />)}
    </FigSvg>
  )
}
