/**
 * nubi-table.js — <nubi-table> HTML table widget (M8-A).
 *
 * ATTRIBUTES
 * ----------
 * query-id   (required) Registered query id.
 * limit      Max rows to display. Defaults to 100.
 * columns    Optional comma-separated list of column names to show (ordered).
 * token      Static JWT or get-token fn name on window.
 * get-token  Name of a window function returning Promise<string>|string.
 * backend    Base URL of Nubi API. Defaults to http://localhost:8000.
 *
 * CSS CUSTOM PROPERTIES
 * ---------------------
 * --nubi-bg, --nubi-fg, --nubi-accent, --nubi-border
 *
 * EVENTS
 * ------
 * nubi:widget-ready  { rows, renderer: 'table' }
 * nubi:widget-error  { message }
 */

import { resolveToken, fetchArrow, makeSampleTableData, escapeHtml, formatCell, BASE_STYLES } from './shared.js'

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const TABLE_STYLES = /* css */ `
  ${BASE_STYLES}

  .nubi-wrap {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    box-sizing: border-box;
  }

  .nubi-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 7px 12px;
    background: var(--nubi-accent, #1e2433);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    font-size: 11px;
    gap: 8px;
    flex-shrink: 0;
  }

  .nubi-title {
    font-weight: 600;
    letter-spacing: 0.02em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    opacity: 0.75;
  }

  .nubi-table-wrap {
    overflow: auto;
    flex: 1;
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
    padding: 6px 10px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.65;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    white-space: nowrap;
  }

  tbody tr {
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    transition: background 0.1s;
  }

  tbody tr:hover {
    background: rgba(255,255,255,0.04);
  }

  tbody td {
    padding: 5px 10px;
    color: var(--nubi-fg, #e2e8f0);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .nubi-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 4px 10px;
    font-size: 10px;
    opacity: 0.4;
    border-top: 1px solid var(--nubi-border, #2d3748);
    gap: 8px;
    flex-shrink: 0;
  }
`

// ---------------------------------------------------------------------------
// Render Arrow table to HTML
// ---------------------------------------------------------------------------
function buildTableHTML(table, colNames, limit) {
  const rowCount = Math.min(table.numRows, limit)

  const thead = `<thead><tr>${
    colNames.map(n => `<th>${escapeHtml(n)}</th>`).join('')
  }</tr></thead>`

  const rows = []
  for (let r = 0; r < rowCount; r++) {
    const cells = colNames.map(col => {
      const val = table.getChild(col)?.get(r)
      return `<td>${escapeHtml(formatCell(val))}</td>`
    })
    rows.push(`<tr>${cells.join('')}</tr>`)
  }
  const tbody = `<tbody>${rows.join('')}</tbody>`
  return `<table>${thead}${tbody}</table>`
}

// ---------------------------------------------------------------------------
// NubiTable — custom element
// ---------------------------------------------------------------------------
class NubiTable extends HTMLElement {
  static get observedAttributes() {
    return ['query-id', 'limit', 'columns', 'token', 'get-token', 'backend']
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null
  }

  connectedCallback() { this._render() }
  disconnectedCallback() { this._abort() }
  attributeChangedCallback(_n, old, val) { if (old !== val && this.isConnected) this._render() }

  _abort() { if (this._ac) { this._ac.abort(); this._ac = null } }

  _backend() {
    return (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')
  }

  _limit() {
    const v = parseInt(this.getAttribute('limit') || '100', 10)
    return isNaN(v) || v <= 0 ? 100 : v
  }

  _columns(table) {
    const attr = this.getAttribute('columns')
    if (!attr) return table.schema.fields.map(f => f.name)
    return attr.split(',').map(c => c.trim()).filter(c =>
      table.schema.fields.some(f => f.name === c)
    )
  }

  _ensureScaffold() {
    if (this._shadow.querySelector('.nubi-wrap')) return

    const styleEl = document.createElement('style')
    styleEl.textContent = TABLE_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(styleEl)

    this._shadow.innerHTML += /* html */ `
      <div class="nubi-wrap">
        <div class="nubi-toolbar">
          <span class="nubi-title">${escapeHtml(this.getAttribute('query-id') || 'table')}</span>
          <span class="nubi-badge" style="display:none">SAMPLE</span>
        </div>
        <div class="nubi-sample-note">preview · sample data</div>
        <div class="nubi-table-wrap">
          <div class="nubi-loading">Loading</div>
        </div>
        <div class="nubi-footer"></div>
      </div>
    `
    this._shadow.insertBefore(styleEl, this._shadow.firstChild)
  }

  _showTable(table, isSample) {
    const limit = this._limit()
    const cols = this._columns(table)

    const wrap = this._shadow.querySelector('.nubi-table-wrap')
    if (wrap) wrap.innerHTML = buildTableHTML(table, cols, limit)

    const displayed = Math.min(table.numRows, limit)
    const footer = this._shadow.querySelector('.nubi-footer')
    if (footer) {
      footer.textContent = `${displayed.toLocaleString()} / ${table.numRows.toLocaleString()} rows`
    }

    const badge = this._shadow.querySelector('.nubi-badge')
    const note = this._shadow.querySelector('.nubi-sample-note')
    if (isSample) {
      if (badge) { badge.style.display = 'inline-block'; badge.textContent = 'SAMPLE' }
      if (note) note.classList.add('visible')
    } else {
      if (badge) badge.style.display = 'none'
      if (note) note.classList.remove('visible')
    }
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()

    const tw = this._shadow.querySelector('.nubi-table-wrap')
    if (tw) tw.innerHTML = '<div class="nubi-loading">Loading</div>'

    const queryId = this.getAttribute('query-id')
    const backend = this._backend()

    let token = null
    try { token = await resolveToken(this) } catch (_) { /* ignore */ }
    if (ac.signal.aborted) return

    if (queryId && backend) {
      try {
        const table = await fetchArrow(backend, queryId, token, ac.signal)
        if (ac.signal.aborted) return

        this._showTable(table, false)
        this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
          bubbles: true, composed: true,
          detail: { rows: table.numRows, renderer: 'table' },
        }))
        return
      } catch (err) {
        if (err.name === 'AbortError') return
        console.warn('[nubi-table] fetch failed — showing sample:', err.message)
        this.dispatchEvent(new CustomEvent('nubi:widget-error', {
          bubbles: true, composed: true,
          detail: { message: err.message },
        }))
      }
    }

    if (ac.signal.aborted) return

    // Sample fallback
    const sample = makeSampleTableData()
    this._showTable(sample, true)
    this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
      bubbles: true, composed: true,
      detail: { rows: sample.numRows, renderer: 'table' },
    }))
  }
}

export { NubiTable }
