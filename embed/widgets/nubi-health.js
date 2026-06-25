/**
 * nubi-health.js — <nubi-health> data-health score + freshness widget.
 *
 * ATTRIBUTES
 * ----------
 * backend      Base URL of the Nubi API. Defaults to http://localhost:8000.
 * get-token    Name of a window function returning Promise<string>|string.
 * token        Static JWT string.
 * theme        'dark' | 'light'. Defaults to 'dark'.
 * dataset-key  Optional. When set, filters score & freshness to a single dataset.
 * no-sample-fallback  When present, shows an error instead of sample data.
 *
 * EVENTS
 * ------
 * nubi:widget-ready  { score, grade, datasets, renderer: 'health' }
 * nubi:widget-error  { message }
 *
 * RENDERING
 * ---------
 * • Circular gauge showing overall score + grade letter (average when multi-dataset)
 * • Reasons list from the score response
 * • Freshness table with RAG dots: green=fresh, amber=stale<24h, red=stale>24h
 *
 * Fully themed via CSS custom properties from the Nubi design system.
 *
 * SAMPLE FALLBACK
 * ---------------
 * When no backend is configured (or the request fails) the widget renders
 * sample health data so demo pages always show content.
 */

import { resolveToken, escapeHtml, BASE_STYLES } from './shared.js'
import { fetchJson } from './shared.js'
import { applyTheme } from '../theme.js'

// ---------------------------------------------------------------------------
// Sample data
// ---------------------------------------------------------------------------

function makeSampleHealth() {
  return {
    score: 87,
    grade: 'B',
    reasons: [
      'orders dataset is fresh (updated 2 h ago)',
      'products completeness: 98 %',
      'sessions dataset stale — last update 26 h ago',
    ],
    datasets: [
      { dataset_key: 'raw/orders',    score: 94, grade: 'A', fresh: true,  last_updated: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),  status: 'fresh' },
      { dataset_key: 'raw/products',  score: 91, grade: 'A', fresh: true,  last_updated: new Date(Date.now() - 5 * 3600 * 1000).toISOString(),  status: 'fresh' },
      { dataset_key: 'model/revenue', score: 85, grade: 'B', fresh: true,  last_updated: new Date(Date.now() - 14 * 3600 * 1000).toISOString(), status: 'fresh' },
      { dataset_key: 'raw/sessions',  score: 42, grade: 'D', fresh: false, last_updated: new Date(Date.now() - 26 * 3600 * 1000).toISOString(), status: 'stale' },
    ],
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Compute average score and grade from an array of score results.
 * Falls back to the first result when only one exists.
 */
function aggregateScores(results) {
  if (!results || results.length === 0) return { score: 0, grade: 'F', reasons: [], datasets: [] }
  if (results.length === 1) {
    const r = results[0]
    return {
      score:    r.score ?? 0,
      grade:    r.grade ?? 'F',
      reasons:  r.reasons ?? [],
      datasets: results,
    }
  }
  const avg = Math.round(results.reduce((s, r) => s + (r.score ?? 0), 0) / results.length)
  const grade = scoreToGrade(avg)
  const reasons = results.flatMap(r => r.reasons ?? []).slice(0, 5)
  return { score: avg, grade, reasons, datasets: results }
}

function scoreToGrade(score) {
  if (score >= 90) return 'A'
  if (score >= 80) return 'B'
  if (score >= 70) return 'C'
  if (score >= 60) return 'D'
  return 'F'
}

/** RAG status for a freshness record. */
function freshnessRag(rec) {
  if (rec.fresh === true || rec.status === 'fresh') return 'green'
  const updated = rec.last_updated || rec.last_success_at
  if (!updated) return 'red'
  const ageH = (Date.now() - new Date(updated).getTime()) / 3_600_000
  return ageH < 24 ? 'amber' : 'red'
}

/** Human-readable relative time (e.g. "2 h ago"). */
function relTime(isoString) {
  if (!isoString) return '—'
  const ageMs = Date.now() - new Date(isoString).getTime()
  if (ageMs < 0) return 'just now'
  const h = Math.floor(ageMs / 3_600_000)
  const d = Math.floor(h / 24)
  if (d > 0) return `${d} d ago`
  if (h > 0) return `${h} h ago`
  const m = Math.floor(ageMs / 60_000)
  return m > 0 ? `${m} min ago` : 'just now'
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const HEALTH_STYLES = /* css */ `
  ${BASE_STYLES}

  :host {
    min-width: 260px;
    min-height: 180px;
  }

  .hl-wrap {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
  }

  .hl-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px 8px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    flex-shrink: 0;
  }

  .hl-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.55;
  }

  .hl-badges {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .hl-body {
    flex: 1;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 0;
  }

  /* Score section */
  .hl-score-row {
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 16px 16px 12px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
  }

  .hl-gauge-wrap {
    position: relative;
    flex-shrink: 0;
    width: 72px;
    height: 72px;
  }

  .hl-gauge {
    display: block;
    transform: rotate(-90deg);
    transform-origin: center;
  }

  .hl-gauge-track {
    fill: none;
    stroke: var(--nubi-border, #2d3748);
    stroke-width: 6;
  }

  .hl-gauge-fill {
    fill: none;
    stroke-width: 6;
    stroke-linecap: round;
    transition: stroke-dashoffset 0.5s ease, stroke 0.3s;
  }

  .hl-gauge-grade {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 22px;
    font-weight: 800;
    color: var(--nubi-fg, #e2e8f0);
    line-height: 1;
  }

  .hl-score-meta {
    flex: 1;
    min-width: 0;
  }

  .hl-score-number {
    font-size: 32px;
    font-weight: 700;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
    color: var(--nubi-fg, #e2e8f0);
    line-height: 1;
    margin-bottom: 4px;
  }

  .hl-score-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.45;
  }

  /* Reasons */
  .hl-reasons {
    padding: 10px 16px 8px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
  }

  .hl-reasons-title {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.4;
    margin-bottom: 6px;
  }

  .hl-reason-item {
    font-size: 11px;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.75;
    padding: 2px 0;
    line-height: 1.4;
  }

  .hl-reason-item::before {
    content: '•';
    margin-right: 6px;
    opacity: 0.4;
  }

  /* Freshness table */
  .hl-freshness {
    padding: 10px 16px 12px;
    flex: 1;
    overflow: auto;
  }

  .hl-fresh-title {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.4;
    margin-bottom: 8px;
  }

  .hl-fresh-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
    color: var(--nubi-fg, #e2e8f0);
  }

  .hl-fresh-table th {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    opacity: 0.4;
    text-align: left;
    padding: 0 0 6px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
  }

  .hl-fresh-table td {
    padding: 5px 0;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    opacity: 0.8;
    font-variant-numeric: tabular-nums;
  }

  .hl-fresh-table tr:last-child td {
    border-bottom: none;
  }

  .hl-fresh-table td.hl-key {
    font-family: var(--nubi-font-mono, monospace);
    font-size: 10px;
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding-right: 8px;
  }

  .hl-fresh-table td.hl-age {
    color: var(--nubi-fg-muted, #718096);
    font-size: 10px;
    white-space: nowrap;
    padding-right: 8px;
  }

  .hl-fresh-table td.hl-grade {
    font-size: 10px;
    font-weight: 700;
    text-align: right;
    padding-right: 0;
  }

  /* RAG dot */
  .hl-rag {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 8px;
    flex-shrink: 0;
    vertical-align: middle;
  }
  .hl-rag.green  { background: var(--nubi-success, #10b981); }
  .hl-rag.amber  { background: var(--nubi-warning, #f59e0b); }
  .hl-rag.red    { background: var(--nubi-error, #ef4444); }

  /* Grade colors */
  .grade-A { color: var(--nubi-success, #10b981); }
  .grade-B { color: var(--nubi-success, #10b981); opacity: 0.85; }
  .grade-C { color: var(--nubi-warning, #f59e0b); }
  .grade-D { color: var(--nubi-warning, #f59e0b); opacity: 0.85; }
  .grade-F { color: var(--nubi-error, #ef4444); }

  /* Error state */
  .hl-error {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 24px;
    font-size: 12px;
    opacity: 0.5;
  }
`

// ---------------------------------------------------------------------------
// Gauge arc helper
// ---------------------------------------------------------------------------
const GAUGE_R = 29
const GAUGE_CIRC = 2 * Math.PI * GAUGE_R

function gaugeColor(score) {
  if (score >= 80) return 'var(--nubi-success, #10b981)'
  if (score >= 60) return 'var(--nubi-warning, #f59e0b)'
  return 'var(--nubi-error, #ef4444)'
}

// ---------------------------------------------------------------------------
// NubiHealth — custom element
// ---------------------------------------------------------------------------

class NubiHealth extends HTMLElement {
  static get observedAttributes() {
    return ['get-token', 'token', 'backend', 'theme', 'dataset-key', 'no-sample-fallback']
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
    if (this._shadow.querySelector('.hl-wrap')) return

    const style = document.createElement('style')
    style.textContent = HEALTH_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(style)

    const wrap = document.createElement('div')
    wrap.className = 'hl-wrap'
    wrap.innerHTML = /* html */ `
      <div class="hl-header">
        <span class="hl-title">Data Health</span>
        <div class="hl-badges">
          <span class="nubi-badge sample" style="display:none">SAMPLE</span>
          <span class="nubi-badge live"   style="display:none">LIVE</span>
        </div>
      </div>
      <div class="hl-body">
        <div class="nubi-loading">Loading…</div>
      </div>
    `
    this._shadow.appendChild(wrap)
  }

  _showLoading() {
    const body = this._shadow.querySelector('.hl-body')
    if (body) body.innerHTML = '<div class="nubi-loading">Loading…</div>'
  }

  _showError(message) {
    const body = this._shadow.querySelector('.hl-body')
    if (!body) return
    const div = document.createElement('div')
    div.className = 'hl-error'
    div.appendChild(document.createTextNode('⚠ ' + (message || 'Could not load health data')))
    body.innerHTML = ''
    body.appendChild(div)
    this._shadow.querySelector('.nubi-badge.sample').style.display = 'none'
    this._shadow.querySelector('.nubi-badge.live').style.display = 'none'
  }

  _renderHealth(data, freshnessRecords, isSample) {
    const body = this._shadow.querySelector('.hl-body')
    if (!body) return

    this._shadow.querySelector('.nubi-badge.sample').style.display = isSample ? 'inline-block' : 'none'
    this._shadow.querySelector('.nubi-badge.live').style.display   = isSample ? 'none' : 'inline-block'

    const { score, grade, reasons, datasets } = data

    // --- Build DOM safely (no innerHTML on user data) ---

    body.innerHTML = ''

    // Score row
    const scoreRow = document.createElement('div')
    scoreRow.className = 'hl-score-row'

    // Gauge
    const gaugeWrap = document.createElement('div')
    gaugeWrap.className = 'hl-gauge-wrap'

    const svgNS = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(svgNS, 'svg')
    svg.setAttribute('class', 'hl-gauge')
    svg.setAttribute('width', '72')
    svg.setAttribute('height', '72')
    svg.setAttribute('viewBox', '0 0 72 72')

    const track = document.createElementNS(svgNS, 'circle')
    track.setAttribute('class', 'hl-gauge-track')
    track.setAttribute('cx', '36')
    track.setAttribute('cy', '36')
    track.setAttribute('r', String(GAUGE_R))

    const fill = document.createElementNS(svgNS, 'circle')
    fill.setAttribute('class', 'hl-gauge-fill')
    fill.setAttribute('cx', '36')
    fill.setAttribute('cy', '36')
    fill.setAttribute('r', String(GAUGE_R))
    fill.setAttribute('stroke-dasharray', `${GAUGE_CIRC}`)
    fill.setAttribute('stroke-dashoffset', String(GAUGE_CIRC * (1 - (score || 0) / 100)))
    fill.setAttribute('stroke', gaugeColor(score || 0))

    svg.appendChild(track)
    svg.appendChild(fill)
    gaugeWrap.appendChild(svg)

    const gradeLabel = document.createElement('div')
    gradeLabel.className = `hl-gauge-grade grade-${grade || 'F'}`
    gradeLabel.textContent = grade || 'F'
    gaugeWrap.appendChild(gradeLabel)

    // Score meta
    const scoreMeta = document.createElement('div')
    scoreMeta.className = 'hl-score-meta'

    const scoreNumber = document.createElement('div')
    scoreNumber.className = 'hl-score-number'
    scoreNumber.textContent = String(Math.round(score || 0))

    const scoreLabel = document.createElement('div')
    scoreLabel.className = 'hl-score-label'
    scoreLabel.textContent = 'Health Score'

    scoreMeta.appendChild(scoreNumber)
    scoreMeta.appendChild(scoreLabel)

    scoreRow.appendChild(gaugeWrap)
    scoreRow.appendChild(scoreMeta)
    body.appendChild(scoreRow)

    // Reasons
    const reasonsList = (reasons || []).slice(0, 5)
    if (reasonsList.length > 0) {
      const reasonsSec = document.createElement('div')
      reasonsSec.className = 'hl-reasons'

      const reasonsTitle = document.createElement('div')
      reasonsTitle.className = 'hl-reasons-title'
      reasonsTitle.textContent = 'Reasons'
      reasonsSec.appendChild(reasonsTitle)

      for (const reason of reasonsList) {
        const item = document.createElement('div')
        item.className = 'hl-reason-item'
        item.textContent = String(reason)
        reasonsSec.appendChild(item)
      }

      body.appendChild(reasonsSec)
    }

    // Freshness table
    // Merge: datasets from score + separate freshness records
    const allRecords = _mergeFreshnessRecords(datasets || [], freshnessRecords || [])

    if (allRecords.length > 0) {
      const freshSec = document.createElement('div')
      freshSec.className = 'hl-freshness'

      const freshTitle = document.createElement('div')
      freshTitle.className = 'hl-fresh-title'
      freshTitle.textContent = 'Freshness'
      freshSec.appendChild(freshTitle)

      const table = document.createElement('table')
      table.className = 'hl-fresh-table'

      // Header
      const thead = document.createElement('thead')
      const hRow = document.createElement('tr')
      for (const colName of ['', 'Dataset', 'Updated', 'Score']) {
        const th = document.createElement('th')
        th.textContent = colName
        hRow.appendChild(th)
      }
      thead.appendChild(hRow)
      table.appendChild(thead)

      const tbody = document.createElement('tbody')
      for (const rec of allRecords) {
        const tr = document.createElement('tr')

        // RAG dot
        const ragTd = document.createElement('td')
        ragTd.style.width = '16px'
        const dot = document.createElement('span')
        dot.className = `hl-rag ${freshnessRag(rec)}`
        ragTd.appendChild(dot)
        tr.appendChild(ragTd)

        // Dataset key
        const keyTd = document.createElement('td')
        keyTd.className = 'hl-key'
        keyTd.title = String(rec.dataset_key || rec.key || '')
        keyTd.textContent = String(rec.dataset_key || rec.key || '—')
        tr.appendChild(keyTd)

        // Last updated
        const ageTd = document.createElement('td')
        ageTd.className = 'hl-age'
        ageTd.textContent = relTime(rec.last_updated || rec.last_success_at)
        tr.appendChild(ageTd)

        // Grade / score
        const gradeTd = document.createElement('td')
        gradeTd.className = `hl-grade grade-${rec.grade || scoreToGrade(rec.score || 0)}`
        gradeTd.textContent = rec.grade
          ? `${rec.grade} (${Math.round(rec.score || 0)})`
          : String(Math.round(rec.score || 0))
        tr.appendChild(gradeTd)

        tbody.appendChild(tr)
      }
      table.appendChild(tbody)
      freshSec.appendChild(table)
      body.appendChild(freshSec)
    }
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()
    this._showLoading()

    const backend    = this._backend()
    const datasetKey = this.getAttribute('dataset-key')

    let token = null
    try { token = await resolveToken(this) } catch (_) { /* ignore */ }
    if (ac.signal.aborted) return

    if (backend) {
      try {
        // Fetch score and freshness in parallel
        const scorePath = datasetKey
          ? `/api/v1/health/score?dataset_key=${encodeURIComponent(datasetKey)}`
          : '/api/v1/health/score'

        const freshnessPath = datasetKey
          ? `/api/v1/health/freshness/${encodeURIComponent(datasetKey)}`
          : '/api/v1/health/freshness'

        const [rawScore, rawFreshness] = await Promise.all([
          fetchJson(backend, scorePath, token, ac.signal),
          fetchJson(backend, freshnessPath, token, ac.signal).catch(() => null),
        ])
        if (ac.signal.aborted) return

        // Normalise score response
        let scoreData
        if (datasetKey) {
          // Single dataset response
          scoreData = aggregateScores([rawScore])
        } else {
          const results = Array.isArray(rawScore) ? rawScore : (rawScore.datasets || [rawScore])
          scoreData = aggregateScores(results)
        }

        // Normalise freshness response
        let freshnessRecords = []
        if (rawFreshness) {
          if (Array.isArray(rawFreshness)) {
            freshnessRecords = rawFreshness
          } else if (rawFreshness.datasets) {
            freshnessRecords = rawFreshness.datasets
          } else if (rawFreshness.dataset_key) {
            freshnessRecords = [rawFreshness]
          }
        }

        this._renderHealth(scoreData, freshnessRecords, false)
        this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
          bubbles: true, composed: true,
          detail: {
            score:    scoreData.score,
            grade:    scoreData.grade,
            datasets: scoreData.datasets.length,
            renderer: 'health',
          },
        }))
        return
      } catch (err) {
        if (err.name === 'AbortError') return
        console.warn('[nubi-health] fetch failed:', err.message)
        this.dispatchEvent(new CustomEvent('nubi:widget-error', {
          bubbles: true, composed: true,
          detail: { message: err.message },
        }))
        if (this.hasAttribute('no-sample-fallback')) {
          this._showError(err.message)
          return
        }
      }
    }

    if (ac.signal.aborted) return

    if (this.hasAttribute('no-sample-fallback')) {
      this._showError('No backend configured')
      return
    }

    // Sample fallback
    const sample = makeSampleHealth()
    this._renderHealth(
      { score: sample.score, grade: sample.grade, reasons: sample.reasons, datasets: sample.datasets },
      sample.datasets,
      true,
    )
    this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
      bubbles: true, composed: true,
      detail: { score: sample.score, grade: sample.grade, datasets: sample.datasets.length, renderer: 'health' },
    }))
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Merge score dataset results with separate freshness records, deduplicating by key.
 */
function _mergeFreshnessRecords(scoreDatasets, freshnessRecords) {
  const seen = new Set()
  const out = []

  // Score datasets have score + grade; prefer them
  for (const d of scoreDatasets) {
    const key = d.dataset_key || d.key || ''
    seen.add(key)
    out.push(d)
  }

  // Fill in freshness records not already present
  for (const r of freshnessRecords) {
    const key = r.dataset_key || r.key || ''
    if (!seen.has(key)) {
      seen.add(key)
      out.push(r)
    }
  }

  return out
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

if (!customElements.get('nubi-health')) {
  customElements.define('nubi-health', NubiHealth)
}

export { NubiHealth }
