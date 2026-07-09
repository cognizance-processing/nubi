/**
 * notebooks.js — API client for the Notebook / preview endpoints.
 *
 * Endpoints:
 *   POST /flows/preview          — run a single cell interactively (sampled rows)
 *   POST /flows/{id}/run         — durable run-all (delegates to flows.js)
 *
 * The preview endpoint (PreviewCellIn, backend/app/routes/flows.py) accepts
 * { spec | flow_id, cell_key?, params?, preview_limit? } and returns
 * { cell_key, columns, rows, row_count, total_row_count }.  The backend
 * executes all upstream cells in the dependency chain itself, so the client
 * only sends the full spec plus the target cell key.  Falls back gracefully
 * on any error.
 */

import { post } from './api.js'

const BASE = '/flows'

// ---------------------------------------------------------------------------
// previewCell
// ---------------------------------------------------------------------------

/**
 * Run a single cell in preview (interactive) mode.
 *
 * Calls POST /flows/preview with the full inline spec; the backend walks the
 * dependency chain, executes all upstream cells first, then the target cell
 * against sampled data (preview_limit rows, default 500).
 *
 * @param {object} spec — full FlowSpec/NotebookSpec dict (unsaved edits included)
 * @param {string} cellKey — key of the target cell to run
 * @param {{ params?: object, previewLimit?: number }} [opts]
 * @returns {Promise<{
 *   rows: object[],
 *   columns: string[],
 *   row_count: number,
 *   total_row_count?: number,
 *   elapsed_ms: number,
 *   error?: string,
 * }>}
 */
export async function previewCell(spec, cellKey, opts = {}) {
  const started = Date.now()
  try {
    const body = { spec, cell_key: cellKey }
    if (opts.params && Object.keys(opts.params).length > 0) {
      body.params = opts.params
    }
    if (opts.previewLimit) {
      body.preview_limit = opts.previewLimit
    }
    const data = await post(`${BASE}/preview`, body)
    return { elapsed_ms: Date.now() - started, ...data }
  } catch (err) {
    console.warn('[notebooks] previewCell failed:', err.message)
    return {
      rows: [],
      columns: [],
      row_count: 0,
      elapsed_ms: Date.now() - started,
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
