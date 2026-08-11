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
 * @template T
 * @param {{ past: T[], present: T, future: T[] }} history
 * @param {T} newState
 * @returns {{ past: T[], present: T, future: T[] }}
 */
export function push(history, newState) {
  if (newState === history.present) return history

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
