/**
 * metricRuntime.js — server-side fetch for governed METRIC bindings.
 *
 * Dashboard widgets can bind either to a registered `query_id` (handled by
 * runArrowQueryById in wasmRuntime.js) OR to a governed `metric`. Metric
 * queries MUST run server-side: the backend compiles the metric definition and
 * injects row-level security before executing. We therefore POST the metric
 * binding to the metrics endpoint and decode the Arrow IPC stream response into
 * the SAME shape runArrowQueryById returns ({ table, cacheStatus }), so widgets
 * can consume either path interchangeably.
 *
 * Endpoint:
 *   POST /api/v1/metrics/{metric_id}/query
 *   body: { dimensions, time_grain, filters, limit? }
 *   → Arrow IPC stream (application/vnd.apache.arrow.stream), same response
 *     shape as POST /api/v1/query.
 *
 * Decoding + auth mirror runArrowQueryById exactly: the in-memory Bearer token
 * from api.js (getAccessToken) plus credentials:'include' for the HttpOnly
 * refresh cookie, and apache-arrow RecordBatchReader.from(response) for true
 * Arrow IPC stream decoding (which also yields an empty Table from the schema
 * when the result set is empty).
 *
 * Unlike the runArrow* helpers this throws on any failure — the widgets already
 * catch and surface errors — so a governance/RLS failure is never silently
 * masked with sample data.
 */

import * as arrow from 'apache-arrow'
import { getAccessToken } from './api.js'

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? ''

/**
 * Run a governed metric query server-side and return its result table.
 *
 * @param {{
 *   metric_id: string,
 *   dimensions?: string[],
 *   time_grain?: string | null,
 *   filters?: Array<{ field: string, op: string, value: unknown }>,
 *   limit?: number,
 * }} metric  — the widget's `metric` binding.
 * @param {{ signal?: AbortSignal }} [opts]
 * @returns {Promise<{ table: import('apache-arrow').Table, cacheStatus: string }>}
 */
export async function runMetricQuery(metric, { signal } = {}) {
  if (!metric || !metric.metric_id) {
    throw new Error('runMetricQuery: metric.metric_id is required')
  }

  const url = `${BACKEND_URL}/api/v1/metrics/${encodeURIComponent(metric.metric_id)}/query`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }

  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  // Build the request body from the metric binding. Always send the governed
  // shape the backend expects; default optional fields so the body is stable.
  const reqBody = {
    dimensions: Array.isArray(metric.dimensions) ? metric.dimensions : [],
    time_grain: metric.time_grain ?? null,
    filters: Array.isArray(metric.filters) ? metric.filters : [],
  }
  if (typeof metric.limit === 'number') reqBody.limit = metric.limit

  const response = await fetch(url, {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify(reqBody),
    signal,
  })

  if (!response.ok) {
    let payload
    try { payload = await response.json() } catch { payload = null }
    const message =
      payload?.error?.message ??
      payload?.detail ??
      `Metric query failed: ${response.status} ${response.statusText}`
    const err = new Error(message)
    err.status = response.status
    throw err
  }

  const cacheStatus = response.headers.get('X-Nubi-Cache') ?? 'MISS'

  // Incremental Arrow IPC stream decode — same supported path as
  // runArrowQueryById. RecordBatchReader.from(response) adapts response.body
  // into an AsyncByteStream and yields each RecordBatch as it arrives.
  const reader = await arrow.RecordBatchReader.from(response)
  await reader.open()

  const batches = []
  for await (const batch of reader) {
    batches.push(batch)
  }

  if (batches.length === 0) {
    // Empty result set — build an empty Table from the schema.
    return { table: new arrow.Table(reader.schema, []), cacheStatus }
  }

  return { table: new arrow.Table(batches), cacheStatus }
}
