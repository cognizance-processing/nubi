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
import { resolveTextColors } from '../textColors.js'

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

  // Per-role text colours. The theme-token classes (text-fg / text-muted) would
  // silently win over a colour inherited from the card wrapper, so each role is
  // applied explicitly here. The subtitle is the `muted` role, which means it
  // can now be coloured independently of the title instead of always being the
  // title at 65% opacity.
  const text = resolveTextColors(widget.style)
  const titleStyle = text.title ? { color: text.title } : undefined
  const subtitleStyle = text.muted ? { color: text.muted } : undefined

  const ruleColor = (text.title || text.body)
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
                style={{ color: text.title || 'var(--primary)' }}
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
