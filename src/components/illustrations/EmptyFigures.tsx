/**
 * Empty-state figures. 160x100 viewBox, legible at 120px wide. No FIG caption or
 * labels: these sit above an EmptyState title. Strokes are 1.5px at any size,
 * the ink is `currentColor` (set a text-* colour on the parent to retint) and
 * there is one accent element each. A dashed outline always means "not there yet".
 */
import { FigSvg, Box, Cylinder, makeIso, type FigProps } from './kit'

type EP = Pick<FigProps, 'className' | 'title'>
const S = ({ className, title, children }: EP & { children: React.ReactNode }) => (
  <FigSvg w={160} h={100} className={className} title={title}>{children}</FigSvg>
)

/** No boards yet: a sheet with ghost tiles and one add-tile in the accent. */
export function EmptyBoards(p: EP) {
  return (
    <S {...p}>
      <rect x={16} y={12} width={128} height={76} rx={2} className="k1 fp" />
      <line x1={16} y1={26} x2={144} y2={26} className="k1" />
      <rect x={24} y={34} width={44} height={22} rx={2} className="k2 dd" />
      <rect x={24} y={62} width={112} height={20} rx={2} className="k2 dd" />
      <rect x={76} y={34} width={60} height={22} rx={2} className="ka w2 fp" />
      <line x1={106} y1={40} x2={106} y2={50} className="ka w2" />
      <line x1={101} y1={45} x2={111} y2={45} className="ka w2" />
    </S>
  )
}

/** No queries: an empty editor with a prompt and a blinking-block caret. */
export function EmptyQueries(p: EP) {
  return (
    <S {...p}>
      <rect x={16} y={12} width={128} height={76} rx={2} className="k1 fp" />
      <line x1={16} y1={26} x2={144} y2={26} className="k1" />
      <polyline points="28,40 35,46 28,52" className="k1 w2" />
      <rect x={42} y={40} width={7} height={13} className="ns fa" />
      <line x1={28} y1={64} x2={96} y2={64} className="k3 dd" />
      <line x1={28} y1={74} x2={72} y2={74} className="k3 dd" />
    </S>
  )
}

/** No connectors: a plug with an open line to a ghost database. */
export function EmptyConnectors(p: EP) {
  return (
    <S {...p}>
      <rect x={14} y={38} width={34} height={24} rx={2} className="k1 fp" />
      <line x1={48} y1={45} x2={58} y2={45} className="k1 w2" />
      <line x1={48} y1={55} x2={58} y2={55} className="k1 w2" />
      <line x1={58} y1={50} x2={92} y2={50} className="ka w2 dd" />
      <Cylinder x={96} y={24} w={50} h={56} tone="k2" rings={1} />
    </S>
  )
}

/** No data: a table with a header rule and ghost rows. */
export function EmptyData(p: EP) {
  return (
    <S {...p}>
      <rect x={16} y={12} width={128} height={76} rx={2} className="k1 fp" />
      <line x1={16} y1={30} x2={144} y2={30} className="ka w2" />
      <line x1={58} y1={12} x2={58} y2={88} className="k3" />
      <line x1={100} y1={12} x2={100} y2={88} className="k3" />
      {[0, 1, 2].map(i => <line key={i} x1={16} y1={46 + i * 16} x2={144} y2={46 + i * 16} className="k2 dd w1" />)}
      {[0, 1, 2].map(i => <rect key={i} x={24} y={36 + i * 16} width={28} height={4} className="ns ff" />)}
    </S>
  )
}

/** No flows: a lone start node (accent) and ghost steps to come. */
export function EmptyFlows(p: EP) {
  return (
    <S {...p}>
      <line x1={46} y1={50} x2={56} y2={50} className="k2 dd" />
      <polyline points="56,50 56,30 66,30" className="k2 dd" />
      <polyline points="56,50 56,70 66,70" className="k2 dd" />
      <polyline points="98,30 108,30 108,50 118,50" className="k2 dd" />
      <polyline points="98,70 108,70 108,50 118,50" className="k2 dd" />
      <rect x={14} y={38} width={32} height={24} rx={2} className="ka w2 fp" />
      <rect x={66} y={19} width={32} height={22} rx={2} className="k2 dd fp" />
      <rect x={66} y={59} width={32} height={22} rx={2} className="k2 dd fp" />
      <rect x={118} y={38} width={28} height={24} rx={2} className="k2 dd fp" />
    </S>
  )
}

/** No watches: a quiet series under a dashed threshold (accent). */
export function EmptyWatches(p: EP) {
  return (
    <S {...p}>
      <polyline points="16,12 16,86 146,86" className="k1" />
      <line x1={16} y1={34} x2={146} y2={34} className="ka w2 dd" />
      <polyline points="24,68 42,64 58,70 76,60 94,66 112,58 134,62" className="k1" />
      <rect x={134} y={59} width={6} height={6} className="ns fi" />
    </S>
  )
}

/** Nothing matches: a lens over ghost rows, handle in the accent. */
export function EmptySearch(p: EP) {
  return (
    <S {...p}>
      {[0, 1, 2].map(i => <line key={i} x1={16} y1={26 + i * 22} x2={52 - i * 6} y2={26 + i * 22} className="k2 dd" />)}
      <circle cx={84} cy={46} r={26} className="k1 w2 fp" />
      <line x1={78} y1={40} x2={90} y2={52} className="k2" />
      <line x1={90} y1={40} x2={78} y2={52} className="k2" />
      <line x1={103} y1={65} x2={132} y2={90} className="ka w3" />
    </S>
  )
}

/** Something failed: a cube cracked apart, the broken piece ghosted in the accent. */
export function ErrorFigure(p: EP) {
  const P = makeIso(62, 44, 21)
  return (
    <S {...p}>
      <Box iso={P} x={0} y={0} w={2.2} d={2.2} h={1.3} />
      <Box iso={P} x={0.2} y={0.2} z={1.8} w={1.8} d={1.8} h={1.0} tone="ghost-accent" />
      <line x1={112} y1={52} x2={140} y2={52} className="k3 dd" />
      <line x1={112} y1={64} x2={132} y2={64} className="k3 dd" />
    </S>
  )
}

/** Name → figure, for `EmptyState art=`. */
export const EMPTY_FIGURES = {
  boards: EmptyBoards,
  queries: EmptyQueries,
  connectors: EmptyConnectors,
  data: EmptyData,
  flows: EmptyFlows,
  watches: EmptyWatches,
  search: EmptySearch,
  error: ErrorFigure,
} as const
export type EmptyFigureName = keyof typeof EMPTY_FIGURES
