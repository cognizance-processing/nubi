/**
 * notebooks.js — API client for the Notebook / preview endpoints.
 *
 * Endpoints:
 *   POST /flows/preview          — run a single cell interactively (sampled rows)
 *   POST /flows/{id}/run         — durable run-all (delegates to flows.js)
 *
 * The preview endpoint is defined by EndpointsAgent.  It accepts a
 * { cell, upstream_results? } payload and returns { rows, columns, row_count,
 * elapsed_ms } JSON.  Falls back gracefully on any error.
 *
 * Cell shape expected by the preview endpoint (mirrors CellSpec / TaskSpec):
 *   {
 *     key:        string,          // stable cell slug
 *     kind:       'query' | 'python',
 *     cell_type:  'sql' | 'python',
 *     config:     {
 *       sql?:           string,    // for sql cells
 *       code?:          string,    // for python cells
 *       datastore_id?:  string,    // BYO connector; absent = demo DuckDB
 *       preview_limit?: number,    // default 500
 *     },
 *   }
 */

import { post, get } from './api.js'

const BASE = '/flows'

// ---------------------------------------------------------------------------
// previewCell
// ---------------------------------------------------------------------------

/**
 * Run a single cell in preview (interactive) mode.
 *
 * Calls POST /flows/preview.  The backend executes the cell against sampled
 * data (preview_limit rows) and returns rows + column metadata as JSON.
 *
 * upstream_results is an optional dict of { [cellKey]: { rows, columns } }
 * that represents previously-run cells in the same notebook session, giving
 * the backend the ability to wire cross-cell references without DuckDB-WASM.
 *
 * @param {{
 *   key: string,
 *   kind: string,
 *   cell_type: string,
 *   config: object,
 * }} cell — CellSpec-compatible task
 * @param {Record<string, { rows: object[], columns: string[] }>} [upstreamResults]
 * @returns {Promise<{
 *   rows: object[],
 *   columns: string[],
 *   row_count: number,
 *   elapsed_ms: number,
 *   error?: string,
 * }>}
 */
export async function previewCell(cell, upstreamResults) {
  try {
    const body = { cell }
    if (upstreamResults && Object.keys(upstreamResults).length > 0) {
      body.upstream_results = upstreamResults
    }
    const data = await post(`${BASE}/preview`, body)
    return data
  } catch (err) {
    console.warn('[notebooks] previewCell failed:', err.message)
    return {
      rows: [],
      columns: [],
      row_count: 0,
      elapsed_ms: 0,
      error: err.message ?? 'Preview failed',
    }
  }
}

// ---------------------------------------------------------------------------
// Cell key helpers
// ---------------------------------------------------------------------------

/**
 * Generate a stable, unique cell key using a human-readable slug prefix
 * plus a short random suffix (blueprint §2.5 — stable UUID slugs).
 *
 * e.g. "cell_sql_4f2a", "cell_python_9e1b", "cell_note_3c8d"
 *
 * @param {'sql' | 'python' | 'markdown'} cellType
 * @returns {string}
 */
export function genCellKey(cellType = 'sql') {
  // 'markdown' cells read better as 'note' in the key slug.
  const slug = cellType === 'markdown' ? 'note' : cellType
  const suffix = Math.random().toString(36).slice(2, 6)
  return `cell_${slug}_${suffix}`
}

/**
 * Make a blank CellSpec for the given cell type.
 *
 * v4 "cells, not kinds": three user-facing cell types — sql (kind 'query'),
 * python (kind 'python'), and markdown/Note (kind 'noop', config.markdown).
 *
 * @param {'sql' | 'python' | 'markdown'} cellType
 * @returns {object}
 */
export function makeBlankCell(cellType = 'sql') {
  const key = genCellKey(cellType)
  if (cellType === 'markdown') {
    return {
      key,
      kind: 'noop',
      cell_type: 'markdown',
      needs: [],
      config: { markdown: '' },
      retries: 0,
      retry_backoff_s: 30,
      timeout_s: 60,
      cache_ttl_s: 0,
    }
  }
  if (cellType === 'python') {
    return {
      key,
      kind: 'python',
      cell_type: 'python',
      needs: [],
      config: { code: '# Write your Python code here\nresult = {}' },
      retries: 0,
      retry_backoff_s: 30,
      timeout_s: 60,
      cache_ttl_s: 0,
    }
  }
  return {
    key,
    kind: 'query',
    cell_type: 'sql',
    needs: [],
    config: { sql: '' },
    retries: 0,
    retry_backoff_s: 30,
    timeout_s: 60,
    cache_ttl_s: 0,
  }
}

// ---------------------------------------------------------------------------
// Lineage API helpers
// ---------------------------------------------------------------------------

const LINEAGE_BASE = '/lineage'

/**
 * Fetch column-level lineage for a stored flow (GET /lineage/flow/{id}).
 *
 * @param {string} flowId
 * @returns {Promise<{ flow_id: string, issues: string[], lineage: object|null }>}
 */
export async function fetchFlowLineage(flowId) {
  try {
    return await get(`${LINEAGE_BASE}/flow/${flowId}`)
  } catch (err) {
    console.warn('[notebooks] fetchFlowLineage failed:', err.message)
    return { flow_id: flowId, issues: [err.message ?? 'Lineage fetch failed'], lineage: null }
  }
}

/**
 * Ephemeral column lineage for a single ad-hoc cell (POST /lineage/cell).
 *
 * @param {{
 *   sql: string,
 *   dialect?: string,
 *   cell_key?: string,
 *   upstream_cells?: Record<string, string>,
 * }} params
 * @returns {Promise<{ cell_key: string, edges: object[] }>}
 */
export async function fetchCellLineage({ sql, dialect = 'duckdb', cell_key = '', upstream_cells = {} }) {
  try {
    return await post(`${LINEAGE_BASE}/cell`, { sql, dialect, cell_key, upstream_cells })
  } catch (err) {
    console.warn('[notebooks] fetchCellLineage failed:', err.message)
    return { cell_key, edges: [] }
  }
}

/**
 * Ephemeral plan-before-apply (POST /lineage/plan).
 * Returns impact report: which downstream cells would be affected.
 *
 * @param {{ spec: object, changed_cell_key: string }} params
 * @returns {Promise<{
 *   valid: boolean,
 *   issues: string[],
 *   lineage: object|null,
 *   downstream_impact: Array<{ cell_key: string, change_type: string, affected_columns: string[] }>,
 * }>}
 */
export async function fetchLineagePlan({ spec, changed_cell_key }) {
  try {
    return await post(`${LINEAGE_BASE}/plan`, { spec, changed_cell_key })
  } catch (err) {
    console.warn('[notebooks] fetchLineagePlan failed:', err.message)
    return { valid: false, issues: [err.message ?? 'Plan fetch failed'], lineage: null, downstream_impact: [] }
  }
}

// ---------------------------------------------------------------------------
// specToNotebook / notebookToSpec
// ---------------------------------------------------------------------------

/**
 * Convert a FlowSpec to a notebook-friendly representation.
 * Notebook is just the spec with view='notebook' ensured on the envelope.
 * Individual cells have cell_type inferred from kind when absent.
 *
 * @param {object} spec  FlowSpec
 * @returns {object}     same spec annotated with view + cell_type on tasks
 */
export function specToNotebook(spec) {
  if (!spec) return { version: 1, name: 'untitled', params: [], tasks: [], view: 'notebook' }
  const tasks = (spec.tasks ?? []).map(task => ({
    ...task,
    cell_type: task.cell_type ?? (task.kind === 'python' ? 'python' : 'sql'),
  }))
  return { ...spec, tasks, view: 'notebook' }
}

/**
 * Convert a notebook spec (with view='notebook') back to a canonical FlowSpec.
 * Strips notebook-only presentation fields so the executor sees a plain FlowSpec.
 *
 * @param {object} notebook
 * @returns {object}  FlowSpec
 */
export function notebookToSpec(notebook) {
  if (!notebook) return { version: 1, name: 'untitled', params: [], tasks: [] }
  // eslint-disable-next-line no-unused-vars
  const { view, ...rest } = notebook
  return rest
}
