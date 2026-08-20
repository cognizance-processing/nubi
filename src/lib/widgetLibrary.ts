/**
 * widgetLibrary.js — API client for reusable LIBRARY WIDGETS.
 *
 * Nubi is canvas-first: a widget normally lives inline in a board's
 * `spec.widgets[]`. The library is the opt-in reuse escape hatch — save a
 * configured widget once, drop copies of it onto any board.
 *
 * This rides the generic org-scoped resource router (backend/app/routes/
 * resources.py) against the long-latent `widgets` table — no bespoke backend:
 *   GET    /widgets              listLibraryWidgets
 *   POST   /widgets              saveWidgetToLibrary
 *   PUT    /widgets/{id}         updateLibraryWidget    ("edit for all" — writes the shared config)
 *   DELETE /widgets/{id}         deleteLibraryWidget
 *   GET    /widgets/{id}/usage   getWidgetUsage          ("used by N boards", backend/app/routes/dashboards.py)
 *
 * A row is { id, org_id, project_id, name, config, created_at, ... } where
 * `config` is the widget shape minus its board-local identity (see
 * toLibraryConfig). Read helpers degrade to a safe value so the palette still
 * renders; writes re-throw so the caller can surface the failure.
 *
 * Adding a library widget to a board now defaults to a REFERENCE (`ref` +
 * sparse `overrides`) rather than a detached copy — fix the shared entry once
 * and every board using it updates. A detached, fully independent copy is
 * still available as an explicit opt-in (see the editor's "Add a detached
 * copy" action) for the cases a linked instance is actively wrong for.
 * The resolver (library config + overrides → effective widget, with
 * board-local fields always forced from the spec entry) lives in
 * `dashboards/widgetLibraryShape.js` as a pure, unit-tested function — see
 * `resolveWidgetRef` there, and `SpecRenderer.jsx` for where it's applied
 * before rendering. A backend-equivalent resolver runs the same rule
 * (`backend/app/dashboards/widget_refs.py`).
 */

import { get, post, put, del } from './api.js'
import {
  toLibraryConfig, fromLibraryRow, resolveWidgetRef, isRefWidget,
  applyWidgetEdit, deepMergeOverrides, diffOverrides,
  flattenOverridePaths, removeOverridePath, humanizeOverridePath,
  BOARD_LOCAL_FIELDS, BOARD_LOCAL_DEFAULTS,
} from '../dashboards/widgetLibraryShape.js'

const BASE = '/widgets'

// The pure shape helpers live in dashboards/widgetLibraryShape.js (importing
// api.js would make them untestable under node --test). Re-exported here so
// callers have a single import for "library widgets".
export {
  toLibraryConfig, fromLibraryRow, resolveWidgetRef, isRefWidget,
  applyWidgetEdit, deepMergeOverrides, diffOverrides,
  flattenOverridePaths, removeOverridePath, humanizeOverridePath,
  BOARD_LOCAL_FIELDS, BOARD_LOCAL_DEFAULTS,
}

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
 * Overwrite a library entry's shared `config` — the "Edit for all boards" path
 * from the "This widget is used by N boards" prompt. Every board with a `ref`
 * to this row picks up the change next time it resolves (each board's own
 * `overrides` still apply on top, so a board that locally overrode `title`
 * keeps its local title even after the shared edit).
 * Re-throws so the caller can surface the failure.
 * @param {string} id
 * @param {object} widget — the edited spec widget (board-local fields are stripped)
 * @returns {Promise<object>} the updated row
 */
export async function updateLibraryWidget(id, widget) {
  return put(`${BASE}/${id}`, { config: toLibraryConfig(widget) })
}

/**
 * Delete a library entry. Boards with a `ref` to it will render a visible
 * broken-reference placeholder (see `resolveWidgetRef`) rather than crash —
 * they are NOT silently deleted or auto-detached. Boards that hold a
 * DETACHED copy (no `ref`) are unaffected, as always.
 * @param {string} id
 */
export async function deleteLibraryWidget(id) {
  return del(`${BASE}/${id}`)
}

/**
 * "Used by N" for a library entry — how many spec widget instances (across
 * boards in the active org) currently `ref` this row, and which boards.
 *
 * GET /widgets/{id}/usage → { widget_id, count, boards: [{ board_id,
 * board_name, widget_ids }] } (backend/app/routes/dashboards.py).
 *
 * Degrades to `null` on ANY failure — network error, the route not being
 * deployed yet, auth hiccup — so callers must treat `null` as "unknown,
 * don't show a count" and never as zero. A resolved `{ count: 0, boards: [] }`
 * is a real answer (a fresh entry no board references yet) and is returned
 * as-is.
 * @param {string} id
 * @returns {Promise<{ count: number, boards: Array<{ board_id: string, board_name: string, widget_ids: string[] }> } | null>}
 */
export async function getWidgetUsage(id) {
  try {
    const data = await get(`${BASE}/${id}/usage`)
    if (typeof data?.count !== 'number') return null
    return { count: data.count, boards: Array.isArray(data.boards) ? data.boards : [] }
  } catch (err) {
    console.warn('[widgetLibrary] usage fetch failed; hiding count:', err.message)
    return null
  }
}
