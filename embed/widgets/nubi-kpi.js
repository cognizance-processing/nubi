/**
 * nubi-kpi.js — <nubi-kpi> big-number metric card widget (M8-A).
 *
 * ATTRIBUTES
 * ----------
 * query-id    (required) Registered query id to execute via POST /api/v1/query.
 * value-col   (required) Column name to read the metric value from (first row).
 * label       Display label shown below the number. Defaults to value-col.
 * format      Optional format hint: "number" | "currency" | "percent" | "integer".
 *             Defaults to "number".
 * token       Static JWT string.
 * get-token   Name of a window function returning Promise<string>|string.
 * backend     Base URL of the Nubi API. Defaults to http://localhost:8000.
 *
 * CSS CUSTOM PROPERTIES
 * ---------------------
 * --nubi-bg, --nubi-fg, --nubi-accent, --nubi-border  (standard Nubi theme vars)
 *
 * EVENTS
 * ------
 * nubi:widget-ready  { rows, renderer: 'kpi' }
 * nubi:widget-error  { message }
 *
 * SAMPLE FALLBACK
 * ---------------
 * Any failure falls back to a visible sample card so demo pages always render.
 */

import { resolveToken, fetchArrow, makeSampleKpiTable, escapeHtml, formatCell, BASE_STYLES } from './shared.js'

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const KPI_STYLES = /* css */ `
  ${BASE_STYLES}

  :host {
    min-width: 140px;
    min-height: 100px;
  }

  .kpi-wrap {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: space-between;
    padding: 20px 24px 16px;
    height: 100%;
    box-sizing: border-box;
    gap: 8px;
  }

  .kpi-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
  }

  .kpi-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.55;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .kpi-value {
    font-size: 36px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--nubi-accent-fg, var(--nubi-fg, #e2e8f0));
    line-height: 1;
    font-variant-numeric: tabular-nums;
    word-break: break-all;
  }

  .kpi-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    gap: 8px;
  }

  .kpi-sublabel {
    font-size: 11px;
    opacity: 0.4;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .nubi-sample-note {
    font-size: 10px;
    padding: 2px 6px;
    margin: 0;
    border: none;
    border-radius: 3px;
    text-align: left;
  }
`

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------
function formatValue(raw, fmt) {
  const num = Number(raw)
  if (isNaN(num)) return formatCell(raw)

  switch (fmt) {
    case 'currency':
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(num)
    case 'percent':
      return new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(num)
    case 'integer':
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(num)
    case 'number':
    default: {
      // Auto-compact: e.g. 124500 -> "124.5K"
      if (Math.abs(num) >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M'
      if (Math.abs(num) >= 1_000) return (num / 1_000).toFixed(1) + 'K'
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(num)
    }
  }
}

// ---------------------------------------------------------------------------
// NubiKpi — custom element
// ---------------------------------------------------------------------------
class NubiKpi extends HTMLElement {
  static get observedAttributes() {
    return ['query-id', 'value-col', 'label', 'format', 'token', 'get-token', 'backend']
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

  _ensureScaffold() {
    if (this._shadow.querySelector('.kpi-wrap')) return

    const styleEl = document.createElement('style')
    styleEl.textContent = KPI_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(styleEl)

    const wrap = document.createElement('div')
    wrap.className = 'kpi-wrap'
    wrap.innerHTML = /* html */ `
      <div class="kpi-top">
        <span class="kpi-label"></span>
        <span class="nubi-badge sample" style="display:none">SAMPLE</span>
      </div>
      <div class="kpi-value nubi-loading">…</div>
      <div class="kpi-footer">
        <span class="kpi-sublabel"></span>
        <span class="nubi-sample-note visible" style="display:none">preview · sample data</span>
      </div>
    `
    this._shadow.appendChild(wrap)
  }

  _showValue(table, isSample) {
    const valueCol = this.getAttribute('value-col') || table.schema.fields[0]?.name || ''
    const label = this.getAttribute('label') || valueCol
    const fmt = this.getAttribute('format') || 'number'

    const colVec = table.getChild(valueCol)
    const rawVal = colVec ? colVec.get(0) : null
    const display = rawVal !== null ? formatValue(rawVal, fmt) : '—'

    this._shadow.querySelector('.kpi-label').textContent = label
    this._shadow.querySelector('.kpi-value').className = 'kpi-value'
    this._shadow.querySelector('.kpi-value').textContent = display

    const badge = this._shadow.querySelector('.nubi-badge')
    const note = this._shadow.querySelector('.nubi-sample-note')
    if (isSample) {
      badge.style.display = 'inline-block'
      note.style.display = 'inline-block'
      this._shadow.querySelector('.kpi-sublabel').textContent = 'sample data'
    } else {
      badge.style.display = 'none'
      note.style.display = 'none'
      this._shadow.querySelector('.kpi-sublabel').textContent = `query: ${this.getAttribute('query-id') || ''}`
    }
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()

    // Show loading state
    this._shadow.querySelector('.kpi-value').className = 'kpi-value nubi-loading'
    this._shadow.querySelector('.kpi-value').textContent = '…'

    const queryId = this.getAttribute('query-id')
    const backend = this._backend()

    let token = null
    try { token = await resolveToken(this) } catch (_) { /* ignore */ }
    if (ac.signal.aborted) return

    if (queryId && backend) {
      try {
        const table = await fetchArrow(backend, queryId, token, ac.signal)
        if (ac.signal.aborted) return

        this._showValue(table, false)
        this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
          bubbles: true, composed: true,
          detail: { rows: table.numRows, renderer: 'kpi' },
        }))
        return
      } catch (err) {
        if (err.name === 'AbortError') return
        console.warn('[nubi-kpi] fetch failed — showing sample:', err.message)
        this.dispatchEvent(new CustomEvent('nubi:widget-error', {
          bubbles: true, composed: true,
          detail: { message: err.message },
        }))
      }
    }

    if (ac.signal.aborted) return

    // Sample fallback
    const sample = makeSampleKpiTable()
    this._showValue(sample, true)
    this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
      bubbles: true, composed: true,
      detail: { rows: sample.numRows, renderer: 'kpi' },
    }))
  }
}

export { NubiKpi }
