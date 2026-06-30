/**
 * nubi-explain.js — <nubi-explain> root-cause contribution analysis widget.
 *
 * ATTRIBUTES
 * ----------
 * metric-id         (required) The metric slug to analyze.
 * current-start     (required) ISO datetime for current period start.
 * current-end       (required) ISO datetime for current period end.
 * comparison-start  (required) ISO datetime for comparison period start.
 * comparison-end    (required) ISO datetime for comparison period end.
 * top-n             Max members per dimension (default 10).
 * include-summary   "true" to request NL summary (default false).
 * token             Static JWT string.
 * get-token         Name of a window function returning Promise<string>|string.
 * backend           Base URL of the Nubi API. Defaults to http://localhost:8000.
 * model-attribution JSON string of a host-supplied ModelAttribution payload (see below).
 *
 * PROPERTIES
 * ----------
 * .modelAttribution  (ModelAttribution|null) The EXPLICIT host prop pathway for the
 *                    model-attribution payload. Set it directly on the element instance
 *                    (e.g. `el.modelAttribution = {...}`) to render the second, distinct
 *                    "why the model predicted this" section. This payload is supplied by
 *                    the HOST — the widget NEVER fetches or computes it. Takes precedence
 *                    over the `model-attribution` attribute. When unset, the drawer renders
 *                    exactly as before (metric-contribution only).
 *
 * TWO DISTINCT PAYLOADS
 * ---------------------
 * This drawer can render two payloads that answer DIFFERENT questions and are kept
 * visually namespaced (never blurred together):
 *   1. METRIC CONTRIBUTION (Nubi computes it, via POST /metrics/{id}/explain) —
 *      "why did this NUMBER move across dimensions." Rendered from the fetched/inline
 *      `data` (ExplainResponse). Always present.
 *   2. MODEL ATTRIBUTION (the HOST posts it; Nubi does NOT compute it) — per-prediction
 *      SHAP / feature attribution for the host's own model: "why did the model predict X."
 *      Optional; supplied via the `.modelAttribution` prop or `model-attribution` attribute.
 *
 * EVENTS
 * ------
 * nubi:select   Fired when user clicks a member bar. detail: { dimension, member, delta, direction }
 * nubi:error    Fired on fetch errors. detail: { message }
 *
 * CSS CUSTOM PROPERTIES
 * ---------------------
 * --nubi-bg, --nubi-fg, --nubi-accent, --nubi-border
 * --nubi-up     (default #22c55e) — colour for positive deltas
 * --nubi-down   (default #ef4444) — colour for negative deltas
 *
 * SAMPLE FALLBACK
 * ---------------
 * On any failure the component renders sample contribution data so demo
 * pages always show something meaningful.
 */

import { resolveToken, escapeHtml, BASE_STYLES } from './shared.js'
import { applyTheme } from '../theme.js'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
//
// The metric-contribution payload (ExplainResponse — Nubi computes it) is
// documented in the file header and the backend (ExplainResponse in
// backend/app/routes/metrics.py). The types below describe the SECOND,
// distinct payload: a host-supplied per-prediction model attribution. It is
// intentionally generic and domain-agnostic — Nubi never computes or fetches
// it; the host posts it in.

/**
 * A single feature's contribution to a model prediction (e.g. one SHAP value).
 *
 * @typedef {Object} ModelAttributionFeature
 * @property {string}                feature        Human-readable feature name.
 * @property {string|number} [value]                The feature's value for this prediction.
 * @property {number}                contribution   Signed push toward (+) or away from (−)
 *                                                   the prediction, in the prediction's units.
 */

/**
 * Host-supplied model-attribution payload. Answers "why did the model predict X
 * for this SKU/store" — distinct from the metric-contribution payload, which
 * answers "why did this number move across dimensions".
 *
 * @typedef {Object} ModelAttribution
 * @property {'model_attribution'}        kind          Namespace discriminator.
 * @property {string}              [model]              Model name / identifier.
 * @property {{label?: string, value?: number}} [prediction]  The prediction being explained.
 * @property {number}              [base_value]         Model base/expected value (SHAP E[f(x)]).
 * @property {ModelAttributionFeature[]} features        Per-feature contributions (any order;
 *                                                       the widget sorts by |contribution|).
 * @property {string}              [summary]            Optional natural-language summary.
 */

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const EXPLAIN_STYLES = /* css */ `
  ${BASE_STYLES}

  :host {
    min-width: 280px;
    min-height: 200px;
  }

  .explain-wrap {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
    overflow: hidden;
  }

  .explain-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 14px;
    background: var(--nubi-accent, #1e2433);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    font-size: 11px;
    flex-shrink: 0;
    gap: 8px;
  }

  .explain-title {
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.7;
  }

  .explain-delta {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    font-size: 13px;
  }

  .explain-delta.up   { color: var(--nubi-up, #22c55e); }
  .explain-delta.down { color: var(--nubi-down, #ef4444); }
  .explain-delta.flat { opacity: 0.5; }

  .explain-body {
    flex: 1;
    overflow-y: auto;
    padding: 8px 0 4px;
  }

  .dim-section {
    margin-bottom: 16px;
  }

  .dim-title {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    opacity: 0.45;
    padding: 0 14px 4px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .dim-coverage {
    opacity: 0.4;
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
  }

  .member-row {
    display: flex;
    align-items: center;
    padding: 3px 14px;
    cursor: pointer;
    gap: 8px;
    transition: background 0.1s;
  }

  .member-row:hover {
    background: rgba(255,255,255,0.04);
  }

  .member-name {
    font-size: 12px;
    flex: 0 0 90px;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    opacity: 0.85;
  }

  .member-name.other {
    opacity: 0.4;
    font-style: italic;
  }

  .member-bar-wrap {
    flex: 1;
    height: 10px;
    background: rgba(255,255,255,0.06);
    border-radius: 2px;
    overflow: hidden;
    position: relative;
  }

  .member-bar {
    height: 100%;
    border-radius: 2px;
    min-width: 2px;
    transition: width 0.3s;
  }

  .member-bar.up   { background: var(--nubi-up, #22c55e); }
  .member-bar.down { background: var(--nubi-down, #ef4444); }
  .member-bar.flat { background: rgba(255,255,255,0.2); }
  .member-bar.other { opacity: 0.4; }

  .member-share {
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    flex: 0 0 40px;
    text-align: right;
    opacity: 0.55;
  }

  .explain-summary {
    font-size: 12px;
    line-height: 1.5;
    opacity: 0.65;
    padding: 8px 14px 10px;
    border-top: 1px solid var(--nubi-border, #2d3748);
    margin-top: 4px;
  }

  /* ── Namespaced section headers (shown only when BOTH payloads present) ── */
  .ns-header {
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 10px 14px 6px;
  }

  .ns-header.attrib {
    border-top: 2px solid var(--nubi-border, #2d3748);
    margin-top: 6px;
  }

  .ns-namespace {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    opacity: 0.5;
  }
  .ns-namespace.contribution { color: var(--nubi-up, #22c55e); }
  .ns-namespace.attrib       { color: #818cf8; }

  .ns-question {
    font-size: 12px;
    font-weight: 600;
    opacity: 0.8;
  }

  /* ── Model-attribution section ── */
  .attrib-meta {
    padding: 2px 14px 6px;
    font-size: 11px;
    opacity: 0.6;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 10px;
    font-variant-numeric: tabular-nums;
  }

  .attrib-meta b {
    font-weight: 600;
    opacity: 0.9;
  }

  .attrib-row {
    display: flex;
    align-items: center;
    padding: 3px 14px;
    gap: 8px;
  }

  .attrib-feature {
    font-size: 12px;
    flex: 0 0 110px;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    opacity: 0.85;
  }

  .attrib-feature .fval {
    opacity: 0.45;
    font-variant-numeric: tabular-nums;
  }

  /* Bidirectional bar: a centre line with the bar growing left (−) or right (+). */
  .attrib-bar-wrap {
    flex: 1;
    height: 10px;
    position: relative;
    background: rgba(255,255,255,0.06);
    border-radius: 2px;
  }

  .attrib-bar-wrap::before {
    content: '';
    position: absolute;
    left: 50%;
    top: -1px;
    bottom: -1px;
    width: 1px;
    background: rgba(255,255,255,0.18);
  }

  .attrib-bar {
    position: absolute;
    top: 0;
    height: 100%;
    border-radius: 2px;
    min-width: 2px;
  }
  .attrib-bar.up   { left: 50%;  background: var(--nubi-up, #22c55e); }
  .attrib-bar.down { right: 50%; background: var(--nubi-down, #ef4444); }

  .attrib-contribution {
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    flex: 0 0 56px;
    text-align: right;
    opacity: 0.7;
  }
  .attrib-contribution.up   { color: var(--nubi-up, #22c55e); }
  .attrib-contribution.down { color: var(--nubi-down, #ef4444); }

  .attrib-summary {
    font-size: 12px;
    line-height: 1.5;
    opacity: 0.65;
    padding: 8px 14px 10px;
  }

  .nubi-badge.model { background: #1e1b4b; color: #c7d2fe; }

  .explain-footer {
    padding: 4px 14px;
    font-size: 10px;
    opacity: 0.35;
    border-top: 1px solid var(--nubi-border, #2d3748);
    flex-shrink: 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

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

  .nubi-error-state {
    padding: 32px 24px;
    text-align: center;
    color: var(--nubi-fg, #e2e8f0);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }

  .nubi-error-primary {
    font-size: 13px;
    font-weight: 500;
    opacity: 0.6;
  }

  .nubi-error-primary::before {
    content: '⚠ ';
    font-size: 12px;
  }

  .nubi-error-secondary {
    font-size: 11px;
    opacity: 0.35;
  }
`

// ---------------------------------------------------------------------------
// Sample fallback data
// ---------------------------------------------------------------------------

function _makeSampleData() {
  return {
    metric_id: 'demo',
    measure: 'revenue',
    delta_total: 12500,
    current_total: 87500,
    comparison_total: 75000,
    dimensions: [
      {
        dimension: 'region',
        members: [
          { member: 'North', current: 42000, comparison: 34000, delta: 8000, share: 0.64, direction: 'up' },
          { member: 'South', current: 28000, comparison: 25000, delta: 3000, share: 0.24, direction: 'up' },
          { member: 'West',  current: 17500, comparison: 16000, delta: 1500, share: 0.12, direction: 'up' },
        ],
        other: null,
        coverage: 1.0,
        explanatory_power: 1.0,
      },
      {
        dimension: 'category',
        members: [
          { member: 'Software', current: 50000, comparison: 40000, delta: 10000, share: 0.80, direction: 'up' },
          { member: 'Hardware', current: 25000, comparison: 27500, delta: -2500, share: -0.20, direction: 'down' },
          { member: 'Services', current: 12500, comparison: 7500, delta: 5000, share: 0.40, direction: 'up' },
        ],
        other: null,
        coverage: 1.0,
        explanatory_power: 0.95,
      },
    ],
    summary: null,
  }
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function _fmtNumber(n) {
  if (n === null || n === undefined || isNaN(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : n > 0 ? '+' : ''
  if (abs >= 1_000_000) return sign + (abs / 1_000_000).toFixed(1) + 'M'
  if (abs >= 1_000) return sign + (abs / 1_000).toFixed(1) + 'K'
  return sign + new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(abs)
}

function _fmtPct(share) {
  if (share === null || share === undefined || isNaN(share)) return ''
  const pct = Math.abs(share * 100)
  return pct.toFixed(1) + '%'
}

// Signed, adaptive-precision formatter for model-attribution contributions
// (SHAP-like values are often small, so keep more decimals than _fmtNumber).
function _fmtSigned(n) {
  if (n === null || n === undefined || isNaN(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : n > 0 ? '+' : ''
  if (abs >= 1_000_000) return sign + (abs / 1_000_000).toFixed(2) + 'M'
  if (abs >= 1_000) return sign + (abs / 1_000).toFixed(2) + 'K'
  const digits = abs >= 1 ? 2 : 3
  return sign + new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(abs)
}

// Unsigned, adaptive-precision formatter for base value / prediction value.
function _fmtValue(n) {
  if (n === null || n === undefined || isNaN(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (abs >= 1_000) return (n / 1_000).toFixed(2) + 'K'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(n)
}

// ---------------------------------------------------------------------------
// Sample model-attribution (host-supplied payload demo)
// ---------------------------------------------------------------------------

/**
 * @returns {ModelAttribution}
 */
function _makeSampleModelAttribution() {
  return {
    kind: 'model_attribution',
    model: 'demand_forecast_v3',
    prediction: { label: 'units_next_week', value: 168 },
    base_value: 120,
    features: [
      { feature: 'promo_active',      value: 'yes',   contribution: 38 },
      { feature: 'avg_temp_c',        value: 27.5,    contribution: 21 },
      { feature: 'price',             value: 14.99,   contribution: -18 },
      { feature: 'day_of_week',       value: 'Sat',   contribution: 12 },
      { feature: 'competitor_promo',  value: 'yes',   contribution: -9 },
      { feature: 'stock_on_hand',     value: 320,     contribution: 4 },
    ],
    summary: null,
  }
}

// ---------------------------------------------------------------------------
// NubiExplain — custom element
// ---------------------------------------------------------------------------

class NubiExplain extends HTMLElement {
  static get observedAttributes() {
    return [
      'metric-id',
      'current-start', 'current-end',
      'comparison-start', 'comparison-end',
      'top-n', 'include-summary',
      'token', 'get-token', 'backend',
      'theme', 'data', 'no-sample-fallback',
      'model-attribution',
    ]
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null
    /** @type {ModelAttribution|null} */
    this._modelAttribution = null
  }

  /**
   * Explicit host prop pathway for the model-attribution payload.
   * The host sets this directly (`el.modelAttribution = {...}`); the widget
   * never fetches or computes it. Setting it triggers a re-render.
   * @returns {ModelAttribution|null}
   */
  get modelAttribution() { return this._modelAttribution }
  set modelAttribution(val) {
    this._modelAttribution = val || null
    if (this.isConnected) this._render()
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

  _abort() {
    if (this._ac) { this._ac.abort(); this._ac = null }
  }

  _backend() {
    return (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')
  }

  /**
   * Resolve the host-supplied model-attribution payload. Precedence:
   *   1. `.modelAttribution` property (the explicit prop pathway).
   *   2. `model-attribution` attribute (inline JSON string).
   * Returns null when neither is present or the JSON is invalid — in which
   * case the drawer renders the metric-contribution payload alone, unchanged.
   * @returns {ModelAttribution|null}
   */
  _resolveModelAttribution() {
    if (this._modelAttribution && typeof this._modelAttribution === 'object') {
      return this._modelAttribution
    }
    const attr = this.getAttribute('model-attribution')
    if (attr) {
      try {
        const parsed = JSON.parse(attr)
        if (parsed && typeof parsed === 'object') return parsed
      } catch (err) {
        console.warn('[nubi-explain] invalid model-attribution attribute:', err.message)
      }
    }
    return null
  }

  _ensureScaffold() {
    if (this._shadow.querySelector('.explain-wrap')) return

    const styleEl = document.createElement('style')
    styleEl.textContent = EXPLAIN_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(styleEl)

    const wrap = document.createElement('div')
    wrap.className = 'explain-wrap'
    wrap.innerHTML = /* html */ `
      <div class="explain-header">
        <span class="explain-title">Contribution Analysis</span>
        <span class="explain-delta">—</span>
        <span class="nubi-badge sample" style="display:none">SAMPLE</span>
      </div>
      <div class="explain-body">
        <div class="nubi-loading">Loading…</div>
      </div>
    `
    this._shadow.appendChild(wrap)
  }

  _renderData(data, isSample) {
    const body = this._shadow.querySelector('.explain-body')
    body.innerHTML = ''

    // Host-supplied model-attribution payload (optional, second payload).
    const attrib = this._resolveModelAttribution()

    // Only namespace the contribution section when BOTH payloads are present,
    // so the single-payload case renders exactly as before (no regression).
    if (attrib) {
      const nsHead = document.createElement('div')
      nsHead.className = 'ns-header contribution'
      nsHead.innerHTML = `
        <span class="ns-namespace contribution">Metric contribution</span>
        <span class="ns-question">Why the number moved</span>
      `
      body.appendChild(nsHead)
    }

    const deltaEl = this._shadow.querySelector('.explain-delta')
    const sampleBadge = this._shadow.querySelector('.nubi-badge.sample')

    const dt = data.delta_total || 0
    const dir = dt > 1e-9 ? 'up' : dt < -1e-9 ? 'down' : 'flat'
    deltaEl.textContent = _fmtNumber(dt)
    deltaEl.className = `explain-delta ${dir}`

    if (isSample) {
      sampleBadge.style.display = 'inline-block'
    } else {
      sampleBadge.style.display = 'none'
    }

    // Compute max abs delta across all top members (for bar scaling)
    let maxAbsDelta = 0
    for (const dim of (data.dimensions || [])) {
      for (const m of dim.members) {
        if (Math.abs(m.delta) > maxAbsDelta) maxAbsDelta = Math.abs(m.delta)
      }
      if (dim.other && Math.abs(dim.other.delta) > maxAbsDelta) {
        maxAbsDelta = Math.abs(dim.other.delta)
      }
    }
    if (maxAbsDelta < 1e-9) maxAbsDelta = 1

    for (const dim of (data.dimensions || [])) {
      const section = document.createElement('div')
      section.className = 'dim-section'

      const titleEl = document.createElement('div')
      titleEl.className = 'dim-title'
      const coveragePct = ((dim.coverage || 0) * 100).toFixed(0)
      titleEl.innerHTML = `
        <span>${escapeHtml(dim.dimension)}</span>
        <span class="dim-coverage">${coveragePct}% coverage</span>
      `
      section.appendChild(titleEl)

      const allMembers = [...dim.members]
      if (dim.other) allMembers.push(dim.other)

      for (const member of allMembers) {
        const isOther = member.member === 'Other'
        const mDir = member.direction || 'flat'
        const barWidthPct = Math.min(100, (Math.abs(member.delta) / maxAbsDelta) * 100)

        const row = document.createElement('div')
        row.className = 'member-row'
        row.setAttribute('data-dimension', dim.dimension)
        row.setAttribute('data-member', String(member.member))
        row.setAttribute('data-delta', String(member.delta))
        row.setAttribute('data-direction', mDir)

        const nameEl = document.createElement('span')
        nameEl.className = `member-name${isOther ? ' other' : ''}`
        nameEl.textContent = member.member === null ? '(null)' : String(member.member)

        const barWrap = document.createElement('div')
        barWrap.className = 'member-bar-wrap'

        const bar = document.createElement('div')
        bar.className = `member-bar ${mDir}${isOther ? ' other' : ''}`
        bar.style.width = `${barWidthPct}%`
        barWrap.appendChild(bar)

        const shareEl = document.createElement('span')
        shareEl.className = 'member-share'
        shareEl.textContent = _fmtPct(member.share)

        row.appendChild(nameEl)
        row.appendChild(barWrap)
        row.appendChild(shareEl)

        // Emit nubi:select when user clicks
        row.addEventListener('click', () => {
          this.dispatchEvent(new CustomEvent('nubi:select', {
            bubbles: true,
            composed: true,
            detail: {
              dimension: dim.dimension,
              member: member.member,
              delta: member.delta,
              direction: mDir,
              share: member.share,
            },
          }))
        })

        section.appendChild(row)
      }

      body.appendChild(section)
    }

    // NL summary (if present)
    if (data.summary) {
      const summaryEl = document.createElement('div')
      summaryEl.className = 'explain-summary'
      summaryEl.textContent = data.summary
      body.appendChild(summaryEl)
    }

    // Footer
    const footer = document.createElement('div')
    footer.className = 'explain-footer'
    footer.innerHTML = `
      <span>${escapeHtml(data.metric_id || '')} · ${escapeHtml(data.measure || '')}</span>
      <span>${data.dimensions ? data.dimensions.length : 0} dimension(s)</span>
    `
    body.appendChild(footer)

    // Second, visually distinct payload: host-supplied model attribution.
    if (attrib) this._renderModelAttribution(body, attrib)
  }

  /**
   * Render the host-supplied model-attribution payload as a clearly separated,
   * namespaced section ("Why the model predicted this"). Feature contributions
   * are sorted by |contribution|, signed/directional, with an optional base
   * value + prediction and summary. Nubi never computes this — it is passed in.
   *
   * @param {HTMLElement} body
   * @param {ModelAttribution} attrib
   */
  _renderModelAttribution(body, attrib) {
    // Namespaced header — distinct accent colour from the contribution section.
    const nsHead = document.createElement('div')
    nsHead.className = 'ns-header attrib'
    nsHead.innerHTML = `
      <span class="ns-namespace attrib">Model attribution${attrib.model ? ' · ' + escapeHtml(String(attrib.model)) : ''}</span>
      <span class="ns-question">Why the model predicted this</span>
    `
    body.appendChild(nsHead)

    // Base value + prediction meta line.
    const pred = attrib.prediction || {}
    const hasBase = attrib.base_value !== null && attrib.base_value !== undefined
    const hasPred = pred.value !== null && pred.value !== undefined
    if (hasBase || hasPred || pred.label) {
      const meta = document.createElement('div')
      meta.className = 'attrib-meta'
      const parts = []
      if (hasBase) parts.push(`<span>base <b>${escapeHtml(_fmtValue(attrib.base_value))}</b></span>`)
      if (hasPred || pred.label) {
        const label = pred.label ? escapeHtml(String(pred.label)) : 'prediction'
        const valTxt = hasPred ? escapeHtml(_fmtValue(pred.value)) : '—'
        parts.push(`<span>${label} <b>${valTxt}</b></span>`)
      }
      meta.innerHTML = parts.join('')
      body.appendChild(meta)
    }

    // Feature rows, sorted by |contribution| descending.
    const features = Array.isArray(attrib.features) ? attrib.features.slice() : []
    features.sort((a, b) => Math.abs(b.contribution || 0) - Math.abs(a.contribution || 0))

    let maxAbs = 0
    for (const f of features) {
      const c = Math.abs(f.contribution || 0)
      if (c > maxAbs) maxAbs = c
    }
    if (maxAbs < 1e-9) maxAbs = 1

    for (const f of features) {
      const c = Number(f.contribution) || 0
      const dir = c > 1e-9 ? 'up' : c < -1e-9 ? 'down' : 'flat'
      const widthPct = Math.min(50, (Math.abs(c) / maxAbs) * 50)

      const row = document.createElement('div')
      row.className = 'attrib-row'
      row.setAttribute('data-feature', String(f.feature))
      row.setAttribute('data-contribution', String(c))

      const nameEl = document.createElement('span')
      nameEl.className = 'attrib-feature'
      const valSuffix = (f.value !== null && f.value !== undefined && f.value !== '')
        ? ` <span class="fval">${escapeHtml(String(f.value))}</span>`
        : ''
      // feature name is text; value is escaped above.
      nameEl.innerHTML = `${escapeHtml(String(f.feature ?? ''))}${valSuffix}`

      const barWrap = document.createElement('div')
      barWrap.className = 'attrib-bar-wrap'
      if (dir !== 'flat') {
        const bar = document.createElement('div')
        bar.className = `attrib-bar ${dir}`
        bar.style.width = `${widthPct}%`
        barWrap.appendChild(bar)
      }

      const contribEl = document.createElement('span')
      contribEl.className = `attrib-contribution ${dir}`
      contribEl.textContent = _fmtSigned(c)

      row.appendChild(nameEl)
      row.appendChild(barWrap)
      row.appendChild(contribEl)
      body.appendChild(row)
    }

    if (attrib.summary) {
      const summaryEl = document.createElement('div')
      summaryEl.className = 'attrib-summary'
      summaryEl.textContent = attrib.summary
      body.appendChild(summaryEl)
    }
  }

  _showError(_rawMsg) {
    const body = this._shadow.querySelector('.explain-body')
    if (body) {
      body.innerHTML = ''
      const d = document.createElement('div')
      d.className = 'nubi-error-state'
      d.innerHTML = `
        <span class="nubi-error-primary">Couldn't load data</span>
        <span class="nubi-error-secondary">Check your connection or try again</span>
      `
      body.appendChild(d)
    }
    const badge = this._shadow.querySelector('.nubi-badge.sample')
    if (badge) badge.style.display = 'none'
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()

    const body = this._shadow.querySelector('.explain-body')
    body.innerHTML = '<div class="nubi-loading">Loading…</div>'

    // Inline data injection — bypasses fetch entirely
    const dataAttr = this.getAttribute('data')
    if (dataAttr) {
      try {
        const data = JSON.parse(dataAttr)
        this._renderData(data, false)
      } catch (err) {
        console.warn('[nubi-explain] invalid data attribute:', err.message)
      }
      return
    }

    const metricId    = this.getAttribute('metric-id')
    const currentStart   = this.getAttribute('current-start')
    const currentEnd     = this.getAttribute('current-end')
    const comparisonStart = this.getAttribute('comparison-start')
    const comparisonEnd   = this.getAttribute('comparison-end')
    const topN = parseInt(this.getAttribute('top-n') || '10', 10)
    const includeSummary = this.getAttribute('include-summary') === 'true'
    const backend = this._backend()

    let token = null
    try { token = await resolveToken(this) } catch { /* ignore */ }
    if (ac.signal.aborted) return

    if (metricId && currentStart && currentEnd && comparisonStart && comparisonEnd && backend) {
      try {
        const url = `${backend}/api/v1/metrics/${encodeURIComponent(metricId)}/explain`
        const headers = { 'Content-Type': 'application/json' }
        if (token) headers['Authorization'] = `Bearer ${token}`

        const resp = await fetch(url, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            current: { start: currentStart, end: currentEnd },
            comparison: { start: comparisonStart, end: comparisonEnd },
            top_n: topN,
            include_summary: includeSummary,
          }),
          credentials: 'omit',
          signal: ac.signal,
        })

        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status} from ${url}`)
        }

        const data = await resp.json()
        if (ac.signal.aborted) return

        this._renderData(data, false)
        return
      } catch (err) {
        if (err.name === 'AbortError') return
        console.warn('[nubi-explain] fetch failed — showing sample:', err.message)
        this.dispatchEvent(new CustomEvent('nubi:error', {
          bubbles: true,
          composed: true,
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

    this._renderData(_makeSampleData(), true)
  }
}

if (typeof customElements !== 'undefined' && !customElements.get('nubi-explain')) {
  customElements.define('nubi-explain', NubiExplain)
}

export { NubiExplain, _makeSampleModelAttribution }
