/**
 * DividerWidget.jsx — a standalone rule used to carve a board into regions.
 *
 * widget shape (type === 'divider'):
 *   {
 *     id, type: 'divider',
 *     props: {
 *       orientation?: 'horizontal' | 'vertical',  // default 'horizontal'
 *       thickness?: number,   // line weight in px, default 1
 *       color?: string,       // any CSS colour or theme token; default var(--border)
 *       lineStyle?: 'solid' | 'dashed' | 'dotted',  // default 'solid'
 *       inset?: number,       // px of empty space at BOTH ends of the line, default 0
 *       opacity?: number,     // 0..1, default 1
 *     },
 *     pos
 *   }
 *
 * Distinct from `section`, which is a *labelled* heading that happens to draw a
 * rule: this is the rule on its own, which is what the legacy estate uses it
 * for — a horizontal separator under a header strip, or a vertical hairline
 * between two columns of KPI tiles. Purely presentational; no query.
 *
 * The line is centred in its tile and stretches along the tile's long axis, so
 * resizing the widget in the grid is how you set its length.
 */

const LINE_STYLES = new Set(['solid', 'dashed', 'dotted'])

export default function DividerWidget({ widget }) {
  const props = widget.props ?? {}
  const vertical = props.orientation === 'vertical'
  // A 0/negative/NaN thickness would render nothing at all, which reads as a
  // broken widget rather than a deliberate choice — floor it at a hairline.
  const thickness = Math.max(1, Number(props.thickness) || 1)
  const lineStyle = LINE_STYLES.has(props.lineStyle) ? props.lineStyle : 'solid'
  const inset = Math.max(0, Number(props.inset) || 0)
  const opacity = props.opacity == null ? 1 : Math.min(1, Math.max(0, Number(props.opacity)))

  // A widget-level text colour (Appearance → Text colour) is applied by the
  // card wrapper as a CSS `color`, so `currentColor` lets a style preset tint
  // the rule without the author touching the widget. props.color wins when set.
  const color = props.color || 'var(--border)'

  // `dashed`/`dotted` need a real border to render the gaps — a background fill
  // can only ever be solid.
  const line = vertical
    ? {
        height: `calc(100% - ${inset * 2}px)`,
        borderLeftWidth: `${thickness}px`,
        borderLeftStyle: lineStyle,
        borderLeftColor: color,
      }
    : {
        width: `calc(100% - ${inset * 2}px)`,
        borderTopWidth: `${thickness}px`,
        borderTopStyle: lineStyle,
        borderTopColor: color,
      }

  // `separator` (not aria-hidden): a rule placed deliberately between two
  // regions of a board is structure, and announcing it is the point — the same
  // reason `<hr>` is not decorative.
  return (
    <div className="flex items-center justify-center h-full w-full">
      <div
        style={{ ...line, opacity }}
        role="separator"
        aria-orientation={vertical ? 'vertical' : 'horizontal'}
      />
    </div>
  )
}
