/**
 * nubi-dashboard.js — <nubi-dashboard> read-only embed web component.
 *
 * M3-C: framework-agnostic custom element. Bundles apache-arrow so the script
 * is completely drop-in (no extra imports on the host page).
 *
 * ATTRIBUTES
 * ----------
 * query         SQL string or a named query id. Takes precedence over
 *               `dashboard-id` if both are set.
 * dashboard-id  A dashboard (board) id. The element fetches the read-only
 *               descriptor from GET /api/v1/embed/config/{id} and renders
 *               each widget that references a registered query id.
 * token      Static JWT string. If absent, `get-token` is used instead.
 * get-token  Name of a function on `window` that returns Promise<string> | string.
 * backend    Base URL of the Nubi API, e.g. "https://api.example.com".
 * theme      Optional preset name (reserved; theming is via CSS custom props).
 *
 * CSS CUSTOM PROPERTIES (set on a parent or :root)
 * -------------------------------------------------
 * --nubi-bg        Background colour of the table wrapper.  Default: #0f1117
 * --nubi-fg        Primary foreground / text colour.         Default: #e2e8f0
 * --nubi-accent    Header row background.                    Default: #1e2433
 * --nubi-border    Cell / table border colour.               Default: #2d3748
 *
 * EVENTS
 * ------
 * nubi:ready      — fired after a successful render; detail: { rowCount }
 * nubi:error      — fired on any non-recoverable error;  detail: { message }
 * nubi:query-run  — fired after each query attempt (hit or sample fallback);
 *                   detail: { rowCount, cacheStatus, elapsedMs, sample }
 *
 * SAMPLE FALLBACK
 * ---------------
 * On ANY failure (network, auth, parse) the component renders an inline sample
 * table and adds a small "preview (sample data)" badge so the demo always shows
 * something meaningful.
 */

import { tableFromIPC, tableFromArrays, vectorFromArray, Int32, Float64 } from 'apache-arrow'

// ---------------------------------------------------------------------------
// Inline sample data — rendered when the real backend is unreachable
// ---------------------------------------------------------------------------
const SAMPLE_TABLE = tableFromArrays({
  id:       vectorFromArray([1, 2, 3, 4, 5], new Int32()),
  name:     vectorFromArray(['alpha', 'beta', 'gamma', 'delta', 'epsilon']),
  value:    vectorFromArray([10.5, 22.3, 7.8, 99.1, 45.0], new Float64()),
  active:   vectorFromArray([true, false, true, true, false]),
  category: vectorFromArray(['A', 'B', 'A', 'C', 'B']),
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Decode the payload of a JWT without signature verification.
 * Returns null if the token is malformed.
 *
 * @param {string} token
 * @returns {{ exp?: number } | null}
 */
function decodeJwtPayload(token) {
  try {
    const [, payloadB64] = token.split('.')
    // Base64url → Base64
    const b64 = payloadB64.replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(b64)
    return JSON.parse(json)
  } catch {
    return null
  }
}

/**
 * Render an Arrow Table as an HTML string (first `maxRows` rows).
 * Returns a <table> string safe to set as innerHTML inside the shadow root.
 *
 * @param {import('apache-arrow').Table} table
 * @param {number} [maxRows=100]
 * @returns {string}
 */
function arrowTableToHTML(table, maxRows = 100) {
  const schema = table.schema
  const colNames = schema.fields.map(f => f.name)

  // Header row
  const thead = `<thead><tr>${
    colNames.map(n => `<th>${escapeHtml(String(n))}</th>`).join('')
  }</tr></thead>`

  // Body rows (cap at maxRows)
  const rowCount = Math.min(table.numRows, maxRows)
  const bodyRows = []
  for (let r = 0; r < rowCount; r++) {
    const cells = colNames.map(col => {
      const val = table.getChild(col)?.get(r)
      return `<td>${escapeHtml(formatCell(val))}</td>`
    })
    bodyRows.push(`<tr>${cells.join('')}</tr>`)
  }
  const tbody = `<tbody>${bodyRows.join('')}</tbody>`

  return `<table>${thead}${tbody}</table>`
}

/**
 * Format a single Arrow cell value for display.
 * @param {unknown} val
 * @returns {string}
 */
function formatCell(val) {
  if (val === null || val === undefined) return ''
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'bigint') return val.toString()
  if (val instanceof Date) return val.toISOString()
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

/**
 * Escape HTML special characters for safe insertion into innerHTML.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// ---------------------------------------------------------------------------
// Shadow DOM styles
// ---------------------------------------------------------------------------
const STYLES = /* css */ `
  :host {
    display: block;
    box-sizing: border-box;
    font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    font-size: 13px;
    color: var(--nubi-fg, #e2e8f0);
    background: var(--nubi-bg, #0f1117);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: 8px;
    overflow: hidden;
  }

  .nubi-wrap {
    width: 100%;
    height: 100%;
    overflow: auto;
    position: relative;
  }

  .nubi-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: var(--nubi-accent, #1e2433);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    font-size: 11px;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.8;
    gap: 8px;
  }

  .nubi-toolbar .nubi-title {
    font-weight: 600;
    letter-spacing: 0.02em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
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

  .nubi-badge.hit  { background: #064e3b; color: #6ee7b7; }
  .nubi-badge.miss { background: #1e3a5f; color: #93c5fd; }
  .nubi-badge.sample {
    background: #422006;
    color: #fed7aa;
  }

  .nubi-sample-note {
    font-size: 11px;
    color: #f97316;
    padding: 4px 12px 6px;
    background: #1a1208;
    border-bottom: 1px solid #7c2d12;
    text-align: center;
  }

  .nubi-table-wrap {
    overflow: auto;
    max-height: calc(100% - 72px);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    line-height: 1.4;
  }

  thead tr {
    background: var(--nubi-accent, #1e2433);
    position: sticky;
    top: 0;
    z-index: 1;
  }

  thead th {
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.7;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    white-space: nowrap;
  }

  tbody tr {
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    transition: background 0.1s;
  }

  tbody tr:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  tbody td {
    padding: 6px 10px;
    color: var(--nubi-fg, #e2e8f0);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .nubi-widget-title {
    margin: 0;
    padding: 10px 12px 6px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.03em;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.85;
  }

  .nubi-widget-error {
    padding: 8px 12px;
    font-size: 11px;
    color: #f87171;
  }

  .nubi-empty {
    padding: 32px;
    text-align: center;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.55;
    font-size: 12px;
  }

  .nubi-loading {
    padding: 32px;
    text-align: center;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.5;
  }

  .nubi-loading::after {
    content: '';
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    vertical-align: -3px;
    margin-left: 8px;
    animation: nubi-spin 0.8s linear infinite;
  }

  @keyframes nubi-spin {
    to { transform: rotate(360deg); }
  }

  .nubi-error-msg {
    padding: 16px;
    color: #f87171;
    font-size: 12px;
    background: #1c0a0a;
    border-radius: 4px;
    margin: 8px;
  }

  .nubi-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 4px 10px;
    font-size: 10px;
    opacity: 0.45;
    border-top: 1px solid var(--nubi-border, #2d3748);
    gap: 8px;
  }
`

// ---------------------------------------------------------------------------
// NubiDashboard — the custom element
// ---------------------------------------------------------------------------

class NubiDashboard extends HTMLElement {
  // ---- Custom element lifecycle ------------------------------------------

  static get observedAttributes() {
    return ['query', 'dashboard-id', 'token', 'get-token', 'backend', 'theme']
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._abortController = null
    this._rendering = false
  }

  connectedCallback() {
    this._render()
  }

  disconnectedCallback() {
    this._abort()
  }

  attributeChangedCallback(_name, oldVal, newVal) {
    if (oldVal !== newVal && this.isConnected) {
      this._render()
    }
  }

  // ---- Internal helpers --------------------------------------------------

  _abort() {
    if (this._abortController) {
      this._abortController.abort()
      this._abortController = null
    }
  }

  /**
   * Resolve a JWT token from:
   *  1. The `token` attribute (static string).
   *  2. The `get-token` attribute — a function name on `window`.
   *
   * @returns {Promise<string | null>}
   */
  async _resolveToken() {
    const staticToken = this.getAttribute('token')
    if (staticToken) return staticToken

    const fnName = this.getAttribute('get-token')
    if (!fnName) return null

    const fn = window[fnName]
    if (typeof fn !== 'function') {
      console.warn(`[nubi-dashboard] window.${fnName} is not a function`)
      return null
    }

    try {
      const tok = await fn()
      return tok ?? null
    } catch (err) {
      console.warn('[nubi-dashboard] getToken() threw:', err.message)
      return null
    }
  }

  /** @returns {string} */
  _backendUrl() {
    // Hosts sometimes paste a backend URL that already ends in /api/v1; the
    // element appends /api/v1 itself, so strip the suffix to avoid doubling.
    return (this.getAttribute('backend') || 'http://localhost:8000')
      .replace(/\/+$/, '')
      .replace(/\/api\/v1$/, '')
  }

  // ---- DOM helpers -------------------------------------------------------

  /** Show the loading spinner. */
  _showLoading() {
    const wrap = this._shadow.querySelector('.nubi-table-wrap')
    if (wrap) wrap.innerHTML = '<div class="nubi-loading">Running query</div>'
    const sampleNote = this._shadow.querySelector('.nubi-sample-note')
    if (sampleNote) sampleNote.style.display = 'none'
  }

  /** Render table data into the shadow DOM. */
  _showTable(table, { cacheStatus = 'MISS', elapsedMs = 0, isSample = false } = {}) {
    // Toolbar badge
    const badge = this._shadow.querySelector('.nubi-badge')
    if (badge) {
      if (isSample) {
        badge.textContent = 'SAMPLE'
        badge.className = 'nubi-badge sample'
      } else if (cacheStatus === 'HIT') {
        badge.textContent = 'CACHE HIT'
        badge.className = 'nubi-badge hit'
      } else {
        badge.textContent = 'LIVE'
        badge.className = 'nubi-badge miss'
      }
    }

    // Sample note banner
    const sampleNote = this._shadow.querySelector('.nubi-sample-note')
    if (sampleNote) {
      sampleNote.style.display = isSample ? 'block' : 'none'
    }

    // Table
    const wrap = this._shadow.querySelector('.nubi-table-wrap')
    if (wrap) {
      wrap.innerHTML = arrowTableToHTML(table, 100)
    }

    // Footer
    const footer = this._shadow.querySelector('.nubi-footer')
    if (footer) {
      footer.textContent = `${table.numRows.toLocaleString()} row${table.numRows !== 1 ? 's' : ''} · ${elapsedMs}ms`
    }
  }

  /** Set the toolbar title text (truncated like the scaffold default). */
  _setTitle(text) {
    const titleEl = this._shadow.querySelector('.nubi-title')
    if (!titleEl) return
    const str = String(text ?? '')
    titleEl.textContent = str.length > 60 ? str.slice(0, 57) + '…' : str
  }

  /** Show an error message (only used as last resort; usually we fall to sample). */
  _showError(msg) {
    const wrap = this._shadow.querySelector('.nubi-table-wrap')
    if (wrap) {
      wrap.innerHTML = `<div class="nubi-error-msg">Error: ${escapeHtml(msg)}</div>`
    }
  }

  // ---- Shadow DOM scaffold -----------------------------------------------

  _ensureScaffold() {
    if (this._shadow.querySelector('.nubi-wrap')) return

    const style = document.createElement('style')
    style.textContent = STYLES

    const queryLabel = this.getAttribute('query')
      || this.getAttribute('dashboard-id')
      || 'Query'
    const titleText = queryLabel.length > 60
      ? queryLabel.slice(0, 57) + '…'
      : queryLabel

    this._shadow.innerHTML = ''
    this._shadow.appendChild(style)

    this._shadow.innerHTML += /* html */ `
      <div class="nubi-wrap">
        <div class="nubi-toolbar">
          <span class="nubi-title">${escapeHtml(titleText)}</span>
          <span class="nubi-badge miss">…</span>
        </div>
        <div class="nubi-sample-note" style="display:none">
          preview (sample data) — connect a backend to load real results
        </div>
        <div class="nubi-table-wrap">
          <div class="nubi-loading">Running query</div>
        </div>
        <div class="nubi-footer"></div>
      </div>
    `
    // Re-attach the style node (innerHTML clobber removed it)
    this._shadow.insertBefore(style, this._shadow.firstChild)
  }

  // ---- Dashboard descriptor render -----------------------------------------

  /**
   * Fetch the embed descriptor for `dashboard-id` and render its widgets.
   *
   * Each widget that references a registered query id is executed via
   * POST /api/v1/query ({ query_id }) and rendered as a stacked section with
   * the widget's title as a heading. Returns `true` when the descriptor was
   * rendered (including the empty state); `false` on a config fetch/auth
   * failure so the caller can fall back to the sample table.
   *
   * @param {{ ac: AbortController, t0: number, backend: string,
   *           token: string | null, dashboardId: string }} opts
   * @returns {Promise<boolean>}
   */
  async _renderDashboard({ ac, t0, backend, token, dashboardId }) {
    const jsonHeaders = { 'Accept': 'application/json' }
    const queryHeaders = {
      'Content-Type': 'application/json',
      'Accept': 'application/vnd.apache.arrow.stream',
    }
    if (token) {
      jsonHeaders['Authorization'] = `Bearer ${token}`
      queryHeaders['Authorization'] = `Bearer ${token}`
    }

    // --- Fetch the read-only descriptor --------------------------------------
    let descriptor
    try {
      const response = await fetch(`${backend}/api/v1/embed/config/${encodeURIComponent(dashboardId)}`, {
        method: 'GET',
        headers: jsonHeaders,
        credentials: 'omit',
        signal: ac.signal,
      })
      if (ac.signal.aborted) return true

      if (!response.ok) {
        const httpMsg = `Embed config API returned HTTP ${response.status}`
        console.warn(`[nubi-dashboard] ${httpMsg} — showing sample`)
        this.dispatchEvent(new CustomEvent('nubi:error', {
          bubbles: true, composed: true,
          detail: { message: httpMsg },
        }))
        return false
      }

      descriptor = await response.json()
    } catch (err) {
      if (err.name === 'AbortError') return true
      console.warn('[nubi-dashboard] Embed config fetch error — showing sample:', err.message)
      this.dispatchEvent(new CustomEvent('nubi:error', {
        bubbles: true, composed: true,
        detail: { message: err.message },
      }))
      return false
    }

    if (ac.signal.aborted) return true

    this._setTitle(descriptor.title || dashboardId)

    // --- Widgets that reference a registered query id -------------------------
    const widgets = Array.isArray(descriptor.widgets) ? descriptor.widgets : []
    const renderable = widgets.filter(w => w && (w.query_id || w.props?.query_id))

    if (renderable.length === 0) {
      const wrap = this._shadow.querySelector('.nubi-table-wrap')
      if (wrap) {
        wrap.innerHTML = '<div class="nubi-empty">This dashboard has no embeddable widgets yet — add a widget backed by a registered query to see data here.</div>'
      }
      const badge = this._shadow.querySelector('.nubi-badge')
      if (badge) {
        badge.textContent = 'EMPTY'
        badge.className = 'nubi-badge miss'
      }
      const footer = this._shadow.querySelector('.nubi-footer')
      if (footer) footer.textContent = '0 widgets'

      this.dispatchEvent(new CustomEvent('nubi:ready', {
        bubbles: true, composed: true,
        detail: { rowCount: 0 },
      }))
      return true
    }

    // --- Run each widget's query and stack the results ------------------------
    const sections = []
    let totalRows = 0
    let anyMiss = false

    for (let i = 0; i < renderable.length; i++) {
      const widget = renderable[i]
      const queryId = widget.query_id || widget.props?.query_id
      const widgetTitle = widget.title || widget.props?.title || `Widget ${i + 1}`
      const heading = `<h3 class="nubi-widget-title">${escapeHtml(String(widgetTitle))}</h3>`

      try {
        const response = await fetch(`${backend}/api/v1/query`, {
          method: 'POST',
          headers: queryHeaders,
          body: JSON.stringify({ query_id: queryId }),
          credentials: 'omit',
          signal: ac.signal,
        })
        if (ac.signal.aborted) return true

        if (!response.ok) {
          throw new Error(`Query API returned HTTP ${response.status}`)
        }

        const cacheStatus = response.headers.get('X-Nubi-Cache') ?? 'MISS'
        if (cacheStatus !== 'HIT') anyMiss = true

        const buf = await response.arrayBuffer()
        if (ac.signal.aborted) return true

        const table = tableFromIPC(new Uint8Array(buf))
        totalRows += table.numRows
        sections.push(heading + arrowTableToHTML(table, 100))
      } catch (err) {
        if (err.name === 'AbortError') return true
        console.warn(`[nubi-dashboard] Widget query ${queryId} failed:`, err.message)
        sections.push(`${heading}<div class="nubi-widget-error">Failed to load: ${escapeHtml(err.message)}</div>`)
        anyMiss = true
      }
    }

    const elapsedMs = Math.round(performance.now() - t0)

    const wrap = this._shadow.querySelector('.nubi-table-wrap')
    if (wrap) wrap.innerHTML = sections.join('')

    const sampleNote = this._shadow.querySelector('.nubi-sample-note')
    if (sampleNote) sampleNote.style.display = 'none'

    const badge = this._shadow.querySelector('.nubi-badge')
    if (badge) {
      badge.textContent = anyMiss ? 'LIVE' : 'CACHE HIT'
      badge.className = anyMiss ? 'nubi-badge miss' : 'nubi-badge hit'
    }

    const footer = this._shadow.querySelector('.nubi-footer')
    if (footer) {
      footer.textContent = `${renderable.length} widget${renderable.length !== 1 ? 's' : ''} · ${totalRows.toLocaleString()} row${totalRows !== 1 ? 's' : ''} · ${elapsedMs}ms`
    }

    this.dispatchEvent(new CustomEvent('nubi:query-run', {
      bubbles: true, composed: true,
      detail: { rowCount: totalRows, cacheStatus: anyMiss ? 'MISS' : 'HIT', elapsedMs, sample: false },
    }))
    this.dispatchEvent(new CustomEvent('nubi:ready', {
      bubbles: true, composed: true,
      detail: { rowCount: totalRows },
    }))
    return true
  }

  // ---- Core render -------------------------------------------------------

  async _render() {
    // Debounce: if already rendering, abort the in-flight fetch
    this._abort()

    const ac = new AbortController()
    this._abortController = ac
    this._rendering = true

    this._ensureScaffold()
    this._showLoading()

    const t0 = performance.now()
    const sql = this.getAttribute('query') || ''
    const dashboardId = this.getAttribute('dashboard-id') || ''
    const backend = this._backendUrl()

    // --- Resolve token -------------------------------------------------------
    let token
    try {
      token = await this._resolveToken()
    } catch (err) {
      token = null
    }

    if (ac.signal.aborted) return

    // --- Dashboard descriptor path (query attribute takes precedence) ---------
    if (!sql && dashboardId && backend) {
      const ok = await this._renderDashboard({ ac, t0, backend, token, dashboardId })
      if (ok || ac.signal.aborted) {
        this._rendering = false
        return
      }
      // Config fetch/auth failure — fall through to the sample fallback below.
    }

    // --- Attempt real fetch --------------------------------------------------
    if (sql && backend) {
      try {
        const headers = {
          'Content-Type': 'application/json',
          'Accept': 'application/vnd.apache.arrow.stream',
        }
        if (token) {
          headers['Authorization'] = `Bearer ${token}`
        }

        const response = await fetch(`${backend}/api/v1/query`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ sql }),
          // credentials: 'omit' — cross-origin embed; no cookies sent
          credentials: 'omit',
          signal: ac.signal,
        })

        if (ac.signal.aborted) return

        if (response.ok) {
          const cacheStatus = response.headers.get('X-Nubi-Cache') ?? 'MISS'
          const buf = await response.arrayBuffer()
          if (ac.signal.aborted) return

          const table = tableFromIPC(new Uint8Array(buf))
          const elapsedMs = Math.round(performance.now() - t0)

          this._showTable(table, { cacheStatus, elapsedMs, isSample: false })

          this.dispatchEvent(new CustomEvent('nubi:query-run', {
            bubbles: true, composed: true,
            detail: { rowCount: table.numRows, cacheStatus, elapsedMs, sample: false },
          }))
          this.dispatchEvent(new CustomEvent('nubi:ready', {
            bubbles: true, composed: true,
            detail: { rowCount: table.numRows },
          }))
          this._rendering = false
          return
        }

        // Non-OK response — emit nubi:error then fall through to sample
        const httpMsg = `Query API returned HTTP ${response.status}`
        console.warn(`[nubi-dashboard] ${httpMsg} — showing sample`)
        this.dispatchEvent(new CustomEvent('nubi:error', {
          bubbles: true, composed: true,
          detail: { message: httpMsg },
        }))

      } catch (err) {
        if (err.name === 'AbortError') return
        // Network or parse error — emit nubi:error then fall through to sample
        console.warn('[nubi-dashboard] Fetch/parse error — showing sample:', err.message)
        this.dispatchEvent(new CustomEvent('nubi:error', {
          bubbles: true, composed: true,
          detail: { message: err.message },
        }))
      }
    }

    if (ac.signal.aborted) return

    // --- Sample fallback (always renders) ------------------------------------
    const elapsedMs = Math.round(performance.now() - t0)
    this._showTable(SAMPLE_TABLE, { cacheStatus: 'SAMPLE', elapsedMs, isSample: true })

    this.dispatchEvent(new CustomEvent('nubi:query-run', {
      bubbles: true, composed: true,
      detail: { rowCount: SAMPLE_TABLE.numRows, cacheStatus: 'SAMPLE', elapsedMs, sample: true },
    }))
    this.dispatchEvent(new CustomEvent('nubi:ready', {
      bubbles: true, composed: true,
      detail: { rowCount: SAMPLE_TABLE.numRows },
    }))

    this._rendering = false
  }
}

// ---------------------------------------------------------------------------
// Register the custom element
// ---------------------------------------------------------------------------
customElements.define('nubi-dashboard', NubiDashboard)

export { NubiDashboard }
