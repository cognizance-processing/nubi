/**
 * nubi-lineage.js — <nubi-lineage> DAG lineage graph widget.
 *
 * ATTRIBUTES
 * ----------
 * backend      Base URL of the Nubi API. Defaults to http://localhost:8000.
 * get-token    Name of a window function returning Promise<string>|string.
 * token        Static JWT string.
 * theme        'dark' | 'light'. Defaults to 'dark'.
 * node-id      Optional. When set, fetches /api/v1/lineage/dag/{node-id}?hops=N
 *              instead of the full DAG.
 * hops         Traversal depth when node-id is set (default 2, max 20).
 * no-sample-fallback  When present, shows an error instead of sample data.
 *
 * EVENTS
 * ------
 * nubi:select        { node }  — fired when the user clicks a DAG node
 * nubi:widget-ready  { nodes, edges, renderer: 'lineage' }
 * nubi:widget-error  { message }
 *
 * RENDERING
 * ---------
 * Columnar SVG layout: columns by node type (table → query → metric).
 * Nodes rendered as rounded rect boxes, edges as curved paths.
 * Fully themed via CSS custom properties from the Nubi design system.
 *
 * SAMPLE FALLBACK
 * ---------------
 * When no backend is configured (or the request fails) the widget renders a
 * 4-node / 3-edge sample DAG so demo pages always show content.
 */

import { resolveToken, escapeHtml, BASE_STYLES } from './shared.js'
import { fetchJson } from './shared.js'
import { applyTheme } from '../theme.js'

// ---------------------------------------------------------------------------
// Sample data (used when no backend / request fails)
// ---------------------------------------------------------------------------

function makeSampleDag() {
  return {
    nodes: [
      { id: 'table:orders',   type: 'table',  name: 'orders',   tables: [],        outputs: ['order_count', 'revenue'] },
      { id: 'table:products', type: 'table',  name: 'products', tables: [],        outputs: ['product_id', 'category'] },
      { id: 'query:revenue',  type: 'query',  name: 'Revenue Query', tables: ['orders', 'products'], outputs: ['revenue'] },
      { id: 'metric:revenue', type: 'metric', name: 'Revenue Metric', tables: [],  outputs: ['revenue'] },
    ],
    edges: [
      { from: 'table:orders',   to: 'query:revenue',  via: 'orders' },
      { from: 'table:products', to: 'query:revenue',  via: 'products' },
      { from: 'query:revenue',  to: 'metric:revenue', via: 'revenue' },
    ],
  }
}

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

const COL_ORDER = ['table', 'source', 'raw', 'query', 'metric', 'feature', 'model']

const NODE_W = 140
const NODE_H = 44
const COL_GAP = 80
const ROW_GAP = 20
const PAD_X = 20
const PAD_Y = 20

/**
 * Compute an SVG layout for the given nodes + edges.
 * Returns { nodePositions: Map<id, {x,y,w,h}>, svgW, svgH }.
 */
function computeLayout(nodes, _edges) {
  // Group nodes by column (type)
  const colMap = new Map()
  for (const n of nodes) {
    const col = COL_ORDER.indexOf(n.type) >= 0 ? n.type : 'query'
    if (!colMap.has(col)) colMap.set(col, [])
    colMap.get(col).push(n)
  }

  // Sort columns by COL_ORDER
  const cols = [...colMap.entries()].sort(
    ([a], [b]) => COL_ORDER.indexOf(a) - COL_ORDER.indexOf(b)
  )

  const nodePositions = new Map()
  let x = PAD_X
  let svgH = PAD_Y * 2

  for (const [, colNodes] of cols) {
    let y = PAD_Y
    for (const n of colNodes) {
      nodePositions.set(n.id, { x, y, w: NODE_W, h: NODE_H })
      y += NODE_H + ROW_GAP
    }
    const colH = PAD_Y + (colNodes.length * (NODE_H + ROW_GAP)) - ROW_GAP + PAD_Y
    if (colH > svgH) svgH = colH
    x += NODE_W + COL_GAP
  }

  const svgW = x - COL_GAP + PAD_X

  return { nodePositions, svgW, svgH }
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const LINEAGE_STYLES = /* css */ `
  ${BASE_STYLES}

  :host {
    min-width: 280px;
    min-height: 180px;
  }

  .ln-wrap {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
  }

  .ln-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px 8px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    flex-shrink: 0;
  }

  .ln-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.55;
  }

  .ln-badges {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .ln-body {
    flex: 1;
    overflow: auto;
    position: relative;
  }

  .ln-svg {
    display: block;
  }

  /* Node rects */
  .ln-node {
    cursor: pointer;
  }
  .ln-node rect {
    fill: var(--nubi-bg-2, #1a1f2e);
    stroke: var(--nubi-border, #2d3748);
    stroke-width: 1.5;
    rx: 6;
    ry: 6;
    transition: fill 0.12s, stroke 0.12s;
  }
  .ln-node:hover rect {
    fill: var(--nubi-accent, #1e2433);
    stroke: var(--nubi-primary, #6366f1);
  }
  .ln-node.selected rect {
    fill: var(--nubi-accent, #1e2433);
    stroke: var(--nubi-primary, #6366f1);
    stroke-width: 2;
  }

  .ln-node-label {
    font-size: 11px;
    font-weight: 500;
    fill: var(--nubi-fg, #e2e8f0);
    pointer-events: none;
    dominant-baseline: central;
    text-anchor: middle;
  }

  .ln-node-type {
    font-size: 9px;
    fill: var(--nubi-fg-muted, #718096);
    pointer-events: none;
    dominant-baseline: central;
    text-anchor: middle;
    opacity: 0.7;
  }

  /* Type-based node accent colors */
  .ln-node[data-type="table"] rect    { stroke: var(--nubi-success, #10b981); }
  .ln-node[data-type="source"] rect   { stroke: var(--nubi-success, #10b981); }
  .ln-node[data-type="raw"] rect      { stroke: var(--nubi-success, #10b981); }
  .ln-node[data-type="metric"] rect   { stroke: var(--nubi-primary, #6366f1); }
  .ln-node[data-type="feature"] rect  { stroke: var(--nubi-primary, #6366f1); }
  .ln-node[data-type="model"] rect    { stroke: var(--nubi-warning, #f59e0b); }

  /* Edges */
  .ln-edge {
    fill: none;
    stroke: var(--nubi-border, #2d3748);
    stroke-width: 1.5;
    opacity: 0.7;
  }

  /* Column headers */
  .ln-col-header {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    fill: var(--nubi-fg-muted, #718096);
    opacity: 0.5;
    dominant-baseline: middle;
    text-anchor: middle;
  }

  /* Error state */
  .ln-error {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 24px;
    font-size: 12px;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.5;
  }

  .ln-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    font-size: 12px;
    opacity: 0.4;
  }
`

// ---------------------------------------------------------------------------
// NubiLineage — custom element
// ---------------------------------------------------------------------------

class NubiLineage extends HTMLElement {
  static get observedAttributes() {
    return ['get-token', 'token', 'backend', 'theme', 'node-id', 'hops', 'no-sample-fallback']
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })
    this._ac = null
    this._selectedId = null
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
    if (this._shadow.querySelector('.ln-wrap')) return

    const style = document.createElement('style')
    style.textContent = LINEAGE_STYLES
    this._shadow.innerHTML = ''
    this._shadow.appendChild(style)

    const wrap = document.createElement('div')
    wrap.className = 'ln-wrap'
    wrap.innerHTML = /* html */ `
      <div class="ln-header">
        <span class="ln-title">Lineage DAG</span>
        <div class="ln-badges">
          <span class="nubi-badge sample" style="display:none">SAMPLE</span>
          <span class="nubi-badge live"   style="display:none">LIVE</span>
        </div>
      </div>
      <div class="ln-body">
        <div class="nubi-loading">Loading…</div>
      </div>
    `
    this._shadow.appendChild(wrap)
  }

  _showLoading() {
    const body = this._shadow.querySelector('.ln-body')
    if (body) body.innerHTML = '<div class="nubi-loading">Loading…</div>'
  }

  _showError(message) {
    const body = this._shadow.querySelector('.ln-body')
    if (!body) return
    const div = document.createElement('div')
    div.className = 'ln-error'
    const icon = document.createTextNode('⚠ ')
    const msg = document.createTextNode(message || 'Could not load lineage data')
    div.appendChild(icon)
    div.appendChild(msg)
    body.innerHTML = ''
    body.appendChild(div)
    this._shadow.querySelector('.nubi-badge.sample').style.display = 'none'
    this._shadow.querySelector('.nubi-badge.live').style.display = 'none'
  }

  _renderDag(dag, isSample) {
    const { nodes, edges } = dag
    const body = this._shadow.querySelector('.ln-body')
    if (!body) return

    this._shadow.querySelector('.nubi-badge.sample').style.display = isSample ? 'inline-block' : 'none'
    this._shadow.querySelector('.nubi-badge.live').style.display   = isSample ? 'none' : 'inline-block'

    if (!nodes || nodes.length === 0) {
      body.innerHTML = ''
      const div = document.createElement('div')
      div.className = 'ln-empty'
      div.textContent = 'No lineage data found'
      body.appendChild(div)
      return
    }

    const { nodePositions, svgW, svgH } = computeLayout(nodes, edges)

    // Build SVG string (safe — all user strings are escaped)
    const ns = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(ns, 'svg')
    svg.setAttribute('class', 'ln-svg')
    svg.setAttribute('width', String(Math.max(svgW, 200)))
    svg.setAttribute('height', String(Math.max(svgH, 160)))
    svg.setAttribute('viewBox', `0 0 ${Math.max(svgW, 200)} ${Math.max(svgH, 160)}`)

    // Draw edges first (behind nodes)
    for (const edge of (edges || [])) {
      const src = nodePositions.get(edge.from)
      const dst = nodePositions.get(edge.to)
      if (!src || !dst) continue

      const x1 = src.x + src.w
      const y1 = src.y + src.h / 2
      const x2 = dst.x
      const y2 = dst.y + dst.h / 2
      const cx = (x1 + x2) / 2

      const path = document.createElementNS(ns, 'path')
      path.setAttribute('class', 'ln-edge')
      path.setAttribute('d', `M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}`)
      svg.appendChild(path)
    }

    // Draw nodes
    for (const node of nodes) {
      const pos = nodePositions.get(node.id)
      if (!pos) continue

      const g = document.createElementNS(ns, 'g')
      g.setAttribute('class', this._selectedId === node.id ? 'ln-node selected' : 'ln-node')
      g.setAttribute('data-type', node.type || 'query')
      g.setAttribute('data-id', node.id)
      g.setAttribute('tabindex', '0')

      const rect = document.createElementNS(ns, 'rect')
      rect.setAttribute('x', String(pos.x))
      rect.setAttribute('y', String(pos.y))
      rect.setAttribute('width', String(pos.w))
      rect.setAttribute('height', String(pos.h))
      rect.setAttribute('rx', '6')
      rect.setAttribute('ry', '6')

      // Node label (truncated)
      const label = document.createElementNS(ns, 'text')
      label.setAttribute('class', 'ln-node-label')
      label.setAttribute('x', String(pos.x + pos.w / 2))
      label.setAttribute('y', String(pos.y + pos.h / 2 - 6))
      const labelText = document.createTextNode(
        String(node.name || node.id).slice(0, 18) + (String(node.name || node.id).length > 18 ? '…' : '')
      )
      label.appendChild(labelText)

      const typeLbl = document.createElementNS(ns, 'text')
      typeLbl.setAttribute('class', 'ln-node-type')
      typeLbl.setAttribute('x', String(pos.x + pos.w / 2))
      typeLbl.setAttribute('y', String(pos.y + pos.h / 2 + 9))
      typeLbl.appendChild(document.createTextNode(node.type || ''))

      g.appendChild(rect)
      g.appendChild(label)
      g.appendChild(typeLbl)

      // Click handler — closure over node
      g.addEventListener('click', () => this._onNodeClick(node))
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') this._onNodeClick(node)
      })

      svg.appendChild(g)
    }

    body.innerHTML = ''
    body.appendChild(svg)
  }

  _onNodeClick(node) {
    this._selectedId = node.id
    // Re-render selections visually
    this._shadow.querySelectorAll('.ln-node').forEach(g => {
      g.classList.toggle('selected', g.getAttribute('data-id') === node.id)
    })
    this.dispatchEvent(new CustomEvent('nubi:select', {
      bubbles: true,
      composed: true,
      detail: { node },
    }))
  }

  async _render() {
    this._abort()
    const ac = new AbortController()
    this._ac = ac

    this._ensureScaffold()
    this._showLoading()

    const backend = this._backend()
    const nodeId  = this.getAttribute('node-id')
    const hops    = Math.min(20, Math.max(1, parseInt(this.getAttribute('hops') || '2', 10)))

    let token = null
    try { token = await resolveToken(this) } catch (_) { /* ignore */ }
    if (ac.signal.aborted) return

    if (backend) {
      try {
        const path = nodeId
          ? `/api/v1/lineage/dag/${encodeURIComponent(nodeId)}?hops=${hops}`
          : '/api/v1/lineage/dag'

        const raw = await fetchJson(backend, path, token, ac.signal)
        if (ac.signal.aborted) return

        // The neighbourhood response has a different shape — normalise it
        let dag
        if (nodeId && raw.node) {
          // Expand neighbourhood into flat DAG
          dag = _normaliseDagNeighbourhood(raw)
        } else {
          dag = _normaliseDag(raw)
        }

        this._renderDag(dag, false)
        this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
          bubbles: true, composed: true,
          detail: { nodes: dag.nodes.length, edges: dag.edges.length, renderer: 'lineage' },
        }))
        return
      } catch (err) {
        if (err.name === 'AbortError') return
        console.warn('[nubi-lineage] fetch failed:', err.message)
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
    const sample = makeSampleDag()
    this._renderDag(sample, true)
    this.dispatchEvent(new CustomEvent('nubi:widget-ready', {
      bubbles: true, composed: true,
      detail: { nodes: sample.nodes.length, edges: sample.edges.length, renderer: 'lineage' },
    }))
  }
}

// ---------------------------------------------------------------------------
// Backend response normalisers
// ---------------------------------------------------------------------------

/**
 * Normalise the full DAG response { nodes: [...], edges: [...] }.
 * Ensures every node has at minimum: id, type, name.
 */
function _normaliseDag(raw) {
  const rawNodes = Array.isArray(raw.nodes) ? raw.nodes : []
  const rawEdges = Array.isArray(raw.edges) ? raw.edges : []

  const nodes = rawNodes.map(n => ({
    id:      n.id || n.node_id || String(n.name || ''),
    type:    n.type || 'query',
    name:    n.name || n.id || '',
    tables:  n.tables || [],
    outputs: n.outputs || [],
    columns: n.columns || [],
  }))

  const edges = rawEdges.map(e => ({
    from: e.from || e.source || '',
    to:   e.to   || e.target || '',
    via:  e.via  || '',
  }))

  return { nodes, edges }
}

/**
 * Normalise the neighbourhood response
 * { node_id, node, hops, upstream: [...ids], downstream: [...ids] }
 * into a minimal flat DAG.
 */
function _normaliseDagNeighbourhood(raw) {
  const center = {
    id:   raw.node_id,
    type: raw.node?.type || 'query',
    name: raw.node?.name || raw.node_id,
    tables:  raw.node?.tables  || [],
    outputs: raw.node?.outputs || [],
    columns: raw.node?.columns || [],
  }

  const nodes = [center]
  const edges = []

  for (const upId of (raw.upstream || [])) {
    nodes.push({ id: upId, type: 'table', name: upId, tables: [], outputs: [], columns: [] })
    edges.push({ from: upId, to: raw.node_id, via: '' })
  }
  for (const downId of (raw.downstream || [])) {
    nodes.push({ id: downId, type: 'metric', name: downId, tables: [], outputs: [], columns: [] })
    edges.push({ from: raw.node_id, to: downId, via: '' })
  }

  return { nodes, edges }
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

if (!customElements.get('nubi-lineage')) {
  customElements.define('nubi-lineage', NubiLineage)
}

export { NubiLineage }
