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

export default function SectionWidget({ widget }) {
  const props = widget.props ?? {}
  const title = props.title ?? widget.title ?? 'Section'
  const subtitle = props.subtitle ?? ''
  const align = props.align ?? 'left'
  const showDivider = props.divider !== false

  const alignCls = align === 'center' ? 'items-center text-center'
    : align === 'right' ? 'items-end text-right'
    : 'items-start text-left'

  return (
    <div className={`flex flex-col justify-center h-full w-full px-4 py-2 ${alignCls}`}>
      {title && (
        <h3 className="text-lg font-bold font-display text-fg leading-tight truncate w-full">
          {title}
        </h3>
      )}
      {subtitle && (
        <p className="text-xs text-muted mt-0.5 w-full">{subtitle}</p>
      )}
      {showDivider && <div className="mt-2 h-px w-full bg-border" />}
    </div>
  )
}
