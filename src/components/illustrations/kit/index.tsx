/**
 * Figure kit primitives. Every illustration is built from these so the whole
 * set shares stroke weights, tone steps, caption style and one accent.
 *
 *   <FigSvg fig="04" name="Query workspace" title="…">…</FigSvg>
 *   const P = makeIso(ox, oy, s)           // axonometric projection (30°)
 *   <Box iso={P} x y z w d h tone="accent"/>  // 3-face outline cube
 */
import type { ReactNode } from 'react'
import './fig.css'

export type FigProps = {
  className?: string
  /** Pass to make the figure informative (role="img"); omit for decorative art. */
  title?: string
  /** Drop mono labels and the FIG caption (use when rendered under ~240px wide). */
  compact?: boolean
}

type FigSvgProps = {
  /** viewBox width/height. Default 480x300 (drawing area 480x272 + caption strip). */
  w?: number
  h?: number
  /** Caption: `FIG {fig} / {name}`. Omit for empty states. */
  fig?: string
  name?: string
  className?: string
  title?: string
  compact?: boolean
  children: ReactNode
}

/** Root svg + the FIG caption strip. Never sets a height attribute. */
export function FigSvg({ w = 480, h = 300, fig, name, className = '', title, compact, children }: FigSvgProps) {
  const a11y = title ? { role: 'img' as const, 'aria-label': title } : { 'aria-hidden': true as const }
  const caption = fig && !compact
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      xmlns="http://www.w3.org/2000/svg"
      className={`fig ${className}`.trim()}
      preserveAspectRatio="xMidYMid meet"
      focusable="false"
      // inline-size containment (for the small-render label rule) drops the intrinsic ratio, so state it
      style={{ aspectRatio: `${w} / ${h}` }}
      {...a11y}
    >
      {children}
      {caption && (
        <g className="fcap">
          <line x1="0" y1={h - 22.5} x2={w} y2={h - 22.5} className="k3 w1" />
          <text x="0" y={h - 6} className="t1">FIG {fig}</text>
          <text x={w} y={h - 6} textAnchor="end">{name}</text>
        </g>
      )}
    </svg>
  )
}

/* ── axonometric projection ─────────────────────────────────────────── */
export type Iso = (x: number, y: number, z?: number) => [number, number]
const C30 = 0.8660254
/** x runs right-down, y runs left-down, z up. `s` = px per unit. */
export function makeIso(ox: number, oy: number, s = 24): Iso {
  return (x, y, z = 0) => [round(ox + (x - y) * C30 * s), round(oy + (x + y) * 0.5 * s - z * s)]
}
const round = (n: number) => Math.round(n * 100) / 100
const pts = (a: [number, number][]) => a.map(p => p.join(',')).join(' ')

export type Tone = 'paper' | 'accent' | 'ink' | 'ghost' | 'ghost-accent' | 'sunk'

/** Axonometric box: top, left (+y) and right (+x) faces, ink outline. */
export function Box({ iso, x, y, z = 0, w = 1, d = 1, h = 1, tone = 'paper', weight }: {
  iso: Iso; x: number; y: number; z?: number; w?: number; d?: number; h?: number; tone?: Tone; weight?: 1 | 2
}) {
  const top = pts([iso(x, y, z + h), iso(x + w, y, z + h), iso(x + w, y + d, z + h), iso(x, y + d, z + h)])
  const right = pts([iso(x + w, y, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x + w, y, z + h)])
  const left = pts([iso(x, y + d, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x, y + d, z + h)])
  const wc = weight === 2 ? ' w2' : ''
  if (tone === 'ghost' || tone === 'ghost-accent') {
    const c = `${tone === 'ghost' ? 'k2' : 'ka fp'} dd${wc}`
    return <g><polygon points={top} className={c} /><polygon points={right} className={c} /><polygon points={left} className={c} /></g>
  }
  const f = tone === 'accent' ? ['fat', 'fal', 'far'] : tone === 'ink' ? ['fm', 'fi', 'fi'] : tone === 'sunk' ? ['fs', 'fs', 'fs'] : ['fp', 'fl', 'fr']
  return (
    <g>
      <polygon points={left} className={`k1 ${f[1]}${wc}`} />
      <polygon points={right} className={`k1 ${f[2]}${wc}`} />
      <polygon points={top} className={`k1 ${f[0]}${wc}`} />
    </g>
  )
}

/** Flat ground tile (a very low Box). */
export function Slab(p: Omit<Parameters<typeof Box>[0], 'h'> & { h?: number }) {
  return <Box {...p} h={p.h ?? 0.3} />
}

/** A flat line with optional arrow head at the end. `tone`: k1|k2|k3|ka. */
export function Link({ x1, y1, x2, y2, tone = 'k1', head = false, dash = false, weight, lead = false }: {
  x1: number; y1: number; x2: number; y2: number; tone?: 'k1' | 'k2' | 'k3' | 'ka'; head?: boolean; dash?: boolean; weight?: 1 | 2 | 3
  /** A leader line to a label: hidden together with the labels on small renders. */
  lead?: boolean
}) {
  const c = `${tone}${dash ? ' dd' : ''}${weight ? ` w${weight}` : ''}${lead ? ' lead' : ''}`
  let hd = null
  if (head) {
    const a = Math.atan2(y2 - y1, x2 - x1), L = 7, sp = 0.5
    const p1 = [x2 - L * Math.cos(a - sp), y2 - L * Math.sin(a - sp)], p2 = [x2 - L * Math.cos(a + sp), y2 - L * Math.sin(a + sp)]
    hd = <polyline points={`${round(p1[0])},${round(p1[1])} ${x2},${y2} ${round(p2[0])},${round(p2[1])}`} className={c.replace(' dd', '')} />
  }
  return <g><line x1={x1} y1={y1} x2={x2} y2={y2} className={c} />{hd}</g>
}

/** Orthogonal/straight route through `points` with an arrow head on the last segment. */
export function Route({ points, tone = 'k1', dash = false, head = true, weight }: {
  points: [number, number][]; tone?: 'k1' | 'k2' | 'k3' | 'ka'; dash?: boolean; head?: boolean; weight?: 1 | 2 | 3
}) {
  const c = `${tone}${weight ? ` w${weight}` : ''}`
  const [bx, by] = points[points.length - 2], [ex, ey] = points[points.length - 1]
  const a = Math.atan2(ey - by, ex - bx), L = 7, sp = 0.5
  const h1 = [ex - L * Math.cos(a - sp), ey - L * Math.sin(a - sp)], h2 = [ex - L * Math.cos(a + sp), ey - L * Math.sin(a + sp)]
  return (
    <g>
      <polyline points={pts(points)} className={c + (dash ? ' dd' : '')} />
      {head && <polyline points={`${round(h1[0])},${round(h1[1])} ${ex},${ey} ${round(h2[0])},${round(h2[1])}`} className={c} />}
    </g>
  )
}

/** Mono label. `tone`: mid (default) | ink | accent. */
export function Label({ x, y, children, anchor = 'start', tone = 'mid', code = false, big = false, keep = false }: {
  x: number; y: number; children: ReactNode; anchor?: 'start' | 'middle' | 'end'; tone?: 'mid' | 'ink' | 'accent'; code?: boolean; big?: boolean
  /** Survives the small-render fallback (re-sized to stay legible): use for the few labels a chart can't be read without. */
  keep?: boolean
}) {
  const c = [tone === 'ink' ? 't1' : tone === 'accent' ? 'ta' : '', code ? 'lc' : '', big ? 'big' : '', keep ? 'keep' : ''].filter(Boolean).join(' ')
  return <text x={x} y={y} textAnchor={anchor} className={c || undefined}>{children}</text>
}

/** Square status dot (never round). */
export function Dot({ x, y, s = 6, tone = 'ink' }: { x: number; y: number; s?: number; tone?: 'ink' | 'accent' | 'mid' }) {
  return <rect x={x - s / 2} y={y - s / 2} width={s} height={s} className={`ns ${tone === 'accent' ? 'fa' : tone === 'mid' ? 'fm' : 'fi'}`} />
}

/** Browser/app window chrome: 2px-radius frame, title bar rule, three squares. */
export function Window({ x, y, w, h, bar = 16, tone = 'k1', fill = 'fp', children }: {
  x: number; y: number; w: number; h: number; bar?: number; tone?: 'k1' | 'k2'; fill?: string; children?: ReactNode
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx="2" className={`${tone} ${fill}`} />
      <line x1={x} y1={y + bar} x2={x + w} y2={y + bar} className={tone} />
      {[0, 1, 2].map(i => <rect key={i} x={x + 6 + i * 8} y={y + bar / 2 - 2} width="4" height="4" className="ns fm" />)}
      {children}
    </g>
  )
}

/** Flat cylinder (database). */
export function Cylinder({ x, y, w, h, tone = 'k1', fill = 'fp', rings = 0 }: { x: number; y: number; w: number; h: number; tone?: 'k1' | 'k2' | 'ka'; fill?: string; rings?: number }) {
  const ry = Math.min(7, w / 5), rx = w / 2
  const dash = tone === 'k2' ? ' dd' : ''
  return (
    <g>
      <path d={`M${x},${y + ry} v${h - 2 * ry} a${rx},${ry} 0 0 0 ${w},0 v${-(h - 2 * ry)}`} className={`${tone} ${fill}${dash}`} />
      <ellipse cx={x + rx} cy={y + ry} rx={rx} ry={ry} className={`${tone} ${fill}${dash}`} />
      {Array.from({ length: rings }).map((_, i) => (
        <path key={i} d={`M${x},${y + ry + ((i + 1) * (h - 2 * ry)) / (rings + 1)} a${rx},${ry} 0 0 0 ${w},0`} className={`${tone === 'ka' ? 'ka' : 'k3'}`} />
      ))}
    </g>
  )
}

/** Deterministic pseudo-random (stable across renders and SSR). */
export function seeded(seed = 7) {
  let s = seed >>> 0
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296)
}

/** Polyline points string from [x,y] pairs. */
export const poly = pts
