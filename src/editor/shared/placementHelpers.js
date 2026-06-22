/**
 * shared/placementHelpers.js — Pure helper functions for widget placement.
 * Extracted into a .js file so they can be imported by node:test without
 * requiring JSX / babel transforms.
 *
 * Exports:
 *   effectivePlacement(widget) → 'grid' | 'header' | 'drawer'
 *   applyPlacement(widget, placement) → widget
 */

/**
 * Effective placement for a widget: 'grid' | 'header' | 'drawer'.
 * SHARED CONTRACT (mirrors SpecRenderer.effectivePlacement + backend):
 *   - explicit widget.placement wins;
 *   - else widget.drawer === true → 'drawer' (legacy flag);
 *   - else 'grid' (default).
 */
export function effectivePlacement(w) {
  if (w?.placement) return w.placement
  if (w?.drawer === true) return 'drawer'
  return 'grid'
}

/**
 * Apply a placement choice to a widget, keeping the legacy drawer flag/group in
 * sync (drawer → drawer=true + drawer_group='filters'; grid/header → drawer=false).
 */
export function applyPlacement(widget, placement) {
  const next = { ...widget, placement }
  if (placement === 'drawer') {
    next.drawer = true
    if (!next.drawer_group) next.drawer_group = 'filters'
  } else {
    next.drawer = false
  }
  return next
}
