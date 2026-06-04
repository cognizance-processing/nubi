/**
 * shared.js — Shared helpers for the Nubi widget kit (M8-A).
 *
 * All helpers are plain ES-module functions; no framework, no DOM globals at
 * import time (safe for SSR / build environments).
 *
 * Exports
 * -------
 *  resolveToken(el)              — reads token attr or calls window[get-token fn]
 *  fetchArrow(backend, queryId, token, signal?) → apache-arrow Table
 *  makeSampleKpiTable()          — single-row Table for KPI sample fallback
 *  makeSampleTableData()         — multi-row Table for table/chart sample fallback
 *  escapeHtml(str)               — XSS-safe text encoding
 *  formatCell(val)               — human-readable Arrow cell value
 *  el(tag, attrs?, children?)    — tiny sanitizer-safe DOM element builder
 */

import { tableFromIPC, tableFromArrays, vectorFromArray, Int32, Float64 } from 'apache-arrow'

// ---------------------------------------------------------------------------
// Token resolution  (mirrors nubi-dashboard.js pattern exactly)
// ---------------------------------------------------------------------------

/**
 * Resolve a JWT from a custom element:
 *   1. `token` attribute — static string.
 *   2. `get-token` attribute — name of a function on `window`.
 *
 * Returns null (not throws) on any failure so the caller can fall through to
 * the sample path gracefully.
 *
 * @param {HTMLElement} elem
 * @returns {Promise<string|null>}
 */
export async function resolveToken(elem) {
  const staticToken = elem.getAttribute('token')
  if (staticToken) return staticToken

  const fnName = elem.getAttribute('get-token')
  if (!fnName) return null

  const fn = (typeof window !== 'undefined') ? window[fnName] : undefined
  if (typeof fn !== 'function') {
    console.warn(`[nubi-widget] window.${fnName} is not a function`)
    return null
  }

  try {
    const tok = await fn()
    return tok ?? null
  } catch (err) {
    console.warn('[nubi-widget] get-token fn threw:', err.message)
    return null
  }
}

// ---------------------------------------------------------------------------
// Arrow fetch  (same fetch/parse pattern as nubi-dashboard.js)
// ---------------------------------------------------------------------------

/**
 * POST to {backend}/api/v1/query with {query_id} and Bearer token.
 * Parses the Arrow IPC stream body and returns a Table.
 *
 * @param {string} backend   — Base URL, e.g. "https://api.example.com"
 * @param {string} queryId   — Registered query id to execute.
 * @param {string|null} token — Bearer JWT; omitted from headers when null.
 * @param {AbortSignal} [signal]
 * @returns {Promise<import('apache-arrow').Table>}
 * @throws {Error} on HTTP error or parse failure (caller should catch + sample-fallback)
 */
export async function fetchArrow(backend, queryId, token, signal) {
  const url = `${backend.replace(/\/$/, '')}/api/v1/query`

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/vnd.apache.arrow.stream',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({ query_id: queryId }),
    credentials: 'omit',
    signal,
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status} from ${url}`)
  }

  const buf = await response.arrayBuffer()
  return tableFromIPC(new Uint8Array(buf))
}

// ---------------------------------------------------------------------------
// Sample table builders
// ---------------------------------------------------------------------------

/**
 * A single-row Arrow Table for KPI widget sample fallback.
 * Column "revenue" = 124_500, column "label" = "Sample KPI".
 *
 * @returns {import('apache-arrow').Table}
 */
export function makeSampleKpiTable() {
  return tableFromArrays({
    revenue:   vectorFromArray([124500], new Float64()),
    count:     vectorFromArray([42], new Int32()),
    label:     vectorFromArray(['Sample KPI']),
    category:  vectorFromArray(['demo']),
  })
}

/**
 * A multi-row Arrow Table for table / chart sample fallback.
 *
 * @returns {import('apache-arrow').Table}
 */
export function makeSampleTableData() {
  return tableFromArrays({
    id:       vectorFromArray([1, 2, 3, 4, 5, 6, 7, 8], new Int32()),
    name:     vectorFromArray(['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta']),
    x:        vectorFromArray([1.2, 3.5, 2.1, 4.8, 0.9, 5.3, 3.9, 2.7], new Float64()),
    y:        vectorFromArray([2.4, 1.8, 3.9, 2.2, 4.6, 1.1, 3.3, 4.1], new Float64()),
    value:    vectorFromArray([10.5, 22.3, 7.8, 99.1, 45.0, 33.7, 18.2, 61.4], new Float64()),
    category: vectorFromArray(['A', 'B', 'A', 'C', 'B', 'C', 'A', 'B']),
  })
}

// ---------------------------------------------------------------------------
// DOM utilities (sanitizer-safe — no innerHTML on caller-supplied strings)
// ---------------------------------------------------------------------------

/**
 * Escape HTML special characters for safe insertion into innerHTML.
 *
 * @param {string} str
 * @returns {string}
 */
export function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * Format a single Apache Arrow cell value for display.
 *
 * @param {unknown} val
 * @returns {string}
 */
export function formatCell(val) {
  if (val === null || val === undefined) return ''
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'bigint') return val.toString()
  if (val instanceof Date) return val.toISOString()
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

/**
 * Tiny sanitizer-safe DOM element builder.
 * All attribute values are set via setAttribute (safe); text children are
 * set via textContent (never innerHTML).
 *
 * @param {string} tag
 * @param {Record<string,string>} [attrs]
 * @param {(string|Node)[]} [children]
 * @returns {HTMLElement}
 */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, v)
  }
  for (const child of children) {
    if (typeof child === 'string') {
      node.appendChild(document.createTextNode(child))
    } else if (child instanceof Node) {
      node.appendChild(child)
    }
  }
  return node
}

/**
 * Build a simple CSS string that can be injected into Shadow DOM <style>.
 * Shared CSS custom-property baseline used by all widgets.
 */
export const BASE_STYLES = /* css */ `
  :host {
    display: block;
    box-sizing: border-box;
    font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    color: var(--nubi-fg, #e2e8f0);
    background: var(--nubi-bg, #0f1117);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: 8px;
    overflow: hidden;
  }

  .nubi-badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    letter-spacing: 0.04em;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .nubi-badge.sample { background: #422006; color: #fed7aa; }
  .nubi-badge.live   { background: #064e3b; color: #6ee7b7; }
  .nubi-badge.error  { background: #450a0a; color: #fca5a5; }
  .nubi-badge.webgl  { background: #1e1b4b; color: #a5b4fc; }
  .nubi-badge.svg    { background: #1e3a5f; color: #93c5fd; }

  .nubi-sample-note {
    font-size: 11px;
    color: #f97316;
    padding: 3px 10px 4px;
    background: #1a1208;
    border-bottom: 1px solid #7c2d12;
    text-align: center;
    display: none;
  }
  .nubi-sample-note.visible { display: block; }

  .nubi-loading {
    padding: 24px;
    text-align: center;
    opacity: 0.5;
    font-size: 13px;
  }
  .nubi-loading::after {
    content: '';
    display: inline-block;
    width: 12px; height: 12px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    vertical-align: -2px;
    margin-left: 6px;
    animation: nubi-spin 0.8s linear infinite;
  }
  @keyframes nubi-spin { to { transform: rotate(360deg); } }
`
