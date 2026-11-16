/**
 * Memo comparator for the editor's widget preview.
 *
 * A preview redraws on any change to its widget except `pos`, so dragging a tile
 * doesn't refetch or repaint it. Comparing everything but `pos`, rather than a
 * list of fields that matter, means a new widget field can't leave the canvas
 * showing stale output while the inspector and the saved spec have moved on.
 */
export function previewPropsEqual(
  prev: { widget: Record<string, any>; variableDefaults?: unknown },
  next: { widget: Record<string, any>; variableDefaults?: unknown },
): boolean {
  if (prev.variableDefaults !== next.variableDefaults) return false
  if (prev.widget === next.widget) return true
  const { pos: _prevPos, ...a } = prev.widget
  const { pos: _nextPos, ...b } = next.widget
  return JSON.stringify(a) === JSON.stringify(b)
}
