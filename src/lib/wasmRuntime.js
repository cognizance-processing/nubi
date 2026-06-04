/**
 * wasmRuntime.js — DuckDB-WASM lazy runtime for Nubi M2-D.
 *
 * Provides:
 *   initDuckDB()           — idempotent lazy init; returns the db instance
 *   runArrowQuery(sql, onBatch?) → { table, cacheStatus, elapsedMs }
 *                            — POST /api/v1/query → Arrow IPC STREAM → arrow.Table
 *                            — Incremental path: RecordBatchReader.from(response)
 *                              iterates batches via `for await`, accumulating into
 *                              a Table; calls onBatch(rowsSoFar) after each batch.
 *                            — Falls back to buffered arrayBuffer + tableFromIPC if
 *                              the async iterator path is unavailable.
 *                            — On any failure returns SAMPLE_TABLE fallback.
 *   registerArrowTable(name, table) — insert Arrow table into DuckDB-WASM
 *   queryLocal(sql)        — run SQL against in-browser DuckDB, return Arrow Table
 *   fetchPreaggSuggestions() — GET /api/v1/_preagg/suggestions → suggestion array
 *   SAMPLE_TABLE           — small in-memory Arrow table for offline fallback
 *
 * Streaming path used: INCREMENTAL (RecordBatchReader.from(response.body) via
 * apache-arrow AsyncRecordBatchStreamReader). The apache-arrow `RecordBatchReader.from()`
 * static method accepts a `Response` object directly (FromArg4 in the type signature),
 * which wraps `response.body` (the browser ReadableStream<Uint8Array>) in an
 * AsyncByteStream internally. This gives us true batch-by-batch iteration with
 * the `for await` loop below. If the reader construction or iteration fails for any
 * reason (old browser, CORS, malformed stream), we catch and fall back to SAMPLE_TABLE.
 */

import * as arrow from 'apache-arrow'
import * as duckdb from '@duckdb/duckdb-wasm'
import { getAccessToken } from './api.js'

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? 'http://localhost:8000'

// ---------------------------------------------------------------------------
// SAMPLE_TABLE — graceful fallback when backend is unavailable
// ---------------------------------------------------------------------------

export const SAMPLE_TABLE = arrow.tableFromArrays({
  id:    arrow.vectorFromArray([1, 2, 3, 4, 5], new arrow.Int32()),
  name:  arrow.vectorFromArray(['alpha', 'beta', 'gamma', 'delta', 'epsilon']),
  value: arrow.vectorFromArray([10.5, 22.3, 7.8, 99.1, 45.0], new arrow.Float64()),
  active: arrow.vectorFromArray([true, false, true, true, false]),
})

// ---------------------------------------------------------------------------
// DuckDB-WASM singleton
// ---------------------------------------------------------------------------

/** @type {import('@duckdb/duckdb-wasm').AsyncDuckDB | null} */
let _db = null

/** @type {Promise<import('@duckdb/duckdb-wasm').AsyncDuckDB> | null} */
let _initPromise = null

/**
 * Lazily initialise DuckDB-WASM once; subsequent calls return the same instance.
 * Uses the jsDelivr CDN bundle — no extra vite config, works without SharedArrayBuffer.
 *
 * @returns {Promise<import('@duckdb/duckdb-wasm').AsyncDuckDB>}
 */
export function initDuckDB() {
  if (_db) return Promise.resolve(_db)
  if (_initPromise) return _initPromise

  _initPromise = (async () => {
    const bundles = duckdb.getJsDelivrBundles()
    const bundle = await duckdb.selectBundle(bundles)

    const workerUrl = bundle.mainWorker
    const worker = new Worker(workerUrl, { type: 'module' })
    const logger = new duckdb.VoidLogger()
    const db = new duckdb.AsyncDuckDB(logger, worker)

    await db.instantiate(bundle.mainModule, bundle.pthreadWorker)

    _db = db
    return _db
  })()

  return _initPromise
}

// ---------------------------------------------------------------------------
// runArrowQuery — fetch from backend, parse Arrow IPC stream incrementally
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/query with {sql}, reads the Arrow IPC STREAM response
 * incrementally using apache-arrow RecordBatchReader.
 *
 * Streaming path: INCREMENTAL
 *   - RecordBatchReader.from(response) wraps the fetch Response directly.
 *     apache-arrow's FromArg4 overload accepts a Response object and internally
 *     adapts response.body (ReadableStream<Uint8Array>) into an AsyncByteStream.
 *   - We iterate batches with `for await...of`, accumulating RecordBatch objects.
 *   - After each batch, onBatch(rowsSoFar) is called so the UI can update a
 *     live rows counter while data is still arriving.
 *   - A final Table is constructed from all accumulated batches.
 *
 * Fallback: on ANY error (network, parse, browser compatibility) returns the
 * SAMPLE_TABLE with cacheStatus='SAMPLE' so the UI degrades gracefully.
 *
 * @param {string} sql
 * @param {((rowsSoFar: number) => void) | undefined} [onBatch]
 *   Optional callback invoked after each record batch arrives with the
 *   running total of rows accumulated so far.
 * @returns {Promise<{ table: arrow.Table, cacheStatus: string, elapsedMs: number }>}
 */
export async function runArrowQuery(sql, onBatch) {
  const url = `${BACKEND_URL}/api/v1/query`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }

  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const t0 = performance.now()

  let response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify({ sql }),
    })
  } catch (cause) {
    // Network failure — fall back to sample
    console.warn('[wasmRuntime] Network error; using SAMPLE_TABLE:', cause.message)
    return { table: SAMPLE_TABLE, cacheStatus: 'SAMPLE', elapsedMs: Math.round(performance.now() - t0) }
  }

  if (!response.ok) {
    console.warn(`[wasmRuntime] Query API returned ${response.status}; using SAMPLE_TABLE`)
    return { table: SAMPLE_TABLE, cacheStatus: 'SAMPLE', elapsedMs: Math.round(performance.now() - t0) }
  }

  // Read the X-Nubi-Cache header (HIT | MISS) set by the streaming backend
  const cacheStatus = response.headers.get('X-Nubi-Cache') ?? 'MISS'

  // -- Incremental streaming path ------------------------------------------
  // RecordBatchReader.from(response) is the Apache Arrow supported API for
  // consuming a fetch Response as an Arrow IPC stream. It returns a Promise
  // that resolves to an AsyncRecordBatchStreamReader, which supports
  // `for await...of` to iterate each RecordBatch as it arrives from the wire.
  try {
    const reader = await arrow.RecordBatchReader.from(response)
    await reader.open()

    const batches = []
    let rowsSoFar = 0

    for await (const batch of reader) {
      batches.push(batch)
      rowsSoFar += batch.numRows
      if (typeof onBatch === 'function') {
        onBatch(rowsSoFar)
      }
    }

    const elapsedMs = Math.round(performance.now() - t0)

    if (batches.length === 0) {
      // Empty result set — build an empty table from the schema
      const table = new arrow.Table(reader.schema, [])
      return { table, cacheStatus, elapsedMs }
    }

    // Construct the final Table from all accumulated RecordBatches
    const table = new arrow.Table(batches)
    return { table, cacheStatus, elapsedMs }

  } catch (cause) {
    console.warn('[wasmRuntime] Arrow stream parse failed; using SAMPLE_TABLE:', cause.message)
    return {
      table: SAMPLE_TABLE,
      cacheStatus: 'SAMPLE',
      elapsedMs: Math.round(performance.now() - t0),
    }
  }
}

// ---------------------------------------------------------------------------
// runArrowQueryById — POST /api/v1/query with {query_id}
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/query with {query_id} (a registered server-side query id).
 *
 * Identical streaming / fallback path to runArrowQuery, but sends
 * { query_id } instead of { sql } so the backend resolves the registered query.
 *
 * @param {string} queryId  — Registered query id (e.g. "demo_all").
 * @param {((rowsSoFar: number) => void) | undefined} [onBatch]
 * @returns {Promise<{ table: arrow.Table, cacheStatus: string, elapsedMs: number }>}
 */
export async function runArrowQueryById(queryId, onBatch) {
  const url = `${BACKEND_URL}/api/v1/query`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }

  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const t0 = performance.now()

  let response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify({ query_id: queryId }),
    })
  } catch (cause) {
    console.warn('[wasmRuntime] runArrowQueryById network error; using SAMPLE_TABLE:', cause.message)
    return { table: SAMPLE_TABLE, cacheStatus: 'SAMPLE', elapsedMs: Math.round(performance.now() - t0) }
  }

  if (!response.ok) {
    console.warn(`[wasmRuntime] runArrowQueryById ${queryId} returned ${response.status}; using SAMPLE_TABLE`)
    return { table: SAMPLE_TABLE, cacheStatus: 'SAMPLE', elapsedMs: Math.round(performance.now() - t0) }
  }

  const cacheStatus = response.headers.get('X-Nubi-Cache') ?? 'MISS'

  try {
    const reader = await arrow.RecordBatchReader.from(response)
    await reader.open()

    const batches = []
    let rowsSoFar = 0

    for await (const batch of reader) {
      batches.push(batch)
      rowsSoFar += batch.numRows
      if (typeof onBatch === 'function') onBatch(rowsSoFar)
    }

    const elapsedMs = Math.round(performance.now() - t0)

    if (batches.length === 0) {
      return { table: new arrow.Table(reader.schema, []), cacheStatus, elapsedMs }
    }

    return { table: new arrow.Table(batches), cacheStatus, elapsedMs }
  } catch (cause) {
    console.warn('[wasmRuntime] runArrowQueryById Arrow parse failed; using SAMPLE_TABLE:', cause.message)
    return { table: SAMPLE_TABLE, cacheStatus: 'SAMPLE', elapsedMs: Math.round(performance.now() - t0) }
  }
}

// ---------------------------------------------------------------------------
// runPythonCell — POST /api/v1/compute/run (M4-B)
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/compute/run with a first-party Bearer token.
 *
 * Sends { code, input_query_id?, timeout_s? } and expects an Arrow IPC stream
 * response with header X-Nubi-Tier indicating which execution tier handled
 * the request ('local_kernel', 'remote_kernel', etc.).
 *
 * On any failure (network, non-2xx, parse error) returns a graceful fallback:
 *   { table: SAMPLE_TABLE, tier: 'sample', elapsedMs, error }
 *
 * Embed tokens are rejected by the backend with 403; the error field will
 * describe the failure so the UI can surface it.
 *
 * @param {string} code          — Python snippet that assigns `result` to a pyarrow Table
 * @param {string|undefined} [inputQueryId] — optional registered query ID bound as inputs['input']
 * @returns {Promise<{
 *   table: import('apache-arrow').Table,
 *   tier: string,
 *   elapsedMs: number,
 *   error?: string
 * }>}
 */
export async function runPythonCell(code, inputQueryId) {
  const url = `${BACKEND_URL}/api/v1/compute/run`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }

  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const body = { code }
  if (inputQueryId) {
    body.input_query_id = inputQueryId
  }

  const t0 = performance.now()

  let response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify(body),
    })
  } catch (cause) {
    console.warn('[wasmRuntime] runPythonCell network error; using SAMPLE_TABLE:', cause.message)
    return {
      table: SAMPLE_TABLE,
      tier: 'sample',
      elapsedMs: Math.round(performance.now() - t0),
      error: `Network error: ${cause.message}`,
    }
  }

  if (!response.ok) {
    const errText = await response.text().catch(() => response.status.toString())
    console.warn(`[wasmRuntime] /compute/run returned ${response.status}; using SAMPLE_TABLE`)
    return {
      table: SAMPLE_TABLE,
      tier: 'sample',
      elapsedMs: Math.round(performance.now() - t0),
      error: `Server error ${response.status}: ${errText}`,
    }
  }

  const tier = response.headers.get('X-Nubi-Tier') ?? 'local_kernel'

  // Parse the Arrow IPC response body — buffered path is fine for compute results
  try {
    const buffer = await response.arrayBuffer()
    const table = arrow.tableFromIPC(buffer)
    const elapsedMs = Math.round(performance.now() - t0)
    return { table, tier, elapsedMs }
  } catch (cause) {
    console.warn('[wasmRuntime] runPythonCell Arrow parse failed; using SAMPLE_TABLE:', cause.message)
    return {
      table: SAMPLE_TABLE,
      tier: 'sample',
      elapsedMs: Math.round(performance.now() - t0),
      error: `Arrow parse error: ${cause.message}`,
    }
  }
}

// ---------------------------------------------------------------------------
// fetchPreaggSuggestions — GET /api/v1/_preagg/suggestions
// ---------------------------------------------------------------------------

/**
 * Fetch pre-aggregation rollup suggestions from the backend.
 * Requires a valid Bearer token; sends the HttpOnly refresh cookie.
 *
 * Returns an array of RollupSuggestion objects:
 *   { base_table, dimensions, measures, hits, est_bytes_saved }
 *
 * Returns [] on any failure (backend unavailable, unauthenticated, etc.)
 * so callers can degrade gracefully.
 *
 * @returns {Promise<Array<{
 *   base_table: string,
 *   dimensions: string[],
 *   measures: string[],
 *   hits: number,
 *   est_bytes_saved: number
 * }>>}
 */
export async function fetchPreaggSuggestions() {
  const url = `${BACKEND_URL}/api/v1/_preagg/suggestions`

  const headers = {}
  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  try {
    const response = await fetch(url, {
      method: 'GET',
      headers,
      credentials: 'include',
    })

    if (!response.ok) {
      console.warn(`[wasmRuntime] _preagg/suggestions returned ${response.status}; returning []`)
      return []
    }

    const data = await response.json()
    // Accept both a bare array and { suggestions: [...] } envelope
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.suggestions)) return data.suggestions
    return []
  } catch (cause) {
    console.warn('[wasmRuntime] fetchPreaggSuggestions failed; returning []:', cause.message)
    return []
  }
}

// ---------------------------------------------------------------------------
// registerArrowTable — insert an Arrow table into the in-browser DuckDB
// ---------------------------------------------------------------------------

/**
 * Register an Arrow Table into DuckDB-WASM under the given name so that
 * subsequent queryLocal() calls can reference it.
 *
 * @param {string} name   — SQL table name
 * @param {arrow.Table} table
 * @returns {Promise<void>}
 */
export async function registerArrowTable(name, table) {
  const db = await initDuckDB()
  const conn = await db.connect()
  try {
    await conn.insertArrowTable(table, { name, create: true })
  } finally {
    await conn.close()
  }
}

// ---------------------------------------------------------------------------
// queryLocal — run SQL against in-browser DuckDB, return Arrow Table
// ---------------------------------------------------------------------------

/**
 * Execute SQL against the in-browser DuckDB-WASM instance.
 * Useful for last-mile compute on previously registered Arrow tables.
 *
 * @param {string} sql
 * @returns {Promise<arrow.Table>}
 */
export async function queryLocal(sql) {
  const db = await initDuckDB()
  const conn = await db.connect()
  try {
    const result = await conn.query(sql)
    return result
  } finally {
    await conn.close()
  }
}
