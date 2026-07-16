/**
 * SectionWidget.jsx — a section header / divider block.
 *
 * widget shape (type === 'section'):
 *   {
 *     id, type: 'section',
 *     props: { title?: string, subtitle?: string, align?: 'left'|'center'|'right', divider?: boolean },
 *     pos
 *   }
 *
 * Purely presentational — no query. Used to break a long dashboard into labelled
 * regions.
 */

import { findKpiIcon } from '../kpiIcons.jsx'

export default function SectionWidget({ widget }) {
  const props = widget.props ?? {}
  const title = props.title ?? widget.title ?? 'Section'
  const subtitle = props.subtitle ?? ''
  const align = props.align ?? 'left'
  const showDivider = props.divider !== false
  const SectionIcon = findKpiIcon(props.icon)

  const alignCls = align === 'center' ? 'items-center text-center'
    : align === 'right' ? 'items-end text-right'
    : 'items-start text-left'

  // A widget-level text color (Appearance → Text color) is applied by the card
  // wrapper as a CSS `color`; the theme-token classes (text-fg / text-muted)
  // would silently override it. Mirror ChartWidget's textColorOverride contract:
  // when a color is forced, inherit it (subtitle at reduced opacity) so section
  // headers stay readable on boards with a style-locked palette.
  const forcedColor = typeof widget.style?.color === 'string' && widget.style.color
  const titleStyle = forcedColor ? { color: 'inherit' } : undefined
  const subtitleStyle = forcedColor ? { color: 'inherit', opacity: 0.65 } : undefined

  const ruleColor = forcedColor
    ? 'color-mix(in srgb, currentColor 18%, transparent)'
    : 'var(--border)'

  return (
    <div className={`flex flex-col justify-center h-full w-full px-1.5 py-1 ${alignCls}`}>
      {/* Title + inline rule on one row — fits a single grid row (h:1) with a
          subtitle, instead of stacking title / subtitle / full-width divider. */}
      <div className={`flex items-center gap-3 w-full ${align === 'right' ? 'flex-row-reverse' : ''}`}>
        {(title || SectionIcon) && (
          <div className={`flex items-center gap-2 shrink-0 max-w-full ${align === 'right' ? 'flex-row-reverse' : ''}`}>
            {SectionIcon && (
              <SectionIcon
                size={19}
                weight="duotone"
                className="shrink-0"
                style={forcedColor ? { color: 'inherit' } : { color: 'var(--primary)' }}
                aria-hidden="true"
              />
            )}
            {title && (
              <h3 className="text-base font-bold font-display text-fg leading-tight truncate tracking-tight max-w-full" style={titleStyle}>
                {title}
              </h3>
            )}
          </div>
        )}
        {showDivider && align !== 'center' && (
          <div className="flex-1 h-px min-w-4" style={{ background: ruleColor }} aria-hidden="true" />
        )}
      </div>
      {subtitle && (
        <p className="text-xs text-muted mt-0.5 w-full leading-snug truncate" style={subtitleStyle}>{subtitle}</p>
      )}
      {showDivider && align === 'center' && (
        <div className="mt-1.5 h-px w-full" style={{ background: ruleColor }} aria-hidden="true" />
      )}
    </div>
  )
}
