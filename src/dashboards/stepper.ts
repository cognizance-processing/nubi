/**
 * stepper.js — pure step-transition logic for the `stepper` widget.
 *
 * Kept separate from StepperWidget.jsx so it is unit-testable under the repo's
 * plain `node --test` runner (which cannot load .jsx).
 *
 * A stepper shows one child widget at a time in a single tile — the legacy
 * in-tile drill-down. Clicking a data point in step N sets a filter variable and
 * advances to step N+1, which reads that variable. Stepping BACK must release
 * the variables the abandoned steps set, otherwise the widgets behind the tile
 * stay pinned to a drill-down the user has left.
 */

/** Clamp a requested step index into range. Empty step list => 0. */
export function clampStep(index, stepCount) {
  if (!Number.isFinite(index) || stepCount <= 0) return 0
  return Math.max(0, Math.min(index, stepCount - 1))
}

/**
 * Variables to clear when moving from step `current` to step `target`.
 *
 * Moving forward or staying put clears nothing — the value the click just wrote
 * is exactly what the next step filters on. Moving back releases the variable
 * written by every step from `target` onward.
 *
 * @param   {Array<{widget?: {onClick?: {setVar?: string}}}>} steps
 * @param   {number} current
 * @param   {number} target
 * @returns {string[]} variable names, de-duplicated, in step order.
 */
export function variablesToClear(steps, current, target) {
  if (target >= current) return []
  const names = []
  for (let i = target; i < (steps?.length ?? 0); i += 1) {
    const name = steps[i]?.widget?.onClick?.setVar
    if (name && !names.includes(name)) names.push(name)
  }
  return names
}
