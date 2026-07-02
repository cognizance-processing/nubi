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
 * KPI TARGET ATTRIBUTES (all optional — absent = no target rendering)
 * -----------------------------------------------------------------------
 * target-col      Column name that holds the target value (e.g. "revenue_target").
 *                 When present the widget renders a goal line, % to target, and a
 *                 RAG chip driven by the data columns the compiler emits.
 * rag-col         Column name for the RAG status string ("green"/"amber"/"red").
 *                 Defaults to "<value-col>_rag" when target-col is set.
 * pct-col         Column name for the pct_to_goal ratio.
 *                 Defaults to "<value-col>_pct_to_goal" when target-col is set.
 *
 * When target-col is absent (or the column is missing from the result) the widget
 * renders exactly as before — no target UI is shown.
 *
 * CSS CUSTOM PROPERTIES
 * ---------------------
 * --nubi-bg, --nubi-fg, --nubi-accent, --nubi-border  (standard Nubi theme vars)
 * --nubi-success, --nubi-warning, --nubi-error         (RAG colors)
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

import { resolveToken, fetchArrow, makeSampleKpiTable, formatCell, BASE_STYLES, rowsToArrowTable } from './shared.js'
import { applyTheme } from '../theme.js'

// ---------------------------------------------------------------------------
// RAG color map (uses Nubi theme tokens)
// ---------------------------------------------------------------------------
const RAG_COLORS = {
  green:  'var(--nubi-success, #10b981)',
  amber:  'var(--nubi-warning, #f59e0b)',
  red:    'var(--nubi-error,   #ef4444)',
}

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

  /* ── KPI Target styles ── */

  .kpi-target-row {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
  }

  .kpi-goal-bar-wrap {
    flex: 1;
    height: 4px;
    background: var(--nubi-border, #2d3748);
    border-radius: 2px;
    overflow: hidden;
  }

  .kpi-goal-bar {
    height: 100%;
    border-radius: 2px;
    transition: width 0.3s ease;
  }

  .kpi-rag-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 2px 7px;
    border-radius: 10px;
    opacity: 0.9;
    white-space: nowrap;
    color: #fff;
  }

  .kpi-rag-chip.green  { background: var(--nubi-success, #10b981); }
  .kpi-rag-chip.amber  { background: var(--nubi-warning, #f59e0b); }
  .kpi-rag-chip.red    { background: var(--nubi-error, #ef4444); }

  .kpi-pct-label {
    font-size: 11px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.7;
    white-space: nowrap;
  }

  .kpi-error-state {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 0;
  }

  .kpi-error-primary {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 500;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.65;
  }

  .kpi-error-primary::before {
    content: '⚠';
    font-size: 11px;
    opacity: 0.7;
  }

  .kpi-error-secondary {
    font-size: 10px;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.38;
  }

  /* Keep legacy class for e2e selector compatibility */
  .kpi-error-note {
    display: none;
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

/**
 * Format a pct_to_goal ratio (0–N) as a human-readable percent string.
 * e.g. 0.83 → "83%" · 1.0 → "100%" · 1.25 → "125%"
 */
function formatPct(ratio) {
  const pct = Math.round(Number(ratio) * 100)
  return `${pct}%`
}

// ---------------------------------------------------------------------------
// NubiKpi — custom element
// ---------------------------------------------------------------------------
class NubiKpi extends HTMLElement {
  static get observedAttributes() {
    return [
      'query-id', 'value-col', 'label', 'format',
      'token', 'get-token', 'backend',
      'target-col', 'rag-col', 'pct-col',
      'theme', 'data', 'no-sample-fallback',
    ]
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null
  }

  connectedCallback() {
    applyTheme(this, this.getAttribute('theme') || 'dark')
    this._render()
  }
  disconnectedCallback() { this._abort() }
  attributeChangedCallback(name, old, val) {
    if (old === val) return
    if (name === 'theme') applyTheme(this, val || 'dark')
    if (this.isConnected) this._render()
  }

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
      <div class="kpi-target-row" style="display:none">
        <div class="kpi-goal-bar-wrap">
          <div class="kpi-goal-bar"></div>
        </div>
        <span class="kpi-pct-label"></span>
        <span class="kpi-rag-chip"></span>
      </div>
      <div class="kpi-footer">
        <span class="kpi-sublabel"></span>
        <span class="nubi-sample-note visible" style="display:none">preview · sample data</span>
      </div>
    `
    this._shadow.appendChild(wrap)
  }

  /**
   * Resolve target-related column names from attributes.
   * Returns null when target-col is not configured.
   */
  _targetCols(valueCol) {
    const targetCol = this.getAttribute('target-col')
    if (!targetCol) return null
    return {
      target: targetCol,
      rag:    this.getAttribute('rag-col')  || `${valueCol}_rag`,
      pct:    this.getAttribute('pct-col')  || `${valueCol}_pct_to_goal`,
    }
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
    const note  = this._shadow.querySelector('.nubi-sample-note')

    if (isSample) {
      badge.style.display = 'inline-block'
      note.style.display  = 'inline-block'
      // Keep sublabel empty — the note already reads "preview · sample data"
      this._shadow.querySelector('.kpi-sublabel').textContent = ''
    } else {
      badge.style.display = 'none'
      note.style.display  = 'none'
      // Sublabel: show query-id in a subtle way, or omit if not set
      const qid = this.getAttribute('query-id')
      this._shadow.querySelector('.kpi-sublabel').textContent = qid ? qid : ''
    }

    // ── Target / RAG rendering ───────────────────────────────────────────────
    this._renderTarget(table, valueCol)
  }

  /**
   * Render the KPI target row if the result carries target columns.
   *
   * When target-col attribute is present AND the Arrow table contains the
   * column, renders:
   *   • a goal-progress bar (clamped 0–100 %)
   *   • a pct-to-goal label (e.g. "83%")
   *   • a RAG chip ("green" / "amber" / "red")
   *
   * When absent, hides the target row (no-op for callers without targets).
   */
  _renderTarget(table, valueCol) {
    const targetRow = this._shadow.querySelector('.kpi-target-row')
    const cols = this._targetCols(valueCol)

    if (!cols) {
      targetRow.style.display = 'none'
      return
    }

    // Try to read the rag / pct columns from the result.
    const ragVec = table.getChild(cols.rag)
    const pctVec = table.getChild(cols.pct)

    const rag = ragVec ? String(ragVec.get(0) ?? '').toLowerCase() : null
    const pct = pctVec ? Number(pctVec.get(0)) : null

    if (rag === null && pct === null) {
      // Columns not present in the data — hide target row gracefully.
      targetRow.style.display = 'none'
      return
    }

    targetRow.style.display = 'flex'

    // Goal bar (0–100%, capped)
    const goalBar = this._shadow.querySelector('.kpi-goal-bar')
    const barColor = RAG_COLORS[rag] ?? RAG_COLORS.red
    const barPct = pct !== null ? Math.min(Math.max(pct * 100, 0), 100) : 0
    goalBar.style.width = `${barPct.toFixed(1)}%`
    goalBar.style.background = barColor

    // % to goal label
    const pctLabel = this._shadow.querySelector('.kpi-pct-label')
    pctLabel.textContent = pct !== null ? formatPct(pct) : ''

    // RAG chip
    const ragChip = this._shadow.querySelector('.kpi-rag-chip')
    ragChip.className = `kpi-rag-chip ${rag || 'red'}`
    ragChip.textContent = (rag || 'red').toUpperCase()
  }

  _showError(_rawMessage) {
    // Clear the big metric value — don't show giant text in error state
    const v = this._shadow.querySelector('.kpi-value')
    if (v) {
      v.className = 'kpi-value'
      v.textContent = ''
    }

    const badge = this._shadow.querySelector('.nubi-badge')
    const note  = this._shadow.querySelector('.nubi-sample-note')
    if (badge) badge.style.display = 'none'
    if (note)  note.style.display  = 'none'

    // Inject friendly error UI into the value slot (not the giant-font style)
    let errState = this._shadow.querySelector('.kpi-error-state')
    if (!errState) {
      errState = document.createElement('div')
      errState.className = 'kpi-error-state'
      errState.innerHTML = `
        <span class="kpi-error-primary">Couldn’t load data</span>
        <span class="kpi-error-secondary">Check your connection or try again</span>
      `
      // Keep a hidden .kpi-error-note for e2e selector compatibility
      let errNote = this._shadow.querySelector('.kpi-error-note')
      if (!errNote) {
        errNote = document.createElement('div')
        errNote.className = 'kpi-error-note'
        errNote.style.display = 'none'
      }
      const footer = this._shadow.querySelector('.kpi-footer')
      if (footer) {
        footer.before(errState)
        footer.before(errNote)
      }
    }

    // Hide target row
    const targetRow = this._shadow.querySelector('.kpi-target-row')
    if (targetRow) targetRow.style.display = 'none'
    const label = this.getAttribute('label') || this.getAttribute('value-col') || 'KPI'
    const labelEl = this._shadow.querySelector('.kpi-label')
    if (labelEl) labelEl.textContent = label
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()

    // Show loading state
    this._shadow.querySelector('.kpi-value').className = 'kpi-value nubi-loading'
    this._shadow.querySelector('.kpi-value').textContent = '…'

    // Inline data injection — bypasses fetch entirely
    const dataAttr = this.getAttribute('data')
    if (dataAttr) {
      try {
        const rows = JSON.parse(dataAttr)
        const table = rowsToArrowTable(rows)
        this._showValue(table, false)
        this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
          bubbles: true, composed: true,
          detail: { rows: table.numRows, renderer: 'kpi' },
        }))
      } catch (err) {
        console.warn('[nubi-kpi] invalid data attribute:', err.message)
      }
      return
    }

    const queryId = this.getAttribute('query-id')
    const backend = this._backend()

    let token = null
    try { token = await resolveToken(this) } catch { /* ignore */ }
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
        if (this.hasAttribute('no-sample-fallback')) {
          this._showError()
          return
        }
      }
    }

    if (ac.signal.aborted) return

    // Sample fallback
    if (this.hasAttribute('no-sample-fallback')) {
      this._showError()
      return
    }

    const sample = makeSampleKpiTable()
    this._showValue(sample, true)
    this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
      bubbles: true, composed: true,
      detail: { rows: sample.numRows, renderer: 'kpi' },
    }))
  }
}

export { NubiKpi }
