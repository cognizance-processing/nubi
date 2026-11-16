/**
 * OpenCoreSplit — the open core is the solid block; the optional ee/ module
 * hovers above it (dashed) and docks into one socket, the feature gate (accent).
 */
import { FigSvg, Box, Label, Link, makeIso, type FigProps } from './kit'

export default function OpenCoreSplit({ className, title, compact }: FigProps) {
  const P = makeIso(236, 124, 31)
  const top = 1.8
  const corner = (x: number, y: number, z: number) => P(x, y, z)
  const sock = [corner(1.2, 1.0, top), corner(2.2, 1.0, top), corner(2.2, 2.0, top), corner(1.2, 2.0, top)]
  const ghost = { x: 0.7, y: 0.5, w: 2, d: 2, z: top + 1.3, h: 1.1 }
  const [gx, gy] = corner(2.2, 1.0, top)
  return (
    <FigSvg fig="11" name="Open core" className={className} title={title} compact={compact}>
      <Box iso={P} x={0} y={0} w={3.4} d={3} h={top} weight={2} />
      {/* socket on the core's top face */}
      <polygon points={sock.map(p => p.join(',')).join(' ')} className="ns fa" />
      {/* dashed stems from the hovering module down to the socket */}
      {[[1.2, 1.0], [2.2, 1.0], [2.2, 2.0], [1.2, 2.0]].map(([x, y], i) => {
        const a = corner(x, y, top), b = corner(x, y, ghost.z)
        return <line key={i} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="k2 dd" />
      })}
      <Box iso={P} x={ghost.x} y={ghost.y} z={ghost.z} w={ghost.w} d={ghost.d} h={ghost.h} tone="ghost" />
      {!compact && (
        <>
          <Label x={20} y={34} tone="ink">Core</Label>
          <Label x={20} y={47}>Apache-2.0</Label>
          <Label x={336} y={34} tone="ink">ee/</Label>
          <Label x={336} y={47}>Licensed module</Label>
          <Link x1={gx + 8} y1={gy + 10} x2={gx + 66} y2={gy + 54} tone="k2" lead />
          <Label x={gx + 70} y={gy + 62} tone="accent">Feature gate</Label>
        </>
      )}
    </FigSvg>
  )
}
