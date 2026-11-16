/**
 * history.js — Pure undo/redo history model for the dashboard spec.
 *
 * Model: { past: T[], present: T, future: T[] }
 *
 * History cap: HISTORY_LIMIT = 200 entries.
 * When past.length exceeds HISTORY_LIMIT, the oldest entry is dropped (shift).
 * This bounds memory to at most HISTORY_LIMIT + 1 states (past + present).
 *
 * All functions are pure (no mutation of input) and return a new history object.
 * Extract this module so it is independently unit-testable via node:test.
 */

/** Maximum number of past snapshots retained. Oldest are dropped beyond this cap. */
export const HISTORY_LIMIT = 200

/**
 * Create an initial history from a starting state.
 * @template T
 * @param {T} initialState
 * @returns {{ past: T[], present: T, future: T[] }}
 */
export function createHistory(initialState) {
  return { past: [], present: initialState, future: [] }
}

/**
 * Push a new state onto the history.
 * - Moves current present into past (capped at HISTORY_LIMIT; oldest dropped if over).
 * - Sets present to the new state.
 * - Clears future (a new branch invalidates redo).
 *
 * If newState is strictly equal (===) to present, returns the same history (no-op).
 *
 * `coalesce` REPLACES the present instead of appending to the past — for a
 * control that emits a stream of values for one user decision (dragging in the
 * colour spectrum, scrubbing a slider). Without it a single drag leaves dozens
 * of undo steps and Ctrl+Z walks back through it one shade at a time; with it
 * the whole gesture is one step. The FIRST commit of a gesture must still be a
 * normal push, or there would be no entry to step back to — callers open the
 * gesture, push once, then coalesce for the rest (see LiveEditProvider).
 *
 * @template T
 * @param {{ past: T[], present: T, future: T[] }} history
 * @param {T} newState
 * @param {{ coalesce?: boolean }} [options]
 * @returns {{ past: T[], present: T, future: T[] }}
 */
export function push(history, newState, options: { coalesce?: boolean } = {}) {
  if (newState === history.present) return history

  if (options.coalesce && history.past.length > 0) {
    // Same past, new present. `future` is still cleared: the gesture is a new
    // branch however many values it emitted along the way.
    return { past: history.past, present: newState, future: [] }
  }

  let nextPast = [...history.past, history.present]
  // Enforce cap: drop oldest entries beyond HISTORY_LIMIT
  if (nextPast.length > HISTORY_LIMIT) {
    nextPast = nextPast.slice(nextPast.length - HISTORY_LIMIT)
  }

  return {
    past: nextPast,
    present: newState,
    future: [],
  }
}

/**
 * Undo: step back one state.
 * If past is empty, returns the same history (no-op).
 *
 * @template T
 * @param {{ past: T[], present: T, future: T[] }} history
 * @returns {{ past: T[], present: T, future: T[] }}
 */
export function undo(history) {
  if (history.past.length === 0) return history

  const previous = history.past[history.past.length - 1]
  const newPast = history.past.slice(0, -1)

  return {
    past: newPast,
    present: previous,
    future: [history.present, ...history.future],
  }
}

/**
 * Redo: step forward one state.
 * If future is empty, returns the same history (no-op).
 *
 * @template T
 * @param {{ past: T[], present: T, future: T[] }} history
 * @returns {{ past: T[], present: T, future: T[] }}
 */
export function redo(history) {
  if (history.future.length === 0) return history

  const next = history.future[0]
  const newFuture = history.future.slice(1)

  return {
    past: [...history.past, history.present],
    present: next,
    future: newFuture,
  }
}

/**
 * Whether undo is available.
 * @param {{ past: any[] }} history
 * @returns {boolean}
 */
export function canUndo(history) {
  return history.past.length > 0
}

/**
 * Whether redo is available.
 * @param {{ future: any[] }} history
 * @returns {boolean}
 */
export function canRedo(history) {
  return history.future.length > 0
}
