/**
 * breakpoints.js — Responsive breakpoint resolution for the headless grid.
 *
 * This is a faithful port of react-grid-layout's `getBreakpointFromWidth` so the
 * dnd-kit / CSS-Grid renderer picks EXACTLY the same breakpoint RGL did for a
 * given container width. Keeping the semantics identical is what guarantees that
 * an existing saved spec renders at the same breakpoint (and therefore the same
 * layout) after the migration.
 *
 * Semantics (mirrors RGL): each breakpoint maps to a MIN-WIDTH threshold. The
 * chosen breakpoint is the one with the LARGEST threshold that is still <= the
 * measured width. If the width is below every threshold we fall back to the
 * smallest breakpoint (so very narrow containers still get a layout).
 */

/**
 * Default breakpoint thresholds (px), matching the values SpecRenderer/editor
 * passed to RGL. Keys are the breakpoint tokens used throughout the spec model.
 */
export const DEFAULT_BREAKPOINTS = { lg: 1200, md: 768, sm: 480 }

/**
 * Resolve the active breakpoint key for a container `width`.
 *
 * @param {Record<string, number>} breakpoints  e.g. { lg: 1200, md: 768, sm: 480 }
 * @param {number} width  measured container width in px
 * @returns {string} the breakpoint key (e.g. 'lg' | 'md' | 'sm')
 *
 * Algorithm (identical to RGL):
 *   1. Sort the breakpoint keys by ascending threshold.
 *   2. Walk from largest to smallest; the first whose threshold <= width wins.
 *   3. If none match (width below all thresholds), return the smallest key.
 */
export function getBreakpointFromWidth(breakpoints, width) {
  // Sort keys ascending by their pixel threshold so index 0 = smallest bp.
  const sorted = sortBreakpoints(breakpoints)
  // Default to the smallest breakpoint (covers the "narrower than everything"
  // case and keeps a sensible result for degenerate inputs).
  let matching = sorted[0]
  for (let i = 1; i < sorted.length; i++) {
    const bp = sorted[i]
    // As soon as the width reaches a larger breakpoint's threshold, adopt it.
    // Because we iterate ascending, the last one we adopt is the largest match.
    if (width >= breakpoints[bp]) matching = bp
  }
  return matching
}

/**
 * Return the breakpoint keys sorted ascending by their pixel threshold.
 * (Exported because the geometry/column logic occasionally needs the ordering.)
 */
export function sortBreakpoints(breakpoints) {
  return Object.keys(breakpoints).sort((a, b) => breakpoints[a] - breakpoints[b])
}
