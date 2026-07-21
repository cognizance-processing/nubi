/**
 * BoardMiniature.jsx — a dashboard card's "window into the dashboard".
 *
 * Draws the board's REAL layout: every on-canvas widget of the first tab, at its
 * actual grid position, as a glyph matching its type. The layout model comes
 * from the pure `buildMiniature` (dashboards/specMiniature.js); this file owns
 * only the projection into an SVG viewBox and the glass presentation.
 *
 * Rendering approach — one inline SVG, no data, no queries. See the header of
 * specMiniature.js for why a live <SpecRenderer> per card is not viable.
 *
 * Theming: every fill/stroke derives from `currentColor` (the caller sets a
 * text colour) or from CSS theme tokens, never a hard-coded hex — so the
 * miniature is correct in light and dark with no per-theme branch. The one
 * exception is the per-board gradient tint, which is intentionally a fixed
 * brand ramp (it's the board's identity colour, the same in both themes).
 */

import { useMemo } from 'react'
import { buildMiniature } from '../../dashboards/specMiniature.js'

// The drawable box. Width is normalised to 100 units; height follows from the
// board's real geometry (cols + row_height — see buildMiniature's unitHeight),
// so a squat board reads squat and a tall one reads tall. Unitless.
const VIEW_W = 100

/** Padding INSIDE each widget rect, in viewBox units, so glyphs don't touch the edge. */
const PAD = 1.1

/**
 * Glyph for one widget. `x/y/w/h` are the widget's pixel-ish viewBox rect.
 *
 * Each glyph is drawn to be recognisable at ~6px tall, which is the real
 * constraint here — detail is wasted, silhouette is everything. Opacity does
 * the visual hierarchy: the widget's surface is faint, its "ink" is stronger.
 */
function Glyph({ kind, x, y, w, h }) {
  const ix = x + PAD
  const iy = y + PAD
  const iw = Math.max(0.5, w - PAD * 2)
  const ih = Math.max(0.5, h - PAD * 2)
  const ink = { fill: 'currentColor' }

  switch (kind) {
    case 'kpi': {
      // A short label rule above a heavy value bar — the KPI silhouette.
      const labelH = Math.min(0.9, ih * 0.16)
      const valueH = Math.min(2.6, ih * 0.42)
      return (
        <>
          <rect x={ix} y={iy + ih * 0.16} width={iw * 0.42} height={labelH} rx={labelH / 2} {...ink} opacity="0.35" />
          <rect x={ix} y={iy + ih * 0.16 + labelH + ih * 0.12} width={iw * 0.66} height={valueH} rx={0.5} {...ink} opacity="0.75" />
        </>
      )
    }

    case 'bars': {
      const n = Math.max(3, Math.min(7, Math.round(iw / 3)))
      const gap = iw / n * 0.32
      const bw = (iw - gap * (n - 1)) / n
      // Deterministic heights — a thumbnail must not shimmer between renders.
      const ramp = [0.45, 0.72, 0.38, 0.9, 0.6, 0.8, 0.5]
      return ramp.slice(0, n).map((f, i) => (
        <rect
          key={i}
          x={ix + i * (bw + gap)}
          y={iy + ih * (1 - f)}
          width={bw}
          height={ih * f}
          rx={Math.min(0.4, bw / 3)}
          {...ink}
          opacity="0.6"
        />
      ))
    }

    case 'line':
    case 'area': {
      const pts = [0.62, 0.3, 0.5, 0.16, 0.42, 0.08]
      const step = iw / (pts.length - 1)
      const coords = pts.map((f, i) => [ix + i * step, iy + ih * f])
      const d = coords.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(2)},${py.toFixed(2)}`).join('')
      return (
        <>
          {kind === 'area' && (
            <path
              d={`${d}L${(ix + iw).toFixed(2)},${(iy + ih).toFixed(2)}L${ix.toFixed(2)},${(iy + ih).toFixed(2)}Z`}
              fill="currentColor"
              opacity="0.22"
            />
          )}
          <path
            d={d}
            fill="none"
            stroke="currentColor"
            strokeWidth="0.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity="0.75"
          />
        </>
      )
    }

    case 'points': {
      const dots = [[0.14, 0.7], [0.32, 0.42], [0.48, 0.6], [0.62, 0.26], [0.8, 0.46], [0.9, 0.72]]
      return dots.map(([fx, fy], i) => (
        <circle key={i} cx={ix + iw * fx} cy={iy + ih * fy} r={Math.min(0.7, ih * 0.09)} {...ink} opacity="0.6" />
      ))
    }

    case 'circle': {
      const r = Math.min(iw, ih) / 2
      const cx = ix + iw / 2
      const cy = iy + ih / 2
      return (
        <>
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="currentColor" strokeWidth={Math.max(0.5, r * 0.42)} opacity="0.28" />
          {/* An arc of a stronger stroke reads as "a slice" — the donut tell. */}
          <path
            d={`M${cx.toFixed(2)},${(cy - r).toFixed(2)} A${r.toFixed(2)},${r.toFixed(2)} 0 0 1 ${(cx + r).toFixed(2)},${cy.toFixed(2)}`}
            fill="none"
            stroke="currentColor"
            strokeWidth={Math.max(0.5, r * 0.42)}
            opacity="0.75"
          />
        </>
      )
    }

    case 'table': {
      const rowH = 0.85
      const gap = Math.max(0.5, (ih - rowH) / Math.max(1, Math.floor(ih / 1.7)) - rowH)
      const rows = Math.max(1, Math.min(6, Math.floor(ih / (rowH + gap))))
      return Array.from({ length: rows }, (_, i) => (
        <rect
          key={i}
          x={ix}
          y={iy + i * (rowH + gap)}
          width={i === 0 ? iw : iw * (i % 2 ? 0.82 : 0.94)}
          height={rowH}
          rx={rowH / 2}
          {...ink}
          opacity={i === 0 ? 0.6 : 0.28}
        />
      ))
    }

    case 'filter': {
      const ph = Math.min(ih, 2.2)
      return <rect x={ix} y={iy + (ih - ph) / 2} width={iw} height={ph} rx={ph / 2} fill="none" stroke="currentColor" strokeWidth="0.4" opacity="0.5" />
    }

    case 'heading': {
      const barH = Math.min(1.2, ih * 0.5)
      return (
        <>
          <rect x={ix} y={iy} width={iw * 0.34} height={barH} rx={barH / 2} {...ink} opacity="0.7" />
          <rect x={ix} y={iy + barH + 0.5} width={iw} height={0.25} {...ink} opacity="0.2" />
        </>
      )
    }

    case 'text':
    default: {
      const lineH = 0.6
      const gap = 0.75
      const lines = Math.max(1, Math.min(4, Math.floor(ih / (lineH + gap))))
      const widths = [1, 0.86, 0.94, 0.6]
      return Array.from({ length: lines }, (_, i) => (
        <rect key={i} x={ix} y={iy + i * (lineH + gap)} width={iw * widths[i % widths.length]} height={lineH} rx={lineH / 2} {...ink} opacity="0.3" />
      ))
    }
  }
}

/**
 * Renders the miniature only — the glass pane and the board's identity tint are
 * the caller's business (see CardThumbnail in DashboardsPage), so this stays a
 * pure drawing of the layout and can sit on any background.
 *
 * @param {{
 *   spec?: object|null,
 *   className?: string,
 *   maxRows?: number,
 * }} props
 */
export default function BoardMiniature({ spec, className = '', maxRows = 14 }) {
  const model = useMemo(() => buildMiniature(spec, { maxRows }), [spec, maxRows])

  // No truthful layout to draw → tell the caller so it can fall back to an icon.
  if (!model) return null

  const { unitWidth, unitHeight, rows, items, header, truncated } = model
  const headerH = header.length > 0 ? unitHeight * 1.2 : 0
  const viewH = rows * unitHeight + headerH
  const fadeId = `bm-fade-${rows}-${items.length}`

  return (
    <svg
      className={className}
      viewBox={`0 0 ${VIEW_W} ${viewH}`}
      // 'meet' (fit, don't crop): the point of the card is the WHOLE silhouette.
      // 'slice' cropped every board to its top rows, which made a section header
      // + KPI strip — the way most boards open — look identical across cards.
      // Anchored top (xMidYMin) so a truncated tall board shows its head.
      preserveAspectRatio="xMidYMin meet"
      aria-hidden="true"
      focusable="false"
    >
      {truncated && (
        <defs>
          <linearGradient id={fadeId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--surface)" stopOpacity="0" />
            <stop offset="100%" stopColor="var(--surface)" stopOpacity="0.85" />
          </linearGradient>
        </defs>
      )}

      {/* Header filter strip — drawn where it actually appears: above the grid. */}
      {header.length > 0 && (
        <g color="var(--fg)">
          {header.slice(0, 4).map((h, i) => {
            const pw = VIEW_W / Math.min(4, header.length) - 2
            return (
              <rect
                key={i}
                x={1 + i * (pw + 2)}
                y={headerH * 0.22}
                width={pw}
                height={headerH * 0.5}
                rx={headerH * 0.25}
                fill="none"
                stroke="currentColor"
                strokeWidth="0.4"
                opacity="0.45"
              />
            )
          })}
        </g>
      )}

      {items.map((it, i) => {
        const x = it.x * unitWidth
        const y = headerH + it.y * unitHeight
        const w = it.w * unitWidth
        const h = it.h * unitHeight
        return (
          // Keyed by index, NOT by widget id: real board specs in the wild do
          // contain repeated widget ids (a legacy authoring quirk), and this is
          // a static drawing with no reconciliation to preserve, so position is
          // the honest identity here.
          <g key={i}>
            {/* The widget's card: a faint surface so the grid rhythm is legible. */}
            <rect
              x={x + 0.5}
              y={y + 0.5}
              width={Math.max(0, w - 1)}
              height={Math.max(0, h - 1)}
              rx="1.2"
              fill="var(--surface)"
              stroke="var(--border)"
              strokeWidth="0.3"
              opacity="0.9"
            />
            <g color="var(--fg)">
              <Glyph kind={it.kind} x={x + 0.5} y={y + 0.5} w={Math.max(0, w - 1)} h={Math.max(0, h - 1)} />
            </g>
          </g>
        )
      })}

      {/* The board continues past the row clamp — fade the cut so the miniature
          reads as "there's more below", not as a board that simply ends here. */}
      {truncated && (
        <rect x="0" y={viewH - unitHeight * 2} width={VIEW_W} height={unitHeight * 2} fill={`url(#${fadeId})`} />
      )}
    </svg>
  )
}

export { BoardMiniature }
