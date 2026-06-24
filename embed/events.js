/**
 * events.js — Outbound DOM event contract for Nubi web components.
 *
 * All events are dispatched with `bubbles: true, composed: true` so they
 * escape shadow DOM boundaries and can be caught on the host page with a
 * plain `element.addEventListener('nubi:save', handler)`.
 *
 * Event payload shapes
 * --------------------
 *
 * nubi:save
 *   { id: string, spec: object }
 *   Fired when a widget's spec is saved by the user.
 *   `id`   — widget / component id.
 *   `spec` — the full widget spec object.
 *
 * nubi:dirty
 *   { id: string, isDirty: boolean }
 *   Fired when a widget's unsaved-changes state changes.
 *
 * nubi:run
 *   { id: string, query: string }
 *   Fired when a query is executed.
 *   `query` — the SQL string or registered query id that was run.
 *
 * nubi:select
 *   { id: string, rowIndex: number, row: object }
 *   Fired when the user selects a row / data point.
 *   `rowIndex` — 0-based row index in the result set.
 *   `row`      — key-value object of the selected row's columns.
 *
 * nubi:cross-filter
 *   { filterId: string, values: any[] }
 *   Fired when this component broadcasts a cross-filter selection.
 *   `filterId` — dimension / column name being filtered on.
 *   `values`   — array of selected values (empty array = clear filter).
 *
 * nubi:navigate
 *   { href: string, target?: string }
 *   Fired when an internal navigation action is triggered.
 *   `href`   — destination URL or path.
 *   `target` — optional window target (e.g. "_blank").
 *
 * nubi:error
 *   { id: string, error: string, code?: string }
 *   Fired on any non-recoverable error within a component.
 *   `id`    — component / widget id.
 *   `error` — human-readable error message.
 *   `code`  — optional machine-readable error code from the backend.
 */

/**
 * Dispatch a composed, bubbling CustomEvent from `element`.
 *
 * @param {HTMLElement} element
 * @param {string} eventName
 * @param {object} detail
 */
export function emitNubiEvent(element, eventName, detail) {
  element.dispatchEvent(
    new CustomEvent(eventName, {
      bubbles:  true,
      composed: true,
      detail,
    }),
  )
}

/**
 * @param {HTMLElement} el
 * @param {{ id: string, spec: object }} detail
 */
export function emitSave(el, { id, spec }) {
  emitNubiEvent(el, 'nubi:save', { id, spec })
}

/**
 * @param {HTMLElement} el
 * @param {{ id: string, isDirty: boolean }} detail
 */
export function emitDirty(el, { id, isDirty }) {
  emitNubiEvent(el, 'nubi:dirty', { id, isDirty })
}

/**
 * @param {HTMLElement} el
 * @param {{ id: string, query: string }} detail
 */
export function emitRun(el, { id, query }) {
  emitNubiEvent(el, 'nubi:run', { id, query })
}

/**
 * @param {HTMLElement} el
 * @param {{ id: string, rowIndex: number, row: object }} detail
 */
export function emitSelect(el, { id, rowIndex, row }) {
  emitNubiEvent(el, 'nubi:select', { id, rowIndex, row })
}

/**
 * @param {HTMLElement} el
 * @param {{ filterId: string, values: any[] }} detail
 */
export function emitCrossFilter(el, { filterId, values }) {
  emitNubiEvent(el, 'nubi:cross-filter', { filterId, values })
}

/**
 * @param {HTMLElement} el
 * @param {{ href: string, target?: string }} detail
 */
export function emitNavigate(el, { href, target }) {
  emitNubiEvent(el, 'nubi:navigate', { href, target })
}

/**
 * @param {HTMLElement} el
 * @param {{ id: string, error: string, code?: string }} detail
 */
export function emitError(el, { id, error, code }) {
  emitNubiEvent(el, 'nubi:error', { id, error, code })
}
