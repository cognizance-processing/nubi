/**
 * metrics.js — API client for the governed METRICS / semantic layer.
 *
 * Mirrors flows.js conventions: reuse the shared api.js helper (base URL, auth
 * Bearer token, X-Org-Id / X-Project-Id headers, silent 401 refresh). The CRUD
 * helpers degrade gracefully (catch + return a safe value) so the UI can still
 * render; the write helpers re-throw so the form can surface validation errors.
 *
 * Endpoints mirror backend/app/routes/metrics.py:
 *   GET    /metrics                  listMetrics
 *   GET    /metrics/{id}             getMetric
 *   POST   /metrics                  createMetric
 *   PUT    /metrics/{id}             updateMetric
 *   DELETE /metrics/{id}             deleteMetric
 *   POST   /metrics/{id}/sql         compileMetricSql  (dry compile → {sql, params})
 *   POST   /metrics/{id}/query       (Arrow) — run via runMetricQuery (metricRuntime.js)
 *
 * A MetricDefinition body mirrors MetricDefinition.to_dict:
 *   { id, name, measure:{name, agg, expr, type, format},
 *     base_table|base_sql, datastore_id,
 *     dimensions:[{name, expr, type}],
 *     time_dimension:{column, grains[], default_grain},
 *     default_filters[], rls_keys[], description, owner }
 */

import { get, post, put, del } from './api.js'

const BASE = '/metrics'

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

/**
 * List the metrics visible to the active org/project.
 * Each row is the compact summary shape from the backend:
 *   { id, name, measure:{name,agg,expr,type,format}, dimensions:[name],
 *     time_grains:[grain], description }
 * Returns [] on any failure so the page degrades gracefully.
 * @returns {Promise<Array>}
 */
export async function listMetrics() {
  try {
    const data = await get(BASE)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.metrics)) return data.metrics
    return []
  } catch (err) {
    console.warn('[metrics] listMetrics failed:', err.message)
    return []
  }
}

/**
 * Get a single metric's FULL serialized definition (not the list summary).
 * @param {string} id
 * @returns {Promise<object|null>}
 */
export async function getMetric(id) {
  try {
    return await get(`${BASE}/${encodeURIComponent(id)}`)
  } catch (err) {
    console.warn('[metrics] getMetric failed:', err.message)
    return null
  }
}

/**
 * Create (register) a metric. The body is a serialized MetricDefinition.
 * Re-throws on failure so the form can surface the structured 400 message.
 * @param {object} def
 * @returns {Promise<object>}  the created (canonical) definition
 */
export function createMetric(def) {
  return post(BASE, def)
}

/**
 * Update an existing metric definition (re-validate + re-register + persist).
 * Re-throws on failure so the form can surface the error.
 * @param {string} id
 * @param {object} def
 * @returns {Promise<object>}
 */
export function updateMetric(id, def) {
  return put(`${BASE}/${encodeURIComponent(id)}`, def)
}

/**
 * Delete (unregister) a metric.
 * @param {string} id
 * @returns {Promise<boolean>}
 */
export async function deleteMetric(id) {
  try {
    await del(`${BASE}/${encodeURIComponent(id)}`)
    return true
  } catch (err) {
    console.warn('[metrics] deleteMetric failed:', err.message)
    return false
  }
}

// ---------------------------------------------------------------------------
// Dry compile (introspection / SQL preview)
// ---------------------------------------------------------------------------

/**
 * Dry-compile a metric query to ``{ sql, params }`` WITHOUT executing it.
 *
 * POST /metrics/{id}/sql
 * body = { dimensions[], time_grain, filters:[{field,op,value}], limit }
 *
 * Re-throws on failure (governance violations are a structured 400) so the
 * caller can surface the message.
 *
 * @param {string} id
 * @param {{ dimensions?: string[], time_grain?: string|null,
 *   filters?: Array<{field:string,op:string,value:any}>, limit?: number }} [query]
 * @returns {Promise<{ sql: string, params: Record<string, any> }>}
 */
export function compileMetricSql(id, query = {}) {
  return post(`${BASE}/${encodeURIComponent(id)}/sql`, {
    dimensions: Array.isArray(query.dimensions) ? query.dimensions : [],
    time_grain: query.time_grain ?? null,
    filters: Array.isArray(query.filters) ? query.filters : [],
    ...(typeof query.limit === 'number' ? { limit: query.limit } : {}),
  })
}
