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

import { resolveToken, escapeHtml, el, BASE_STYLES } from './shared.js'
import { applyTheme } from '../theme.js'

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
      'theme',
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

  _abort() {
    if (this._ac) { this._ac.abort(); this._ac = null }
  }

  _backend() {
    return (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')
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
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()

    const body = this._shadow.querySelector('.explain-body')
    body.innerHTML = '<div class="nubi-loading">Loading…</div>'

    const metricId    = this.getAttribute('metric-id')
    const currentStart   = this.getAttribute('current-start')
    const currentEnd     = this.getAttribute('current-end')
    const comparisonStart = this.getAttribute('comparison-start')
    const comparisonEnd   = this.getAttribute('comparison-end')
    const topN = parseInt(this.getAttribute('top-n') || '10', 10)
    const includeSummary = this.getAttribute('include-summary') === 'true'
    const backend = this._backend()

    let token = null
    try { token = await resolveToken(this) } catch (_) { /* ignore */ }
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
      }
    }

    if (ac.signal.aborted) return

    // Sample fallback
    this._renderData(_makeSampleData(), true)
  }
}

if (typeof customElements !== 'undefined' && !customElements.get('nubi-explain')) {
  customElements.define('nubi-explain', NubiExplain)
}

export { NubiExplain }
