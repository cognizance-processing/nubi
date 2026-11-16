/**
 * events.js — Canonical event-emission helpers for Nubi embed components.
 *
 * All events use { bubbles: true, composed: true } so they pierce shadow DOM
 * boundaries and propagate to host-document listeners.
 *
 * Event constants
 * ---------------
 *  NUBI_RUN    'nubi:run'    — query/metric executed
 *  NUBI_SAVE   'nubi:save'   — user saved a query/metric definition
 *  NUBI_DIRTY  'nubi:dirty'  — unsaved changes present / cleared
 *  NUBI_ERROR  'nubi:error'  — component-level error
 *  NUBI_SELECT 'nubi:select' — row/cell selected in results
 *
 * Emit helpers
 * ------------
 *  emitRun(from, detail)
 *  emitSave(from, detail)
 *  emitDirty(from, detail)
 *  emitError(from, detail)
 *  emitSelect(from, detail)
 */

export const NUBI_RUN    = 'nubi:run'
export const NUBI_SAVE   = 'nubi:save'
export const NUBI_DIRTY  = 'nubi:dirty'
export const NUBI_ERROR  = 'nubi:error'
export const NUBI_SELECT = 'nubi:select'

/**
 * @param {EventTarget} from
 * @param {string} name
 * @param {unknown} detail
 */
function emit(from, name, detail) {
  from.dispatchEvent(new CustomEvent(name, {
    bubbles:   true,
    composed:  true,
    detail,
  }))
}

/**
 * Emit nubi:run — fired when a query or metric is executed.
 * @param {EventTarget} from
 * @param {{ sql?: string, queryId?: string, metricId?: string, params?: unknown }} detail
 */
export function emitRun(from, detail) { emit(from, NUBI_RUN, detail) }

/**
 * Emit nubi:save — fired when the user saves a query/metric definition.
 * @param {EventTarget} from
 * @param {{ queryId?: string, sql?: string, name?: string }} detail
 */
export function emitSave(from, detail) { emit(from, NUBI_SAVE, detail) }

/**
 * Emit nubi:dirty — fired when the editor content diverges from (or matches) the saved state.
 * @param {EventTarget} from
 * @param {{ dirty: boolean }} detail
 */
export function emitDirty(from, detail) { emit(from, NUBI_DIRTY, detail) }

/**
 * Emit nubi:error — fired on any component-level failure.
 * @param {EventTarget} from
 * @param {{ message: string, code?: string }} detail
 */
export function emitError(from, detail) { emit(from, NUBI_ERROR, detail) }

/**
 * Emit nubi:select — fired when a row or cell is selected in a results table.
 * @param {EventTarget} from
 * @param {{ column?: string, value?: unknown, row?: Record<string, unknown> }} detail
 */
export function emitSelect(from, detail) { emit(from, NUBI_SELECT, detail) }
