/**
 * SelfHostTopology — a self-hosted deployment: browser → app container →
 * Postgres + object storage, all inside your network boundary.
 */
import { FigSvg, Window, Cylinder, Label, Route, type FigProps } from './kit'

export default function SelfHostTopology({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="12" name="Self-host topology" className={className} title={title} compact={compact}>
      <Window x={190} y={8} w={100} h={42} />
      <Label x={240} y={39} anchor="middle">Browser</Label>
      <Route points={[[240, 50], [240, 100]]} />
      <rect x={64} y={78} width={352} height={188} rx={2} className="k2 dd" />
      <Label x={76} y={94}>Your network</Label>
      <rect x={170} y={104} width={140} height={48} rx={2} className="k1 w2 fp" />
      <rect x={170} y={104} width={140} height={5} className="ns fa" />
      <Label x={240} y={134} anchor="middle" tone="ink">App container</Label>
      <Route points={[[200, 152], [200, 172], [140, 172], [140, 184]]} />
      <Route points={[[280, 152], [280, 172], [340, 172], [340, 184]]} />
      <Cylinder x={104} y={184} w={72} h={48} rings={1} />
      <Label x={140} y={252} anchor="middle">Postgres</Label>
      <g>
        <rect x={304} y={184} width={72} height={48} rx={2} className="k1 fp" />
        {[0, 1, 2].map(i => <line key={i} x1={312} y1={198 + i * 12} x2={368} y2={198 + i * 12} className="k3 w1" />)}
      </g>
      <Label x={340} y={252} anchor="middle">Object storage</Label>
    </FigSvg>
  )
}
