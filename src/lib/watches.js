/**
 * watches.js — API client for WATCHES (proactive metric alerts).
 *
 * A *watch* monitors a single governed metric and fires when a threshold (or a
 * change-over-time rule) is breached: it composes an AI explanation and
 * dispatches it to a notify channel (Slack). This module is the thin transport
 * layer in front of the backend routes; the UI lives in
 * ``src/pages/app/WatchesPage.jsx``.
 *
 * All read helpers degrade gracefully (catch transport/auth errors and return a
 * safe empty value) so the page can still render; write/evaluate helpers
 * re-throw so the form can surface the error.
 *
 * Endpoints mirror backend/app/routes/watches.py (paths under /api/v1):
 *   GET    /watches                  listWatches
 *   GET    /watches/{id}             getWatch
 *   POST   /watches                  createWatch
 *   PUT    /watches/{id}             updateWatch
 *   DELETE /watches/{id}             deleteWatch
 *   POST   /watches/{id}/evaluate    evaluateWatch
 *
 * Watch shape (the persisted record):
 *   {
 *     id,            // canonical id / slug
 *     name,
 *     metric_id,     // the governed metric this watch monitors
 *     config: {
 *       dimensions: string[],
 *       time_grain: string | null,
 *       // exactly one breach rule:
 *       threshold:  { op: '<'|'<='|'>'|'>='|'==', value: number },
 *       comparison: { kind: 'change_pct', vs: 'previous_period', op, value },
 *       channel_config: { slack_webhook?: string, slack_channel?: string },
 *       enabled: boolean,
 *     }
 *   }
 */

import { get, post, put, del } from './api.js'

const BASE = '/watches'

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

/**
 * List all watches visible to the active org/project.
 * @returns {Promise<Array<object>>}  [] on any failure.
 */
export async function listWatches() {
  try {
    const data = await get(BASE)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.watches)) return data.watches
    return []
  } catch (err) {
    console.warn('[watches] listWatches failed:', err.message)
    return []
  }
}

/**
 * Get a single watch by id (full record).
 * @param {string} id
 * @returns {Promise<object|null>}  null on any failure.
 */
export async function getWatch(id) {
  try {
    return await get(`${BASE}/${id}`)
  } catch (err) {
    console.warn('[watches] getWatch failed:', err.message)
    return null
  }
}

/**
 * Create a watch. Re-throws on failure so the form can show the error.
 * @param {{ name: string, metric_id: string, config?: object }} watch
 * @returns {Promise<object>}  the created watch record.
 */
export async function createWatch(watch) {
  return post(BASE, watch)
}

/**
 * Update an existing watch. Re-throws on failure.
 * @param {string} id
 * @param {{ name?: string, metric_id?: string, config?: object }} watch
 * @returns {Promise<object>}  the updated watch record.
 */
export async function updateWatch(id, watch) {
  return put(`${BASE}/${id}`, watch)
}

/**
 * Delete a watch.
 * @param {string} id
 * @returns {Promise<boolean>}  true on success, false on failure.
 */
export async function deleteWatch(id) {
  try {
    await del(`${BASE}/${id}`)
    return true
  } catch (err) {
    console.warn('[watches] deleteWatch failed:', err.message)
    return false
  }
}

// ---------------------------------------------------------------------------
// Evaluate now
// ---------------------------------------------------------------------------

/**
 * Evaluate a watch NOW and return the run summary. Re-throws on failure so the
 * caller can surface it.
 * @param {string} id
 * @returns {Promise<{
 *   id: string,
 *   breached: boolean,
 *   value: number|null,
 *   state: 'ok'|'breached'|'error',
 *   explanation?: string,
 *   sent: number,
 *   result?: object,
 *   error?: string,
 * }>}
 */
export async function evaluateWatch(id) {
  return post(`${BASE}/${id}/evaluate`, {})
}

// ---------------------------------------------------------------------------
// Metrics (for the metric_id picker) — there is no metrics.js yet, so fetch
// GET /metrics directly via the shared api helper.
// ---------------------------------------------------------------------------

/**
 * List the governed metrics available for a watch's metric_id picker.
 * Each summary carries { id, name, measure, dimensions[], time_grains[] }.
 * @returns {Promise<Array<object>>}  [] on any failure.
 */
export async function listMetrics() {
  try {
    const data = await get('/metrics')
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.metrics)) return data.metrics
    return []
  } catch (err) {
    console.warn('[watches] listMetrics failed:', err.message)
    return []
  }
}
