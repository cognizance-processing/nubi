/**
 * widgetLibrary.js — API client for reusable LIBRARY WIDGETS.
 *
 * Nubi is canvas-first: a widget normally lives inline in a board's
 * `spec.widgets[]`. The library is the opt-in reuse escape hatch — save a
 * configured widget once, drop copies of it onto any board.
 *
 * This rides the generic org-scoped resource router (backend/app/routes/
 * resources.py) against the long-latent `widgets` table — no bespoke backend:
 *   GET    /widgets        listLibraryWidgets
 *   POST   /widgets        saveWidgetToLibrary
 *   DELETE /widgets/{id}   deleteLibraryWidget
 *
 * A row is { id, org_id, project_id, name, config, created_at, ... } where
 * `config` is the widget shape minus its board-local identity (see
 * toLibraryConfig). Read helpers degrade to a safe value so the palette still
 * renders; writes re-throw so the caller can surface the failure.
 *
 * Copies are DETACHED: adding a library widget to a board inlines a plain copy
 * into the spec with no back-reference, so later edits to the library entry
 * never silently mutate boards already using it. That keeps board specs
 * self-contained (which is what embedding relies on) and avoids the
 * "editing a shared chart changed 12 dashboards" failure mode. Linked/synced
 * instances would be a deliberate opt-in on top, not the default.
 */

import { get, post, del } from './api.js'
import { toLibraryConfig, fromLibraryRow } from '../dashboards/widgetLibraryShape.js'

const BASE = '/widgets'

// The pure shape helpers live in dashboards/widgetLibraryShape.js (importing
// api.js would make them untestable under node --test). Re-exported here so
// callers have a single import for "library widgets".
export { toLibraryConfig, fromLibraryRow }

/**
 * List the library widgets visible to the active org/project.
 * Returns [] on any failure so the palette degrades gracefully.
 * @returns {Promise<Array>}
 */
export async function listLibraryWidgets() {
  try {
    const data = await get(BASE)
    return Array.isArray(data) ? data : []
  } catch (err) {
    console.warn('[widgetLibrary] list failed:', err.message)
    return []
  }
}

/**
 * Save a configured spec widget to the library under `name`.
 * Re-throws so the caller can surface the error.
 * @param {string} name
 * @param {object} widget — a spec widget (board-local fields are stripped)
 * @returns {Promise<object>} the created row
 */
export async function saveWidgetToLibrary(name, widget) {
  return post(BASE, { name, config: toLibraryConfig(widget) })
}

/**
 * Delete a library entry. Boards that already inlined a copy are unaffected —
 * copies are detached by design.
 * @param {string} id
 */
export async function deleteLibraryWidget(id) {
  return del(`${BASE}/${id}`)
}
