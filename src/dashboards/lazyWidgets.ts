/**
 * lazyWidgets.ts — which board widgets wait until they are near the screen
 * before they mount (and so before they run their query).
 *
 * Only widgets whose whole job is to show query data are deferred. Filters,
 * text, images, breadcrumbs, steppers and custom-HTML tiles always mount at
 * once: a filter can set a variable on mount (an auto-selected default) that
 * the tables above it depend on, and static tiles cost nothing to draw.
 * The editor canvas never defers anything.
 */

export const LAZY_WIDGET_TYPES: ReadonlySet<string> = new Set(['table', 'chart', 'pivot', 'kpi', 'metric'])

/** Window event that makes every deferred widget mount now (exports, print). */
export const MOUNT_ALL_WIDGETS_EVENT = 'nubi:mount-all-widgets'

/** How far outside the screen a widget starts loading, so it is usually ready by the time you scroll to it. */
export const LAZY_ROOT_MARGIN = '600px 0px'

export function shouldLazyMount(widget: any, editMode: boolean): boolean {
  if (editMode) return false
  if (!widget || typeof widget !== 'object') return false
  if (widget.html) return false
  return LAZY_WIDGET_TYPES.has(widget.type)
}
