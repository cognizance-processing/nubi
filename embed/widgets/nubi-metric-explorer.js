/**
 * nubi-metric-explorer.js — Governed metric query builder.
 *
 * <nubi-metric-explorer> custom element.
 *
 * No raw SQL surface. Provides a UI to pick a metric, dimensions, and time
 * grain, then runs a governed metric query via the API.
 *
 * Attributes
 * ----------
 *  token       — static JWT string
 *  get-token   — name of a window.* function returning a JWT
 *  backend     — API base URL (default 'http://localhost:8000')
 *  metric-id   — pre-select a metric by ID
 *  dimensions  — comma-separated default selected dimensions
 *  theme       — 'dark' | 'light' (default 'dark')
 *
 * Capability gating
 * -----------------
 *  author:metric → controls enabled, Run button visible
 *  read-only (no author:metric) → controls shown but disabled; read-only indicator
 *
 * Events emitted (bubbles, composed)
 * ------------------------------------
 *  nubi:run    — { metricId, dimensions, timeGrain }
 *  nubi:select — { column, value, row }
 *  nubi:error  — { message, code }
 */

import { resolveToken, fetchArrow, escapeHtml, formatCell, el, BASE_STYLES } from './shared.js'
import { decodeScopes, hasScope }    from '../nubi-context.js'
import { emitRun, emitSelect, emitError } from '../events.js'
import { applyTheme }                from '../theme.js'
import { tableFromIPC }              from 'apache-arrow'

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const EXPLORER_STYLES = /* css */ `
  ${BASE_STYLES}

  :host {
    display: flex;
    flex-direction: column;
    min-height: 200px;
  }

  .nubi-me-wrap {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  /* Toolbar */
  .nubi-me-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 12px;
    height: var(--nubi-toolbar-h, 36px);
    background: var(--nubi-bg-2, #1a1f2e);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    flex-shrink: 0;
  }
  .metric-name {
    font-size: var(--nubi-font-size-base, 13px);
    font-weight: 600;
    color: var(--nubi-fg, #e2e8f0);
    flex: 1;
  }
  .scope-indicator {
    font-size: var(--nubi-font-size-xs, 10px);
    padding: 2px 7px;
    border-radius: var(--nubi-radius-sm, 4px);
    font-weight: 700;
    letter-spacing: 0.05em;
  }
  .scope-indicator.metric { background: #064e3b; color: #6ee7b7; }
  .scope-indicator.readonly { background: #450a0a; color: #fca5a5; }

  /* Controls */
  .nubi-me-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    padding: 12px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    background: var(--nubi-bg-2, #1a1f2e);
    flex-shrink: 0;
  }
  .control-group {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 140px;
  }
  .control-label {
    font-size: var(--nubi-font-size-xs, 10px);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--nubi-fg-muted, #718096);
  }
  .me-select {
    padding: 5px 8px;
    background: var(--nubi-bg, #0f1117);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: var(--nubi-radius-sm, 4px);
    color: var(--nubi-fg, #e2e8f0);
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .me-select:disabled { opacity: 0.4; cursor: not-allowed; }
  .me-select[multiple] { height: 70px; }

  .dim-checkboxes {
    display: flex;
    flex-direction: column;
    gap: 3px;
    max-height: 100px;
    overflow-y: auto;
  }
  .dim-check-label {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: var(--nubi-font-size-sm, 11px);
    color: var(--nubi-fg, #e2e8f0);
    cursor: pointer;
  }
  .dim-check-label input:disabled { cursor: not-allowed; }
  .dim-check-label input:disabled + span { opacity: 0.4; }

  .btn-run-me {
    align-self: flex-end;
    padding: 5px 16px;
    font-size: var(--nubi-font-size-sm, 11px);
    font-weight: 600;
    border-radius: var(--nubi-radius-sm, 4px);
    border: none;
    cursor: pointer;
    background: var(--nubi-primary, #6366f1);
    color: var(--nubi-primary-fg, #fff);
    transition: var(--nubi-transition, 0.15s ease);
  }
  .btn-run-me:hover { filter: brightness(1.15); }
  .btn-run-me:disabled { opacity: 0.35; cursor: not-allowed; }

  /* Results */
  .nubi-me-results {
    flex: 1;
    overflow: auto;
    padding: 0;
  }
  .me-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .me-table th {
    position: sticky;
    top: 0;
    background: var(--nubi-bg-2, #1a1f2e);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    padding: 6px 10px;
    text-align: left;
    font-weight: 600;
    color: var(--nubi-fg-muted, #718096);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: var(--nubi-font-size-xs, 10px);
  }
  .me-table td {
    padding: 5px 10px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    color: var(--nubi-fg, #e2e8f0);
    cursor: pointer;
  }
  .me-table tr:hover td { background: var(--nubi-accent, #1e2433); }
  .me-empty {
    padding: 32px;
    text-align: center;
    color: var(--nubi-fg-muted, #718096);
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .me-loading {
    padding: 24px;
    text-align: center;
    color: var(--nubi-fg-muted, #718096);
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .me-error {
    padding: 12px 16px;
    background: #1a0808;
    border: 1px solid var(--nubi-error, #ef4444);
    border-radius: var(--nubi-radius-sm, 4px);
    color: var(--nubi-error, #ef4444);
    font-size: var(--nubi-font-size-sm, 11px);
    margin: 12px;
  }

  /* Footer */
  .nubi-me-footer {
    display: flex;
    align-items: center;
    padding: 4px 12px;
    height: 24px;
    font-size: var(--nubi-font-size-xs, 10px);
    color: var(--nubi-fg-muted, #718096);
    background: var(--nubi-bg-2, #1a1f2e);
    border-top: 1px solid var(--nubi-border, #2d3748);
    flex-shrink: 0;
  }

  /* No-metric placeholder */
  .me-no-metric {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--nubi-fg-muted, #718096);
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .no-scope-banner {
    margin: 12px;
    padding: 10px 14px;
    background: #1a0808;
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: var(--nubi-radius-sm, 4px);
    color: var(--nubi-fg-muted, #718096);
    font-size: var(--nubi-font-size-sm, 11px);
  }
`

// ---------------------------------------------------------------------------
// Default metric definitions (used when backend not configured or as fallback)
// ---------------------------------------------------------------------------

const DEFAULT_METRICS = [
  {
    id: 'revenue',
    name: 'Revenue',
    dimensions: ['date', 'region', 'product', 'channel'],
    timeGrains: ['day', 'week', 'month', 'quarter', 'year'],
  },
  {
    id: 'orders',
    name: 'Orders',
    dimensions: ['date', 'region', 'channel'],
    timeGrains: ['day', 'week', 'month'],
  },
  {
    id: 'active_users',
    name: 'Active Users',
    dimensions: ['date', 'region', 'platform'],
    timeGrains: ['day', 'week', 'month'],
  },
]

// ---------------------------------------------------------------------------
// Custom element
// ---------------------------------------------------------------------------

export class NubiMetricExplorer extends HTMLElement {
  static get observedAttributes() {
    return ['token', 'get-token', 'backend', 'metric-id', 'dimensions', 'theme']
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null
    this._scopes = []
    this._metricDef = null
    this._resultTable = null
  }

  // --------------------------------------------------------------------------
  // Lifecycle
  // --------------------------------------------------------------------------

  connectedCallback() {
    applyTheme(this, this.getAttribute('theme') || 'dark')
    this._ensureScaffold()
    this._load()
  }

  disconnectedCallback() {
    this._abort()
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (oldVal === newVal) return
    if (name === 'theme') applyTheme(this, newVal || 'dark')
    if (this.isConnected) this._load()
  }

  // --------------------------------------------------------------------------
  // Shadow DOM scaffold
  // --------------------------------------------------------------------------

  _ensureScaffold() {
    if (this._shadow.querySelector('.nubi-me-wrap')) return

    const style = document.createElement('style')
    style.textContent = EXPLORER_STYLES
    this._shadow.appendChild(style)

    const wrap = document.createElement('div')
    wrap.className = 'nubi-me-wrap'
    wrap.innerHTML = /* html */ `
      <div class="nubi-me-toolbar">
        <span class="metric-name">Metric Explorer</span>
        <span class="scope-indicator readonly">READ-ONLY</span>
      </div>
      <div class="nubi-me-controls" style="display:none"></div>
      <div class="nubi-me-results">
        <div class="me-empty">Select a metric to explore.</div>
      </div>
      <div class="nubi-me-footer"></div>
    `
    this._shadow.appendChild(wrap)
  }

  // --------------------------------------------------------------------------
  // Load: resolve token, decode scopes, load metric definition
  // --------------------------------------------------------------------------

  async _load() {
    this._abort()
    this._ac = new AbortController()

    try {
      const token = await resolveToken(this)
      this._scopes = decodeScopes(token)
      await this._loadMetric(token)
    } catch (err) {
      if (err.name === 'AbortError') return
      this._scopes = []
      this._renderControls(false)
      emitError(this, { message: err.message })
    }
  }

  async _loadMetric(token) {
    const metricId = this.getAttribute('metric-id') || ''
    const backend  = (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')
    const canAuthor = hasScope(this._scopes, 'author:metric')

    // Update scope indicator
    const ind = this._shadow.querySelector('.scope-indicator')
    ind.className = `scope-indicator ${canAuthor ? 'metric' : 'readonly'}`
    ind.textContent = canAuthor ? 'METRIC' : 'READ-ONLY'

    // Try to fetch metric definition from API; fall back to defaults
    if (metricId && token) {
      try {
        const resp = await fetch(`${backend}/api/v1/metrics/${encodeURIComponent(metricId)}`, {
          headers: { 'Authorization': `Bearer ${token}` },
          signal: this._ac?.signal,
        })
        if (resp.ok) {
          this._metricDef = await resp.json()
        }
      } catch { /* fall through to defaults */ }
    }

    if (!this._metricDef) {
      // Find in defaults or use first
      this._metricDef = DEFAULT_METRICS.find(m => m.id === metricId) || DEFAULT_METRICS[0]
    }

    this._renderControls(canAuthor)
  }

  // --------------------------------------------------------------------------
  // Controls
  // --------------------------------------------------------------------------

  _renderControls(canAuthor) {
    const controls = this._shadow.querySelector('.nubi-me-controls')
    const metricName = this._shadow.querySelector('.metric-name')
    if (!controls) return

    const def = this._metricDef
    if (!def) {
      controls.style.display = 'none'
      return
    }

    metricName.textContent = def.name || 'Metric Explorer'
    controls.style.display = ''

    // Build controls HTML safely
    controls.innerHTML = ''

    // No-scope banner
    if (!canAuthor) {
      const banner = document.createElement('div')
      banner.className = 'no-scope-banner'
      banner.textContent = 'Metric authoring requires the author:metric scope. Showing read-only view.'
      controls.appendChild(banner)
    }

    // Metric picker
    const metricGroup = document.createElement('div')
    metricGroup.className = 'control-group'
    const metricLabel = document.createElement('div')
    metricLabel.className = 'control-label'
    metricLabel.textContent = 'Metric'
    const metricSelect = document.createElement('select')
    metricSelect.className = 'me-select'
    metricSelect.disabled = !canAuthor
    metricSelect.dataset.role = 'metric'
    DEFAULT_METRICS.forEach(m => {
      const opt = document.createElement('option')
      opt.value = m.id
      opt.textContent = m.name
      opt.selected = m.id === def.id
      metricSelect.appendChild(opt)
    })
    metricSelect.addEventListener('change', () => this._onMetricChange(metricSelect.value))
    metricGroup.appendChild(metricLabel)
    metricGroup.appendChild(metricSelect)
    controls.appendChild(metricGroup)

    // Dimension pickers (checkboxes)
    const dimGroup = document.createElement('div')
    dimGroup.className = 'control-group'
    const dimLabel = document.createElement('div')
    dimLabel.className = 'control-label'
    dimLabel.textContent = 'Dimensions'
    dimGroup.appendChild(dimLabel)
    const dimBoxes = document.createElement('div')
    dimBoxes.className = 'dim-checkboxes'
    dimBoxes.dataset.role = 'dimensions'
    const preselected = (this.getAttribute('dimensions') || '').split(',').filter(Boolean)
    ;(def.dimensions || []).forEach(dim => {
      const lbl = document.createElement('label')
      lbl.className = 'dim-check-label'
      const chk = document.createElement('input')
      chk.type = 'checkbox'
      chk.value = dim
      chk.checked = preselected.includes(dim) || preselected.length === 0
      chk.disabled = !canAuthor
      const span = document.createElement('span')
      span.textContent = dim
      lbl.appendChild(chk)
      lbl.appendChild(span)
      dimBoxes.appendChild(lbl)
    })
    dimGroup.appendChild(dimBoxes)
    controls.appendChild(dimGroup)

    // Time grain
    const grainGroup = document.createElement('div')
    grainGroup.className = 'control-group'
    const grainLabel = document.createElement('div')
    grainLabel.className = 'control-label'
    grainLabel.textContent = 'Time Grain'
    const grainSelect = document.createElement('select')
    grainSelect.className = 'me-select'
    grainSelect.disabled = !canAuthor
    grainSelect.dataset.role = 'time-grain'
    ;(def.timeGrains || ['day', 'week', 'month']).forEach(g => {
      const opt = document.createElement('option')
      opt.value = g
      opt.textContent = g.charAt(0).toUpperCase() + g.slice(1)
      grainSelect.appendChild(opt)
    })
    grainGroup.appendChild(grainLabel)
    grainGroup.appendChild(grainSelect)
    controls.appendChild(grainGroup)

    // Run button
    if (canAuthor) {
      const runBtn = document.createElement('button')
      runBtn.className = 'btn-run-me'
      runBtn.textContent = 'Run'
      runBtn.dataset.role = 'run'
      runBtn.addEventListener('click', () => this._run())
      controls.appendChild(runBtn)
    }
  }

  _onMetricChange(newId) {
    this._metricDef = DEFAULT_METRICS.find(m => m.id === newId) || DEFAULT_METRICS[0]
    this._shadow.querySelector('.metric-name').textContent = this._metricDef.name
    // Re-render dimension checkboxes
    const dimBoxes = this._shadow.querySelector('[data-role="dimensions"]')
    if (dimBoxes) {
      dimBoxes.innerHTML = ''
      ;(this._metricDef.dimensions || []).forEach(dim => {
        const lbl = document.createElement('label')
        lbl.className = 'dim-check-label'
        const chk = document.createElement('input')
        chk.type = 'checkbox'
        chk.value = dim
        chk.checked = true
        const span = document.createElement('span')
        span.textContent = dim
        lbl.appendChild(chk)
        lbl.appendChild(span)
        dimBoxes.appendChild(lbl)
      })
    }
    // Update time grains
    const grainSelect = this._shadow.querySelector('[data-role="time-grain"]')
    if (grainSelect) {
      grainSelect.innerHTML = ''
      ;(this._metricDef.timeGrains || ['day', 'week', 'month']).forEach(g => {
        const opt = document.createElement('option')
        opt.value = g
        opt.textContent = g.charAt(0).toUpperCase() + g.slice(1)
        grainSelect.appendChild(opt)
      })
    }
  }

  // --------------------------------------------------------------------------
  // Run
  // --------------------------------------------------------------------------

  _getSelections() {
    const controls = this._shadow.querySelector('.nubi-me-controls')
    const metricSelect = controls?.querySelector('[data-role="metric"]')
    const dimBoxes     = controls?.querySelector('[data-role="dimensions"]')
    const grainSelect  = controls?.querySelector('[data-role="time-grain"]')

    const metricId  = metricSelect?.value || this._metricDef?.id || ''
    const dimensions = dimBoxes
      ? [...dimBoxes.querySelectorAll('input[type=checkbox]:checked')].map(c => c.value)
      : []
    const timeGrain = grainSelect?.value || 'day'
    return { metricId, dimensions, timeGrain }
  }

  async _run() {
    const { metricId, dimensions, timeGrain } = this._getSelections()
    const backend = (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')

    // Show loading
    const results = this._shadow.querySelector('.nubi-me-results')
    results.innerHTML = '<div class="me-loading">Running metric query…</div>'
    this._setFooter('')

    this._abort()
    this._ac = new AbortController()

    try {
      const token = await resolveToken(this)
      const headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/vnd.apache.arrow.stream',
      }
      if (token) headers['Authorization'] = `Bearer ${token}`

      // POST to the metric query endpoint
      const resp = await fetch(`${backend}/api/v1/metrics/${encodeURIComponent(metricId)}/query`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ dimensions, time_grain: timeGrain }),
        credentials: 'omit',
        signal: this._ac.signal,
      })

      if (!resp.ok) {
        const msg = `HTTP ${resp.status}`
        results.innerHTML = `<div class="me-error">${escapeHtml(msg)}</div>`
        emitError(this, { message: msg, code: String(resp.status) })
        return
      }

      const buf = await resp.arrayBuffer()
      this._resultTable = tableFromIPC(new Uint8Array(buf))
      this._renderResults(this._resultTable)
      emitRun(this, { metricId, dimensions, timeGrain })
    } catch (err) {
      if (err.name === 'AbortError') return
      results.innerHTML = `<div class="me-error">${escapeHtml(err.message)}</div>`
      emitError(this, { message: err.message })
    }
  }

  // --------------------------------------------------------------------------
  // Result rendering
  // --------------------------------------------------------------------------

  _renderResults(table) {
    const results = this._shadow.querySelector('.nubi-me-results')
    if (!table || table.numRows === 0) {
      results.innerHTML = '<div class="me-empty">No results.</div>'
      this._setFooter('0 rows')
      return
    }

    const fields = table.schema.fields.map(f => f.name)
    const tbl = document.createElement('table')
    tbl.className = 'me-table'

    // Header
    const thead = document.createElement('thead')
    const headerRow = document.createElement('tr')
    fields.forEach(f => {
      const th = document.createElement('th')
      th.textContent = f
      headerRow.appendChild(th)
    })
    thead.appendChild(headerRow)
    tbl.appendChild(thead)

    // Body
    const tbody = document.createElement('tbody')
    for (let r = 0; r < table.numRows; r++) {
      const row = document.createElement('tr')
      const rowData = {}
      fields.forEach((f, ci) => {
        const col = table.getChild(f)
        const val = col ? col.get(r) : null
        rowData[f] = val
        const td = document.createElement('td')
        td.textContent = formatCell(val)
        td.addEventListener('click', () => {
          emitSelect(this, { column: f, value: val, row: rowData })
        })
        row.appendChild(td)
      })
      tbody.appendChild(row)
    }
    tbl.appendChild(tbody)

    results.innerHTML = ''
    results.appendChild(tbl)
    this._setFooter(`${table.numRows.toLocaleString()} rows`)
  }

  _setFooter(text) {
    const footer = this._shadow.querySelector('.nubi-me-footer')
    if (footer) footer.textContent = text
  }

  // --------------------------------------------------------------------------
  // Misc helpers
  // --------------------------------------------------------------------------

  _abort() {
    if (this._ac) { this._ac.abort(); this._ac = null }
  }
}
