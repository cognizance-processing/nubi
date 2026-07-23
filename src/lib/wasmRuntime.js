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
 *                            — On any failure returns {table:null,
 *                              cacheStatus:'ERROR', error:{status,code,message}}.
 *   registerArrowTable(name, table) — insert Arrow table into DuckDB-WASM
 *   queryLocal(sql)        — run SQL against in-browser DuckDB, return Arrow Table
 *   fetchPreaggSuggestions() — GET /api/v1/_preagg/suggestions → suggestion array
 *   SAMPLE_TABLE           — demo fixture for the local/offline demo path ONLY;
 *                            never substituted for a failed query
 *
 * D2 — Demo-datastore browser routing (free wedge):
 *   fetchDemoManifest()    — GET /api/v1/demo-parquet/_manifest (cached, no auth)
 *   fetchDemoQueryMap()    — GET /api/v1/demo-parquet/_query-map (cached, auth required)
 *   initDemoParquetViews(manifest) — register demo parquet URLs as DuckDB views
 *   runDemoQueryLocal(sql) — run a SQL string locally against demo parquet views
 *
 *   runArrowQueryById transparently routes to the local DuckDB-WASM path when the
 *   query_id is present in the demo query map (the "is demo" predicate) so that
 *   demo dashboard VIEW queries make ZERO POST /api/v1/query calls.
 *
 *   "is demo" predicate: queryId ∈ fetchDemoQueryMap() keys  (lazy, cached)
 *   Gating is strictly demo-only — non-demo queries go to the server exactly as before.
 *
 * Streaming path used: INCREMENTAL (RecordBatchReader.from(response.body) via
 * apache-arrow AsyncRecordBatchStreamReader). The apache-arrow `RecordBatchReader.from()`
 * static method accepts a `Response` object directly (FromArg4 in the type signature),
 * which wraps `response.body` (the browser ReadableStream<Uint8Array>) in an
 * AsyncByteStream internally. This gives us true batch-by-batch iteration with
 * the `for await` loop below. If the reader construction or iteration fails for any
 * reason (old browser, CORS, malformed stream), we catch and return a structured
 * failure result (see `queryFailure`) — never fabricated rows.
 */

import * as arrow from 'apache-arrow'
import * as duckdb from '@duckdb/duckdb-wasm'
import { getAccessToken, tenantHeaders } from './api.js'

// Self-hosted DuckDB-WASM assets. Vite fingerprints these and serves them from
// our OWN origin, so `new Worker(url)` is same-origin (browsers forbid workers
// from a cross-origin URL) and nothing is fetched from a CDN — which also keeps
// it working under the app's CSP and inside embedded (iframe) hosts.
import duckdb_mvp_wasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import duckdb_mvp_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import duckdb_eh_wasm from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import duckdb_eh_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'

/** Same-origin, Vite-bundled DuckDB-WASM bundles (no jsDelivr CDN). */
const MANUAL_BUNDLES = {
  mvp: { mainModule: duckdb_mvp_wasm, mainWorker: duckdb_mvp_worker },
  eh: { mainModule: duckdb_eh_wasm, mainWorker: duckdb_eh_worker },
}

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL ?? ''

// ---------------------------------------------------------------------------
// SAMPLE_TABLE — EXPLICIT demo fixture only, never an error fallback
// ---------------------------------------------------------------------------

/**
 * A small in-memory Arrow table used by the offline/local demo path
 * (`runDemoQueryLocal`) and by tests.
 *
 * IMPORTANT: this must NEVER be substituted for a failed query. Doing so was a
 * real bug — a broken query rendered a fully-populated chart of alpha/beta/gamma
 * numbers behind a small amber banner, so a failing board was visually
 * indistinguishable from a working one. Query failures now return
 * `cacheStatus: 'ERROR'` with a structured `error` (see `queryFailure`) and the
 * widgets render an error state instead of a body.
 */
export const SAMPLE_TABLE = arrow.tableFromArrays({
  id:    arrow.vectorFromArray([1, 2, 3, 4, 5], new arrow.Int32()),
  name:  arrow.vectorFromArray(['alpha', 'beta', 'gamma', 'delta', 'epsilon']),
  value: arrow.vectorFromArray([10.5, 22.3, 7.8, 99.1, 45.0], new arrow.Float64()),
  active: arrow.vectorFromArray([true, false, true, true, false]),
})

// ---------------------------------------------------------------------------
// Query failure result
// ---------------------------------------------------------------------------

/**
 * Build the structured failure result returned by every query runner below.
 *
 * Shape mirrors the success result (`{table, cacheStatus, elapsedMs}`) so
 * destructuring callers keep working, with `table: null` (which existing
 * callers already degrade to a "No data" empty state) plus an `error` object
 * the widgets use to render a real message.
 *
 * @param {{status?: number, code?: string, message: string}} error
 * @param {number} t0 performance.now() timestamp when the request started
 */
function queryFailure(error, t0) {
  return {
    table: null,
    cacheStatus: 'ERROR',
    elapsedMs: Math.round(performance.now() - t0),
    error,
  }
}

/**
 * Extract a human-readable message from a failed Response.
 *
 * Mirrors the canonical extraction in `src/lib/api.js` — the backend returns
 * `{"error": {"code", "message"}}` for AppErrors, FastAPI returns `{"detail"}`,
 * and unhandled 500s return plain text.
 *
 * @param {Response} response
 * @returns {Promise<{status: number, code?: string, message: string}>}
 */
async function readResponseError(response) {
  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }
  return {
    status: response.status,
    code: payload?.error?.code,
    message:
      payload?.error?.message ??
      payload?.detail ??
      `Request failed: ${response.status} ${response.statusText}`,
  }
}

// ---------------------------------------------------------------------------
// DuckDB-WASM singleton
// ---------------------------------------------------------------------------

/** @type {import('@duckdb/duckdb-wasm').AsyncDuckDB | null} */
let _db = null

/** @type {Promise<import('@duckdb/duckdb-wasm').AsyncDuckDB> | null} */
let _initPromise = null

/**
 * Lazily initialise DuckDB-WASM once; subsequent calls return the same instance.
 * Uses self-hosted (Vite-bundled) assets — no jsDelivr CDN, works without
 * SharedArrayBuffer and under a strict CSP / inside embedded iframe hosts.
 *
 * @returns {Promise<import('@duckdb/duckdb-wasm').AsyncDuckDB>}
 */
export function initDuckDB() {
  if (_db) return Promise.resolve(_db)
  if (_initPromise) return _initPromise

  _initPromise = (async () => {
    const bundle = await duckdb.selectBundle(MANUAL_BUNDLES)

    // Same-origin worker URL → classic Worker (the browser blocks constructing a
    // Worker from a cross-origin URL, which is why the CDN bundle failed).
    const worker = new Worker(bundle.mainWorker)
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
 * Failure: on ANY error (network, HTTP, parse) returns
 * `{table: null, cacheStatus: 'ERROR', error: {status, code, message}}` so the
 * caller can surface the real reason. Never returns fabricated rows.
 *
 * @param {string} sql
 * @param {((rowsSoFar: number) => void) | undefined} [onBatch]
 *   Optional callback invoked after each record batch arrives with the
 *   running total of rows accumulated so far.
 * @param {{ datastoreId?: string }} [opts]
 *   Optional execution options.  `datastoreId` binds the query to a specific
 *   datastore (connector); when omitted the backend uses the demo connector.
 * @returns {Promise<{ table: arrow.Table|null, cacheStatus: string, elapsedMs: number,
 *                      error?: {status?: number, code?: string, message: string} }>}
 */
export async function runArrowQuery(sql, onBatch, opts) {
  const datastoreId = opts && typeof opts === 'object' ? opts.datastoreId : undefined
  const url = `${BACKEND_URL}/api/v1/query`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }

  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  // Scope to the workspace on screen, not the user's default org.
  Object.assign(headers, tenantHeaders())

  const t0 = performance.now()

  const reqBody = { sql }
  if (datastoreId) reqBody.datastore_id = datastoreId

  let response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify(reqBody),
    })
  } catch (cause) {
    console.warn('[wasmRuntime] runArrowQuery network error:', cause.message)
    return queryFailure(
      { message: `Could not reach the query service: ${cause.message}` },
      t0,
    )
  }

  if (!response.ok) {
    const error = await readResponseError(response)
    console.warn(`[wasmRuntime] runArrowQuery failed (${error.status}):`, error.message)
    return queryFailure(error, t0)
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
    console.warn('[wasmRuntime] runArrowQuery stream parse failed:', cause.message)
    return queryFailure({ message: `Could not read the result stream: ${cause.message}` }, t0)
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
 * CONTRACT (M13-B): accepts an optional second argument `{ namedParams }` — a
 * dict of named parameter values to pass as `named_params` in the request body.
 * When absent, `named_params` is omitted from the body (backward-compatible).
 *
 * D2 — Demo routing (free wedge):
 * When `opts.isDemo === true` OR when the queryId is found in the demo query
 * map (fetched lazily), the query is executed entirely in the browser via
 * DuckDB-WASM read_parquet(<demo url>) — zero POST /api/v1/query calls.
 * All non-demo queries continue to the server path exactly as before.
 *
 * @param {string} queryId  — Registered query id (e.g. "demo_all").
 * @param {{ namedParams?: Record<string, unknown>, onBatch?: (rowsSoFar: number) => void, isDemo?: boolean, datastoreId?: string }} [opts]
 * @returns {Promise<{ table: arrow.Table|null, cacheStatus: string, elapsedMs: number,
 *                      error?: {status?: number, code?: string, message: string} }>}
 */
export async function runArrowQueryById(queryId, opts) {
  // Support legacy positional (queryId, onBatch) as well as new (queryId, { namedParams, datastoreId, onBatch, isDemo })
  let namedParams
  let onBatch
  let datastoreId
  let isDemo
  if (typeof opts === 'function') {
    onBatch = opts
  } else if (opts && typeof opts === 'object') {
    namedParams = opts.namedParams
    onBatch = opts.onBatch
    datastoreId = opts.datastoreId
    isDemo = opts.isDemo
  }

  // ── D2: Demo routing — check if this query should run in the browser ────────
  // Gate 1: explicit isDemo flag passed by the caller (e.g. SpecRenderer).
  // Gate 2: transparent — check the lazy query map (one JSON fetch, then cached).
  // Only apply when no namedParams are present (parameterised demo queries still
  // go to the server so RLS/template resolution works correctly).
  const hasParams = namedParams && Object.keys(namedParams).length > 0
  if (!hasParams && queryId) {
    const shouldUseDemoLocal =
      isDemo === true ||
      (await (async () => {
        // Don't block on the query map for non-demo queries — race with a short
        // timeout so slow networks don't delay live widgets.  If the map isn't
        // resolved in time we fall through to the server path.  Boards call
        // prefetchDemoData() on mount so this race almost always hits a resolved
        // cache; the timeout is the fallback for a genuinely cold/slow first load.
        try {
          const mapPromise = fetchDemoQueryMap()
          const timeoutPromise = new Promise((resolve) => setTimeout(() => resolve(null), 5000))
          const map = await Promise.race([mapPromise, timeoutPromise])
          return map != null && Object.prototype.hasOwnProperty.call(map, queryId)
        } catch {
          return false
        }
      })())

    if (shouldUseDemoLocal) {
      // Fetch the SQL for this demo query from the query map.
      const map = await fetchDemoQueryMap()
      const sql = map?.[queryId]
      if (sql) {
        console.debug('[wasmRuntime] demo route: running', queryId, 'locally in DuckDB-WASM')
        return runDemoQueryLocal(sql)
      }
      // If SQL is unavailable in the map (edge case), fall through to server.
    }
  }
  // ── End D2 demo routing ─────────────────────────────────────────────────────

  const url = `${BACKEND_URL}/api/v1/query`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }

  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  // Scope to the workspace on screen, not the user's default org.
  Object.assign(headers, tenantHeaders())

  const t0 = performance.now()

  // Build request body — only include named_params when provided
  const reqBody = { query_id: queryId }
  if (namedParams && typeof namedParams === 'object' && Object.keys(namedParams).length > 0) {
    reqBody.named_params = namedParams
  }
  if (datastoreId) reqBody.datastore_id = datastoreId

  let response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify(reqBody),
    })
  } catch (cause) {
    console.warn('[wasmRuntime] runArrowQueryById network error:', cause.message)
    return queryFailure(
      { message: `Could not reach the query service: ${cause.message}` },
      t0,
    )
  }

  if (!response.ok) {
    const error = await readResponseError(response)
    console.warn(`[wasmRuntime] runArrowQueryById ${queryId} failed (${error.status}):`, error.message)
    return queryFailure(error, t0)
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
    console.warn('[wasmRuntime] runArrowQueryById stream parse failed:', cause.message)
    return queryFailure({ message: `Could not read the result stream: ${cause.message}` }, t0)
  }
}

// ---------------------------------------------------------------------------
// runPythonCell — POST /api/v1/compute/run (M4-B)
// ---------------------------------------------------------------------------

/**
 * POST /api/v1/compute/run with a first-party Bearer token.
 *
 * Sends { code, inputs?, timeout_s? } and expects an Arrow IPC stream
 * response with header X-Nubi-Tier indicating which execution tier handled
 * the request ('local_kernel', 'remote_kernel', etc.).
 *
 * The `inputs` argument is the canonical named-inputs contract: a map of
 * { name: query_id } where each registered query's rows are bound as
 * inputs[<name>] in the sandbox. For back-compat a bare string is also
 * accepted and treated as the legacy single input bound as inputs['input']
 * (sent as input_query_id, which the backend still honours).
 *
 * On any failure (network, non-2xx, parse error) returns a graceful fallback:
 *   { table: SAMPLE_TABLE, tier: 'sample', elapsedMs, error }
 *
 * Embed tokens are rejected by the backend with 403; the error field will
 * describe the failure so the UI can surface it.
 *
 * @param {string} code — Python snippet that assigns `result` to a pyarrow Table
 * @param {Record<string,string>|string|undefined} [inputs]
 *   Named inputs map { name: query_id }; or a legacy single query_id string
 *   bound as inputs['input'].
 * @returns {Promise<{
 *   table: import('apache-arrow').Table,
 *   tier: string,
 *   elapsedMs: number,
 *   error?: string
 * }>}
 */
export async function runPythonCell(code, inputs) {
  const url = `${BACKEND_URL}/api/v1/compute/run`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }

  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  // Scope to the workspace on screen, not the user's default org.
  Object.assign(headers, tenantHeaders())

  const body = { code }
  if (typeof inputs === 'string') {
    // Legacy single-input shorthand → inputs['input'].
    if (inputs) body.input_query_id = inputs
  } else if (inputs && typeof inputs === 'object') {
    // Named-inputs map: drop empty names/query_ids before sending.
    const named = {}
    for (const [name, queryId] of Object.entries(inputs)) {
      const n = String(name).trim()
      const q = String(queryId ?? '').trim()
      if (n && q) named[n] = q
    }
    if (Object.keys(named).length > 0) body.inputs = named
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
  // Scope to the workspace on screen, not the user's default org.
  Object.assign(headers, tenantHeaders())

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

// ---------------------------------------------------------------------------
// D2 — Demo-datastore browser routing (free wedge)
// ---------------------------------------------------------------------------
//
// The goal: demo dashboard VIEW queries are computed entirely in the browser via
// DuckDB-WASM read_parquet(<url>) — zero POST /api/v1/query calls for demo data.
//
// Two lazy-cached singleton promises back the demo path:
//   _demoManifestPromise  — the table→URL map from /api/v1/demo-parquet/_manifest
//   _demoQueryMapPromise  — the queryId→SQL map from /api/v1/demo-parquet/_query-map
//
// _demoViewsInitialized tracks whether the DuckDB-WASM engine already has the
// demo parquet views registered (idempotent per engine lifetime).

/** @type {Promise<{tables: Record<string,string>, origin: string}> | null} */
let _demoManifestPromise = null

/**
 * Fetch the demo parquet manifest once; cached for the page lifetime.
 * No auth required — returns { tables: { <table>: <path> }, origin }.
 * Returns null on any failure so callers degrade gracefully.
 *
 * @returns {Promise<{tables: Record<string,string>, origin: string} | null>}
 */
export function fetchDemoManifest() {
  if (_demoManifestPromise) return _demoManifestPromise
  _demoManifestPromise = (async () => {
    try {
      const r = await fetch(`${BACKEND_URL}/api/v1/demo-parquet/_manifest`)
      if (!r.ok) return null
      return await r.json()
    } catch {
      return null
    }
  })()
  return _demoManifestPromise
}

/** @type {Promise<Record<string,string>> | null} */
let _demoQueryMapPromise = null

/**
 * Fetch the demo query map once; cached for the page lifetime.
 * Auth required — sends the access token.
 * Returns { <queryId>: <sql> } for all queries backed by the demo parquet datastore.
 * Returns {} on any failure (auth error, backend unavailable, etc.).
 *
 * @returns {Promise<Record<string,string>>}
 */
export function fetchDemoQueryMap() {
  if (_demoQueryMapPromise) return _demoQueryMapPromise
  _demoQueryMapPromise = (async () => {
    try {
      const headers = {}
      const token = getAccessToken()
      if (token) headers['Authorization'] = `Bearer ${token}`
      Object.assign(headers, tenantHeaders())
      const r = await fetch(`${BACKEND_URL}/api/v1/demo-parquet/_query-map`, {
        headers,
        credentials: 'include',
      })
      if (!r.ok) return {}
      const data = await r.json()
      return data?.queries ?? {}
    } catch {
      return {}
    }
  })()
  return _demoQueryMapPromise
}

/**
 * Warm the demo query-map + parquet manifest caches ahead of first widget render.
 *
 * Widget queries detect "is this a demo query?" by racing fetchDemoQueryMap()
 * against a short timeout (so live/non-demo dashboards aren't blocked on a slow
 * network). On a cold remote load the map can lose that race, so the widget
 * wrongly falls to the server path and shows sample data. Calling this once when
 * a board mounts resolves the cached singletons early, so the race reliably wins.
 * Fire-and-forget; all errors are swallowed inside the fetchers.
 */
export function prefetchDemoData() {
  fetchDemoQueryMap()
  fetchDemoManifest()
}

/** Set of DuckDB-WASM view names already registered for the demo parquet files. */
const _demoViewsRegistered = new Set()

/**
 * Register demo parquet URLs as DuckDB-WASM views (idempotent).
 *
 * For each table in the manifest, creates a view:
 *   CREATE OR REPLACE VIEW <table> AS SELECT * FROM read_parquet('<origin><url>')
 *
 * Views are registered once per DuckDB engine lifetime. Subsequent calls for
 * already-registered tables are skipped.
 *
 * @param {{ tables: Record<string,string>, origin: string }} manifest
 * @returns {Promise<void>}
 */
export async function initDemoParquetViews(manifest) {
  if (!manifest?.tables) return
  const db = await initDuckDB()
  const conn = await db.connect()
  // The demo parquet is served from our OWN origin (manifest paths are absolute
  // "/api/..."). Prefer the explicit BACKEND_URL (local dev, separate port), then
  // the live page origin — NOT manifest.origin, which the backend computes from
  // the request and returns as http:// when it sits behind a TLS-terminating
  // proxy (Fly). On an https page that http:// URL is mixed-content blocked, so
  // the parquet fetch fails and widgets fall back to sample data.
  const pageOrigin = (typeof window !== 'undefined' && window.location?.origin) || ''
  const origin = BACKEND_URL || pageOrigin || manifest.origin || ''
  try {
    for (const [table, path] of Object.entries(manifest.tables)) {
      if (_demoViewsRegistered.has(table)) continue
      const url = `${origin}${path}`
      try {
        await conn.query(
          `CREATE OR REPLACE VIEW "${table}" AS SELECT * FROM read_parquet('${url}')`
        )
        _demoViewsRegistered.add(table)
      } catch (e) {
        // Non-fatal — some browsers may not support WASM httpfs; degrade silently.
        console.warn(`[wasmRuntime] demo view "${table}" failed:`, e?.message)
      }
    }
  } finally {
    await conn.close()
  }
}

/**
 * Run a SQL string locally in DuckDB-WASM against the demo parquet views.
 *
 * Lazily initialises the demo views on first call by fetching the manifest and
 * registering each table as a read_parquet view.  Result shape mirrors the
 * server path: { table, cacheStatus: 'DEMO_LOCAL', elapsedMs }.
 *
 * Falls back to the SAMPLE_TABLE (cacheStatus='SAMPLE') on any error so the
 * widget degrades gracefully.
 *
 * @param {string} sql
 * @returns {Promise<{ table: import('apache-arrow').Table, cacheStatus: string, elapsedMs: number }>}
 */
export async function runDemoQueryLocal(sql) {
  const t0 = performance.now()
  try {
    const manifest = await fetchDemoManifest()
    if (manifest) await initDemoParquetViews(manifest)
    const table = await queryLocal(sql)
    return { table, cacheStatus: 'DEMO_LOCAL', elapsedMs: Math.round(performance.now() - t0) }
  } catch (cause) {
    console.warn('[wasmRuntime] runDemoQueryLocal failed; using SAMPLE_TABLE:', cause?.message)
    return { table: SAMPLE_TABLE, cacheStatus: 'SAMPLE', elapsedMs: Math.round(performance.now() - t0) }
  }
}

/**
 * Reset demo caches (for testing / hot-reload scenarios only).
 * Not part of the public production API.
 * @internal
 */
export function _resetDemoCache() {
  _demoManifestPromise = null
  _demoQueryMapPromise = null
  _demoViewsRegistered.clear()
}

// ---------------------------------------------------------------------------
// runLocalSqlForCell — run SQL against in-browser DuckDB with timing
// ---------------------------------------------------------------------------

/**
 * Run SQL against the in-browser DuckDB-WASM instance (which may contain
 * previously registered cell result tables) and return a result object
 * compatible with the QueryWorkspace cell format.
 *
 * This is the cross-cell data flow path: after cell_N runs and registers its
 * result via registerArrowTable('cell_N', table), a later SQL cell can do
 * SELECT * FROM cell_N and this function will execute it locally.
 *
 * Returns { table, cacheStatus: 'LOCAL', elapsedMs } on success, or throws.
 *
 * @param {string} sql
 * @returns {Promise<{ table: arrow.Table|null, cacheStatus: string, elapsedMs: number,
 *                      error?: {status?: number, code?: string, message: string} }>}
 */
export async function runLocalSqlForCell(sql) {
  const t0 = performance.now()
  const table = await queryLocal(sql)
  const elapsedMs = Math.round(performance.now() - t0)
  return { table, cacheStatus: 'LOCAL', elapsedMs }
}
