/**
 * TextWidget.jsx — Spec-driven markdown text widget for the SpecRenderer.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'text'.
 *                   Shape: { id, type: 'text', props: { content: string } }
 *
 * Behaviour
 * ---------
 * - Renders the `props.content` field as markdown using the project's existing
 *   MarkdownRenderer component (no new dependency required).
 * - If content is empty / missing, renders a subtle placeholder so the widget
 *   slot is still visible in the grid.
 * - Styling: scrollable overflow, consistent padding, matches the surface/border
 *   conventions used across KpiWidget and the SpecRenderer wrapper.
 */

import MarkdownRenderer from '../../components/MarkdownRenderer.jsx'

export default function TextWidget({ widget }) {
  const { props: wProps = {} } = widget
  const content = wProps.content ?? ''

  if (!content.trim()) {
    return (
      <div className="flex items-center justify-center h-full px-5 py-4 text-sm text-muted italic opacity-60">
        (empty text widget)
      </div>
    )
  }

  // The grid cell already carries widget.style (including an author-set
  // `color`), so an explicit colour must be allowed to win — the blanket
  // `text-fg` class silently overrode it, which turned coloured section
  // headings on migrated boards into plain theme-foreground text.
  const inheritsColor = typeof widget.style?.color === 'string' && widget.style.color

  // MarkdownRenderer's own elements carry `text-fg`, which beats plain
  // inheritance, so an explicit colour additionally forces descendants to
  // inherit. Without an author colour nothing changes.
  // Markdown blocks carry their own vertical margins (`my-4` on a paragraph),
  // which on top of this padding pushed a ONE-LINE heading past a short grid
  // cell and produced a scrollbar on every section title. Collapse the leading
  // and trailing margins — the same reset used elsewhere in MarkdownRenderer —
  // at both nesting levels, since the renderer wraps its output in an element.
  const marginReset =
    '[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 ' +
    '[&>*>*:first-child]:mt-0 [&>*>*:last-child]:mb-0'

  return (
    <div
      className={
        `h-full overflow-y-auto px-5 py-3 prose-sm flex flex-col justify-center ${marginReset}` +
        (inheritsColor ? ' [&_*]:text-inherit' : ' text-fg')
      }
    >
      <MarkdownRenderer content={content} />
    </div>
  )
}
