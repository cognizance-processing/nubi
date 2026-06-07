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
      <div className="flex items-center justify-center h-full px-5 py-4 text-sm text-muted italic">
        (empty text widget)
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto px-5 py-4 bg-surface text-fg">
      <MarkdownRenderer content={content} />
    </div>
  )
}
