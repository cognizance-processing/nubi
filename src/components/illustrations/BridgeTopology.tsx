/**
 * BridgeTopology — the bridge agent sits inside your network and dials out.
 * No inbound ports: the one connection (the accent) is outbound TLS.
 */
import { FigSvg, Cylinder, Label, Route, type FigProps } from './kit'

export default function BridgeTopology({ className, title, compact }: FigProps) {
  return (
    <FigSvg fig="16" name="Bridge agent" className={className} title={title} compact={compact}>
      <rect x={16} y={30} width={186} height={218} rx={2} className="k2 dd" />
      <rect x={298} y={30} width={166} height={218} rx={2} className="k1 w2" />
      <Label x={28} y={50}>Your network</Label>
      <Label x={310} y={50} tone="ink">Nubi cloud</Label>
      <Cylinder x={28} y={78} w={46} h={50} rings={1} />
      <Cylinder x={28} y={146} w={46} h={50} rings={1} />
      <Label x={51} y={218} anchor="middle">Databases</Label>
      <Route points={[[74, 103], [98, 103]]} />
      <Route points={[[74, 171], [98, 171]]} />
      <rect x={98} y={86} width={90} height={100} rx={2} className="k1 w2 fp" />
      <rect x={98} y={86} width={90} height={5} className="ns fi" />
      <Label x={143} y={134} anchor="middle" tone="ink">Bridge</Label>
      <Label x={143} y={147} anchor="middle" tone="ink">agent</Label>
      <rect x={326} y={86} width={110} height={100} rx={2} className="k1 fp" />
      <Label x={381} y={141} anchor="middle" tone="ink">Nubi API</Label>
      <Route points={[[188, 136], [326, 136]]} tone="ka" weight={2} />
      <Label x={250} y={124} anchor="middle" tone="accent">Outbound</Label>
      <Label x={250} y={158} anchor="middle">TLS · 443</Label>
      {!compact && <Label x={381} y={228} anchor="middle">No inbound ports</Label>}
    </FigSvg>
  )
}
