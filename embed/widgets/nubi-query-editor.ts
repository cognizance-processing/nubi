/**
 * nubi-query-editor.js — Scope-gated SQL / Metric query workspace.
 *
 * <nubi-query-editor> custom element.
 *
 * Attributes
 * ----------
 *  token       — static JWT string
 *  get-token   — name of a window.* function returning a JWT
 *  backend     — API base URL (default 'http://localhost:8000')
 *  query-id    — pre-load a registered query by ID
 *  mode        — 'sql' | 'metric' | 'auto' (default 'auto')
 *  theme       — 'dark' | 'light' (default 'dark')
 *  read-only   — boolean attribute; forces read-only regardless of scopes
 *
 * Capability gating (cosmetic UI — server is the real gate)
 * ----------------------------------------------------------
 *  author:sql    → SQL mode tab available, editor editable, Run + Save enabled
 *  author:metric → Metric mode tab available, editor editable, Run + Save enabled
 *  neither       → read-only (editor locked, no run/save buttons)
 *
 * Monaco shadow-DOM workaround
 * ----------------------------
 *  Monaco injects <style> into document.head and breaks inside shadow DOM.
 *  Solution: mount Monaco in a light-DOM wrapper div appended to document.body,
 *  positioned absolutely over a placeholder div in the shadow root.
 *  A ResizeObserver + scroll listener keep the overlay aligned.
 *
 * Events emitted (bubbles, composed)
 * ------------------------------------
 *  nubi:run    — { sql, queryId, params }
 *  nubi:save   — { sql, queryId, name }
 *  nubi:dirty  — { dirty: boolean }
 *  nubi:error  — { message, code }
 */

import { resolveToken, BASE_STYLES, escapeHtml, formatCell, fetchMetricList, fetchMetricQuery } from './shared.js'
import { decodeScopes, hasScope }  from '../nubi-context.js'
import { emitRun, emitSave, emitDirty, emitError } from '../events.js'
import { applyTheme }              from '../theme.js'

// ---------------------------------------------------------------------------
// Default metric definitions (fallback when no backend configured)
// ---------------------------------------------------------------------------

const DEFAULT_METRICS = [
  { id: 'revenue',      name: 'Revenue',      dimensions: ['date', 'region', 'product', 'channel'], timeGrains: ['day', 'week', 'month', 'quarter', 'year'] },
  { id: 'orders',       name: 'Orders',       dimensions: ['date', 'region', 'channel'],            timeGrains: ['day', 'week', 'month'] },
  { id: 'active_users', name: 'Active Users', dimensions: ['date', 'region', 'platform'],           timeGrains: ['day', 'week', 'month'] },
]

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const EDITOR_STYLES = /* css */ `
  ${BASE_STYLES}

  :host {
    display: flex;
    flex-direction: column;
    position: relative;
    min-height: 200px;
  }

  .nubi-qe-wrap {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  /* Toolbar */
  .nubi-qe-toolbar {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 10px;
    height: var(--nubi-toolbar-h, 36px);
    background: var(--nubi-bg-2, #1a1f2e);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    flex-shrink: 0;
  }

  .nubi-qe-toolbar .mode-tabs {
    display: flex;
    gap: 2px;
  }
  .mode-tab {
    padding: 3px 10px;
    font-size: var(--nubi-font-size-sm, 11px);
    font-weight: 600;
    border-radius: var(--nubi-radius-sm, 4px);
    border: 1px solid transparent;
    cursor: pointer;
    background: transparent;
    color: var(--nubi-fg-muted, #718096);
    transition: var(--nubi-transition, 0.15s ease);
    letter-spacing: 0.04em;
  }
  .mode-tab:hover { color: var(--nubi-fg, #e2e8f0); }
  .mode-tab.active {
    background: var(--nubi-accent, #1e2433);
    border-color: var(--nubi-border, #2d3748);
    color: var(--nubi-fg, #e2e8f0);
  }
  .mode-tab:disabled { opacity: 0.35; cursor: not-allowed; }

  .toolbar-spacer { flex: 1; }

  .scope-badge {
    font-size: var(--nubi-font-size-xs, 10px);
    padding: 2px 7px;
    border-radius: var(--nubi-radius-sm, 4px);
    font-weight: 700;
    letter-spacing: 0.05em;
    white-space: nowrap;
  }
  .scope-badge.sql    { background: #1e1b4b; color: #a5b4fc; }
  .scope-badge.metric { background: #064e3b; color: #6ee7b7; }
  .scope-badge.both   { background: #1e2433; color: #93c5fd; }
  .scope-badge.readonly { background: var(--nubi-bg-2, #1a1f2e); color: var(--nubi-fg-muted, #718096); border: 1px solid var(--nubi-border, #2d3748); }

  .btn-run, .btn-save {
    padding: 3px 12px;
    font-size: var(--nubi-font-size-sm, 11px);
    font-weight: 600;
    border-radius: var(--nubi-radius-sm, 4px);
    border: 1px solid transparent;
    cursor: pointer;
    transition: var(--nubi-transition, 0.15s ease);
  }
  .btn-run {
    background: var(--nubi-primary, #6366f1);
    color: var(--nubi-primary-fg, #fff);
    border-color: var(--nubi-primary, #6366f1);
  }
  .btn-run:hover { filter: brightness(1.15); }
  .btn-save {
    background: transparent;
    color: var(--nubi-fg-muted, #718096);
    border-color: var(--nubi-border, #2d3748);
  }
  .btn-save:hover { color: var(--nubi-fg, #e2e8f0); }
  .btn-run:disabled, .btn-save:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  /* Editor placeholder — Monaco sits over this in light DOM */
  .nubi-qe-placeholder {
    flex: 1;
    position: relative;
    min-height: 120px;
    background: var(--nubi-bg, #0f1117);
  }

  /* Fallback textarea for read-only mode (no Monaco dep) */
  .nubi-qe-textarea {
    width: 100%;
    height: 100%;
    box-sizing: border-box;
    background: var(--nubi-bg, #0f1117);
    color: var(--nubi-fg, #e2e8f0);
    border: none;
    outline: none;
    resize: none;
    font-family: var(--nubi-font-mono, monospace);
    font-size: var(--nubi-font-size-base, 13px);
    padding: 12px;
    line-height: var(--nubi-line-height, 1.5);
  }
  .nubi-qe-textarea:read-only { opacity: 0.7; cursor: default; }

  /* Metric builder (non-SQL mode) */
  .nubi-qe-metric-builder {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .nubi-qe-metric-controls {
    padding: 12px 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    background: var(--nubi-bg-2, #1a1f2e);
    flex-shrink: 0;
  }
  .metric-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 120px;
  }
  .metric-label {
    font-size: var(--nubi-font-size-sm, 11px);
    font-weight: 600;
    color: var(--nubi-fg-muted, #718096);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .metric-select, .metric-input {
    padding: 5px 8px;
    background: var(--nubi-bg, #0f1117);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: var(--nubi-radius-sm, 4px);
    color: var(--nubi-fg, #e2e8f0);
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .metric-select:disabled, .metric-input:disabled {
    opacity: 0.4; cursor: not-allowed;
  }
  /* Metric results table */
  .nubi-qe-metric-results {
    flex: 1;
    overflow: auto;
  }
  .qe-metric-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .qe-metric-table th {
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
  .qe-metric-table td {
    padding: 5px 10px;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    color: var(--nubi-fg, #e2e8f0);
  }
  .qe-metric-table tr:hover td { background: var(--nubi-accent, #1e2433); }
  .qe-metric-empty, .qe-metric-loading {
    padding: 24px;
    text-align: center;
    color: var(--nubi-fg-muted, #718096);
    font-size: var(--nubi-font-size-sm, 11px);
  }
  .qe-metric-error {
    margin: 12px;
    padding: 10px 14px;
    background: #1a0808;
    border: 1px solid var(--nubi-error, #ef4444);
    border-radius: var(--nubi-radius-sm, 4px);
    color: var(--nubi-error, #ef4444);
    font-size: var(--nubi-font-size-sm, 11px);
  }

  /* Status bar */
  .nubi-qe-status {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 12px;
    height: 24px;
    font-size: var(--nubi-font-size-xs, 10px);
    color: var(--nubi-fg-muted, #718096);
    background: var(--nubi-bg-2, #1a1f2e);
    border-top: 1px solid var(--nubi-border, #2d3748);
    flex-shrink: 0;
  }
  .status-error { color: var(--nubi-error, #ef4444); }
  .status-success { color: var(--nubi-success, #10b981); }
`

// ---------------------------------------------------------------------------
// Custom element
// ---------------------------------------------------------------------------

export class NubiQueryEditor extends HTMLElement {
  [key: string]: any

  static get observedAttributes() {
    return ['token', 'get-token', 'backend', 'query-id', 'metric-id', 'mode', 'theme', 'read-only']
  }

  constructor() {
    super()
    this._shadow = this.attachShadow({ mode: 'open' })

    // Stable ID for the light-DOM Monaco wrapper
    this._editorWrapId = `nubi-qe-${Math.random().toString(36).slice(2)}`
    this._editorWrapEl = null  // the light-DOM div in document.body
    this._monacoEditor  = null  // Monaco IEditor instance
    this._monacoModule  = null  // monaco namespace

    this._resizeObserver = null
    this._scrollHandler  = null
    this._ac = null

    // State
    this._scopes      = []
    this._currentSql  = ''
    this._savedSql    = ''
    this._activeMode  = 'sql'  // 'sql' | 'metric'
    this._statusMsg   = ''
    this._statusKind  = ''     // '' | 'error' | 'success'

    // Metric builder state
    this._backendMetrics = null  // fetched from /api/v1/metrics
    this._metricDef      = null  // currently selected metric definition
  }

  // --------------------------------------------------------------------------
  // Lifecycle
  // --------------------------------------------------------------------------

  connectedCallback() {
    applyTheme(this, this.getAttribute('theme') || 'dark')
    this._ensureScaffold()
    this._createLightDomWrapper()
    this._resolveAndGate()
    this._startResizeObserver()
    this._startScrollListener()
  }

  disconnectedCallback() {
    this._destroyMonaco()
    this._removeLightDomWrapper()
    this._resizeObserver?.disconnect()
    this._resizeObserver = null
    if (this._scrollHandler) {
      window.removeEventListener('scroll', this._scrollHandler, true)
      this._scrollHandler = null
    }
    this._abort()
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (oldVal === newVal) return
    if (name === 'theme') applyTheme(this, newVal || 'dark')
    if (name === 'metric-id' || name === 'backend') {
      // Reset cached metric state so next render re-fetches
      this._backendMetrics = null
      this._metricDef      = null
    }
    if (this.isConnected) this._resolveAndGate()
  }

  // --------------------------------------------------------------------------
  // Shadow DOM scaffold
  // --------------------------------------------------------------------------

  _ensureScaffold() {
    if (this._shadow.querySelector('.nubi-qe-wrap')) return

    const style = document.createElement('style')
    style.textContent = EDITOR_STYLES
    this._shadow.appendChild(style)

    const wrap = document.createElement('div')
    wrap.className = 'nubi-qe-wrap'
    wrap.innerHTML = /* html */ `
      <div class="nubi-qe-toolbar">
        <div class="mode-tabs"></div>
        <div class="toolbar-spacer"></div>
        <span class="scope-badge readonly">READ-ONLY</span>
        <button class="btn-save" disabled style="display:none">Save</button>
        <button class="btn-run"  disabled style="display:none">Run</button>
      </div>
      <div class="nubi-qe-placeholder"></div>
      <div class="nubi-qe-metric-builder" style="display:none">
        <div class="nubi-qe-metric-controls"></div>
        <div class="nubi-qe-metric-results"><div class="qe-metric-empty">Select a metric and click Run.</div></div>
      </div>
      <div class="nubi-qe-status"></div>
    `
    this._shadow.appendChild(wrap)

    // Wire up buttons
    this._shadow.querySelector('.btn-run').addEventListener('click', () => this._run())
    this._shadow.querySelector('.btn-save').addEventListener('click', () => this._save())
  }

  // --------------------------------------------------------------------------
  // Light-DOM Monaco wrapper (escapes shadow DOM limitation)
  // --------------------------------------------------------------------------

  _createLightDomWrapper() {
    if (this._editorWrapEl) return
    const div = document.createElement('div')
    div.id = this._editorWrapId
    // Start hidden, positioned correctly by _positionLightDomWrapper
    div.style.cssText = `
      position: absolute;
      top: 0; left: 0;
      width: 0; height: 0;
      z-index: var(--nubi-z-editor, 100);
      overflow: hidden;
      pointer-events: auto;
    `
    document.body.appendChild(div)
    this._editorWrapEl = div
    this._positionLightDomWrapper()
  }

  _removeLightDomWrapper() {
    if (this._editorWrapEl) {
      this._editorWrapEl.remove()
      this._editorWrapEl = null
    }
  }

  _positionLightDomWrapper() {
    const placeholder = this._shadow.querySelector('.nubi-qe-placeholder')
    if (!placeholder || !this._editorWrapEl) return

    const r = placeholder.getBoundingClientRect()
    const scrollX = window.scrollX || document.documentElement.scrollLeft
    const scrollY = window.scrollY || document.documentElement.scrollTop

    Object.assign(this._editorWrapEl.style, {
      top:    `${r.top  + scrollY}px`,
      left:   `${r.left + scrollX}px`,
      width:  `${r.width}px`,
      height: `${r.height}px`,
    })

    // Relay resize to Monaco
    if (this._monacoEditor) {
      this._monacoEditor.layout()
    }
  }

  _startResizeObserver() {
    const placeholder = this._shadow.querySelector('.nubi-qe-placeholder')
    if (!placeholder) return
    this._resizeObserver = new ResizeObserver(() => this._positionLightDomWrapper())
    this._resizeObserver.observe(placeholder)
    this._resizeObserver.observe(this)
  }

  _startScrollListener() {
    this._scrollHandler = () => this._positionLightDomWrapper()
    window.addEventListener('scroll', this._scrollHandler, { passive: true, capture: true })
  }

  // --------------------------------------------------------------------------
  // Monaco
  // --------------------------------------------------------------------------

  async _initMonaco(readOnly = false) {
    if (!this._editorWrapEl) return

    try {
      // Dynamic import — Monaco is a peer dep; Vite handles the workers via vite.authoring.config.js
      const monaco = await import('monaco-editor')
      this._monacoModule = monaco

      // Configure workers if not already done (light DOM context)
      if (!(window as any).__nubiMonacoWorkerConfigured) {
        window.MonacoEnvironment = {
          getWorkerUrl(_moduleId) {
            // Fallback: no worker (Monaco degrades gracefully to main-thread)
            return ''
          },
        }
        ;(window as any).__nubiMonacoWorkerConfigured = true
      }

      const theme = this.getAttribute('theme') || 'dark'
      monaco.editor.defineTheme('nubi-dark', {
        base: 'vs-dark',
        inherit: true,
        rules: [],
        colors: {
          'editor.background': '#0f1117',
          'editor.foreground': '#e2e8f0',
          'editorLineNumber.foreground': '#4a5568',
          'editor.lineHighlightBackground': '#1a1f2e',
        },
      })
      monaco.editor.defineTheme('nubi-light', {
        base: 'vs',
        inherit: true,
        rules: [],
        colors: {
          'editor.background': '#ffffff',
          'editor.foreground': '#1a202c',
        },
      })

      const monacoTheme = theme === 'light' ? 'nubi-light' : 'nubi-dark'

      this._monacoEditor = monaco.editor.create(this._editorWrapEl, {
        value: this._currentSql || '',
        language: 'sql',
        theme: monacoTheme,
        readOnly,
        minimap: { enabled: false },
        lineNumbers: 'on',
        wordWrap: 'on',
        fontSize: 13,
        padding: { top: 8, bottom: 8 },
        scrollBeyondLastLine: false,
        automaticLayout: false, // we call .layout() manually via ResizeObserver
        glyphMargin: false,
      })

      // Ctrl/Cmd+Enter → Run
      this._monacoEditor.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
        () => this._run()
      )

      // Dirty tracking
      this._monacoEditor.onDidChangeModelContent(() => {
        const sql = this._monacoEditor.getValue()
        this._currentSql = sql
        const dirty = sql !== this._savedSql
        emitDirty(this, { dirty })
      })

      this._positionLightDomWrapper()
    } catch (err) {
      // Monaco not available (e.g. in tests); fall back to plain textarea
      console.warn('[nubi-query-editor] Monaco unavailable, using textarea fallback:', err.message)
      this._mountTextareaFallback(readOnly)
    }
  }

  _mountTextareaFallback(readOnly) {
    if (!this._editorWrapEl) return
    const ta = document.createElement('textarea')
    ta.className = 'nubi-qe-textarea'
    ta.readOnly = readOnly
    ta.placeholder = readOnly ? '-- Read-only mode' : '-- Write SQL here…'
    // Use CSS custom properties via the host element's computed style for theming;
    // hard-coded fallbacks mirror the dark theme defaults.
    ta.style.cssText = `
      width: 100%; height: 100%;
      box-sizing: border-box;
      background: var(--nubi-bg, #0f1117);
      color: var(--nubi-fg, #e2e8f0);
      border: none; outline: none; resize: none;
      font-family: var(--nubi-font-mono, 'Fira Code', Consolas, monospace);
      font-size: var(--nubi-font-size-base, 13px);
      padding: 12px;
      line-height: var(--nubi-line-height, 1.5);
      opacity: ${readOnly ? '0.7' : '1'};
      cursor: ${readOnly ? 'default' : 'text'};
    `
    ta.value = this._currentSql || ''
    ta.addEventListener('input', () => {
      this._currentSql = ta.value
      const dirty = ta.value !== this._savedSql
      emitDirty(this, { dirty })
    })
    // Ctrl/Cmd+Enter → Run (mirrors Monaco keybinding)
    if (!readOnly) {
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault()
          this._run()
        }
      })
    }
    this._editorWrapEl.appendChild(ta)
    this._textareaEl = ta
  }

  _destroyMonaco() {
    if (this._monacoEditor) {
      this._monacoEditor.dispose()
      this._monacoEditor = null
    }
    if (this._editorWrapEl) {
      this._editorWrapEl.innerHTML = ''
    }
    this._textareaEl = null
  }

  // --------------------------------------------------------------------------
  // Scope / capability gating
  // --------------------------------------------------------------------------

  async _resolveAndGate() {
    this._abort()
    this._ac = new AbortController()

    try {
      const token = await resolveToken(this)
      this._scopes = decodeScopes(token)
      this._applyCapabilityGating()
    } catch (err) {
      this._scopes = []
      this._applyCapabilityGating()
      emitError(this, { message: err.message })
    }
  }

  _applyCapabilityGating() {
    const hasSql    = hasScope(this._scopes, 'author:sql')
    const hasMetric = hasScope(this._scopes, 'author:metric')
    const forceRO   = this.hasAttribute('read-only')
    const readOnly  = forceRO || (!hasSql && !hasMetric)

    const modeAttr = this.getAttribute('mode') || 'auto'

    // Resolve active mode
    if (modeAttr === 'sql')    this._activeMode = 'sql'
    else if (modeAttr === 'metric') this._activeMode = 'metric'
    else {
      // auto: prefer sql if available, else metric, else sql (read-only)
      if (hasSql) this._activeMode = 'sql'
      else if (hasMetric) this._activeMode = 'metric'
      else this._activeMode = 'sql'
    }

    this._renderToolbar(hasSql, hasMetric, readOnly)
    this._renderEditorPane(readOnly)
  }

  _renderToolbar(hasSql, hasMetric, readOnly) {
    const tabs = this._shadow.querySelector('.mode-tabs')
    const badge = this._shadow.querySelector('.scope-badge')
    const btnRun  = this._shadow.querySelector('.btn-run')
    const btnSave = this._shadow.querySelector('.btn-save')

    // Mode tabs
    tabs.innerHTML = ''
    if (hasSql) {
      const t = this._makeTab('SQL', 'sql')
      tabs.appendChild(t)
    }
    if (hasMetric) {
      const t = this._makeTab('METRIC', 'metric')
      tabs.appendChild(t)
    }
    this._syncActiveTabs()

    // Scope badge
    badge.className = 'scope-badge'
    if (readOnly) {
      badge.classList.add('readonly')
      badge.textContent = 'READ-ONLY'
    } else if (hasSql && hasMetric) {
      badge.classList.add('both')
      badge.textContent = 'SQL + METRIC'
    } else if (hasSql) {
      badge.classList.add('sql')
      badge.textContent = 'SQL'
    } else {
      badge.classList.add('metric')
      badge.textContent = 'METRIC'
    }

    // Action buttons
    const canEdit = !readOnly
    btnRun.disabled  = !canEdit
    btnSave.disabled = !canEdit
    btnRun.style.display  = canEdit ? '' : 'none'
    btnSave.style.display = canEdit ? '' : 'none'
  }

  _makeTab(label, mode) {
    const btn = document.createElement('button')
    btn.className = `mode-tab${this._activeMode === mode ? ' active' : ''}`
    btn.textContent = label
    btn.dataset.mode = mode
    btn.addEventListener('click', () => this._switchMode(mode))
    return btn
  }

  _syncActiveTabs() {
    this._shadow.querySelectorAll('.mode-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.mode === this._activeMode)
    })
  }

  _switchMode(mode) {
    if (mode === this._activeMode) return
    this._activeMode = mode
    this._syncActiveTabs()
    this._renderEditorPane(false)
  }

  _renderEditorPane(readOnly) {
    const placeholder = this._shadow.querySelector('.nubi-qe-placeholder')
    const metricBuilder = this._shadow.querySelector('.nubi-qe-metric-builder')

    if (this._activeMode === 'sql') {
      placeholder.style.display = ''
      metricBuilder.style.display = 'none'
      // Init Monaco if not yet done
      if (!this._monacoEditor && !this._textareaEl) {
        this._initMonaco(readOnly)
      } else if (this._monacoEditor) {
        this._monacoEditor.updateOptions({ readOnly })
        this._positionLightDomWrapper()
      } else if (this._textareaEl) {
        this._textareaEl.readOnly = readOnly
      }
    } else {
      // Metric builder mode — hide Monaco overlay
      placeholder.style.display = 'none'
      if (this._editorWrapEl) this._editorWrapEl.style.display = 'none'
      metricBuilder.style.display = ''
      // _renderMetricBuilder is async (may fetch metric list); fire-and-forget is fine
      this._renderMetricBuilder(readOnly).catch(() => {})
    }
  }

  // --------------------------------------------------------------------------
  // Metric builder UI
  // --------------------------------------------------------------------------

  /**
   * Render (or re-render) the metric controls panel.
   * When a backend is configured, fetch the live metric list first; fall back
   * to DEFAULT_METRICS when there is no backend or the fetch fails.
   */
  async _renderMetricBuilder(readOnly) {
    const controls = this._shadow.querySelector('.nubi-qe-metric-controls')
    if (!controls) return

    const backend  = (this.getAttribute('backend') || '').replace(/\/$/, '')
    const metricId = this.getAttribute('metric-id') || ''

    // Fetch live metric list when backend is configured (once per load)
    if (backend && !this._backendMetrics) {
      try {
        const token = await resolveToken(this)
        this._backendMetrics = await fetchMetricList(backend, token, this._ac?.signal)
      } catch { /* fall through to defaults */ }
    }

    // Resolve the current metric definition
    if (!this._metricDef) {
      if (backend && this._backendMetrics) {
        this._metricDef = this._backendMetrics.find(m => m.id === metricId) ||
                          this._backendMetrics[0] || DEFAULT_METRICS[0]
      } else if (backend && metricId) {
        // Backend configured but list fetch failed — synthesise minimal def
        this._metricDef = { id: metricId, name: metricId, dimensions: [], timeGrains: ['day', 'week', 'month'] }
      } else {
        this._metricDef = DEFAULT_METRICS.find(m => m.id === metricId) || DEFAULT_METRICS[0]
      }
    }

    this._buildMetricControls(controls, readOnly)
  }

  /**
   * Populate the metric controls panel DOM from `this._metricDef` and
   * `this._backendMetrics` (or DEFAULT_METRICS as fallback).
   */
  _buildMetricControls(controls, readOnly) {
    controls.innerHTML = ''

    const backend = (this.getAttribute('backend') || '').replace(/\/$/, '')
    const def = this._metricDef || DEFAULT_METRICS[0]

    // Metric selector
    const metricRow = document.createElement('div')
    metricRow.className = 'metric-row'
    const metricLabel = document.createElement('div')
    metricLabel.className = 'metric-label'
    metricLabel.textContent = 'Metric'
    const metricSelect = document.createElement('select')
    metricSelect.className = 'metric-select'
    metricSelect.disabled = readOnly
    metricSelect.dataset.role = 'metric'

    const metricOptions = backend && this._backendMetrics?.length
      ? this._backendMetrics.map(m => ({ id: m.id, name: m.name }))
      : DEFAULT_METRICS.map(m => ({ id: m.id, name: m.name }))

    metricOptions.forEach(m => {
      const opt = document.createElement('option')
      opt.value = m.id
      opt.textContent = m.name
      opt.selected = m.id === def.id
      metricSelect.appendChild(opt)
    })
    metricSelect.addEventListener('change', () => this._onMetricBuilderChange(metricSelect.value))
    metricRow.appendChild(metricLabel)
    metricRow.appendChild(metricSelect)
    controls.appendChild(metricRow)

    // Dimensions (multiple select)
    const dimRow = document.createElement('div')
    dimRow.className = 'metric-row'
    const dimLabel = document.createElement('div')
    dimLabel.className = 'metric-label'
    dimLabel.textContent = 'Dimensions'
    const dimSelect = document.createElement('select')
    dimSelect.className = 'metric-select'
    dimSelect.disabled = readOnly
    dimSelect.multiple = true
    dimSelect.style.height = '70px'
    dimSelect.dataset.role = 'dimensions'
    ;(def.dimensions || []).forEach(d => {
      const opt = document.createElement('option')
      opt.value = d
      opt.textContent = d
      opt.selected = true
      dimSelect.appendChild(opt)
    })
    dimRow.appendChild(dimLabel)
    dimRow.appendChild(dimSelect)
    controls.appendChild(dimRow)

    // Time grain
    const grainRow = document.createElement('div')
    grainRow.className = 'metric-row'
    const grainLabel = document.createElement('div')
    grainLabel.className = 'metric-label'
    grainLabel.textContent = 'Time Grain'
    const grainSelect = document.createElement('select')
    grainSelect.className = 'metric-select'
    grainSelect.disabled = readOnly
    grainSelect.dataset.role = 'time-grain'
    // When backend configured: add "— none —" as default (avoids forcing VARCHAR time columns)
    if (backend) {
      const noneOpt = document.createElement('option')
      noneOpt.value = ''
      noneOpt.textContent = '— none —'
      grainSelect.appendChild(noneOpt)
    }
    ;(def.timeGrains || ['day', 'week', 'month']).forEach(g => {
      const opt = document.createElement('option')
      opt.value = g
      opt.textContent = g.charAt(0).toUpperCase() + g.slice(1)
      grainSelect.appendChild(opt)
    })
    grainRow.appendChild(grainLabel)
    grainRow.appendChild(grainSelect)
    controls.appendChild(grainRow)
  }

  _onMetricBuilderChange(newId) {
    const backend = (this.getAttribute('backend') || '').replace(/\/$/, '')
    if (backend && this._backendMetrics) {
      this._metricDef = this._backendMetrics.find(m => m.id === newId) ||
                        { id: newId, name: newId, dimensions: [], timeGrains: ['day', 'week', 'month'] }
    } else {
      this._metricDef = DEFAULT_METRICS.find(m => m.id === newId) || DEFAULT_METRICS[0]
    }
    // Re-render controls to update dimensions + time grains for the new metric
    const controls = this._shadow.querySelector('.nubi-qe-metric-controls')
    if (controls) this._buildMetricControls(controls, false)
  }

  _getMetricSelections() {
    const controls = this._shadow.querySelector('.nubi-qe-metric-controls')
    const metricSelect = controls?.querySelector('[data-role="metric"]')
    const dimSelect    = controls?.querySelector('[data-role="dimensions"]')
    const grainSelect  = controls?.querySelector('[data-role="time-grain"]')

    const metricId   = metricSelect?.value || this._metricDef?.id || ''
    const dimensions = dimSelect
      ? [...dimSelect.selectedOptions].map(o => o.value)
      : []
    const timeGrain  = grainSelect?.value || ''
    return { metricId, dimensions, timeGrain }
  }

  _renderMetricResults(table) {
    const resultsEl = this._shadow.querySelector('.nubi-qe-metric-results')
    if (!resultsEl) return

    if (!table || table.numRows === 0) {
      resultsEl.innerHTML = '<div class="qe-metric-empty">No results.</div>'
      return
    }

    const fields = table.schema.fields.map(f => f.name)
    const tbl = document.createElement('table')
    tbl.className = 'qe-metric-table'

    const thead = document.createElement('thead')
    const headerRow = document.createElement('tr')
    fields.forEach(f => {
      const th = document.createElement('th')
      th.textContent = f
      headerRow.appendChild(th)
    })
    thead.appendChild(headerRow)
    tbl.appendChild(thead)

    const tbody = document.createElement('tbody')
    for (let r = 0; r < table.numRows; r++) {
      const row = document.createElement('tr')
      fields.forEach(f => {
        const col = table.getChild(f)
        const val = col ? col.get(r) : null
        const td = document.createElement('td')
        td.textContent = formatCell(val)
        row.appendChild(td)
      })
      tbody.appendChild(row)
    }
    tbl.appendChild(tbody)

    resultsEl.innerHTML = ''
    resultsEl.appendChild(tbl)
  }

  // --------------------------------------------------------------------------
  // Run / Save
  // --------------------------------------------------------------------------

  async _run() {
    if (this._activeMode === 'sql') {
      const sql = this._monacoEditor
        ? this._monacoEditor.getValue()
        : (this._textareaEl?.value || this._currentSql)

      this._setStatus('Running…', '')
      try {
        const token = await resolveToken(this)
        const backend = (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')
        const headers = { 'Content-Type': 'application/json', 'Accept': 'application/vnd.apache.arrow.stream' }
        if (token) headers['Authorization'] = `Bearer ${token}`

        const queryId = this.getAttribute('query-id') || null
        const body = queryId ? { query_id: queryId } : { sql }

        const resp = await fetch(`${backend}/api/v1/query`, {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
          credentials: 'omit',
          signal: this._ac?.signal,
        })

        if (!resp.ok) {
          const msg = `HTTP ${resp.status}`
          this._setStatus(msg, 'error')
          emitError(this, { message: msg, code: String(resp.status) })
          return
        }

        this._setStatus('Done', 'success')
        emitRun(this, { sql: queryId ? undefined : sql, queryId, params: [] })
      } catch (err) {
        if (err.name === 'AbortError') return
        this._setStatus(err.message, 'error')
        emitError(this, { message: err.message })
      }
    } else {
      // Metric mode — gather selections and POST to governed metric endpoint
      const { metricId, dimensions, timeGrain } = this._getMetricSelections()

      if (!metricId) {
        this._setStatus('Select a metric first', 'error')
        return
      }

      const resultsEl = this._shadow.querySelector('.nubi-qe-metric-results')
      if (resultsEl) resultsEl.innerHTML = '<div class="qe-metric-loading">Running metric query…</div>'
      this._setStatus('Running metric…', '')

      // Emit nubi:run immediately with the selection (mirrors old behaviour for no-backend use);
      // a second emit with rowCount follows on successful backend response.
      emitRun(this, { metricId, dimensions, timeGrain })

      this._abort()
      this._ac = new AbortController()

      try {
        const token   = await resolveToken(this)
        const backend = (this.getAttribute('backend') || 'http://localhost:8000').replace(/\/$/, '')

        const table = await fetchMetricQuery(backend, metricId, dimensions, timeGrain, token, this._ac.signal)
        this._renderMetricResults(table)

        const rowCount = table.numRows
        this._setStatus(`${rowCount.toLocaleString()} rows`, 'success')
        emitRun(this, { metricId, dimensions, timeGrain, rowCount })
      } catch (err) {
        if (err.name === 'AbortError') return
        if (resultsEl) resultsEl.innerHTML = `<div class="qe-metric-error">${escapeHtml(err.message)}</div>`
        this._setStatus(err.message, 'error')
        emitError(this, { message: err.message })
      }
    }
  }

  async _save() {
    const sql = this._monacoEditor
      ? this._monacoEditor.getValue()
      : (this._textareaEl?.value || this._currentSql)

    const queryId = this.getAttribute('query-id') || null
    this._savedSql = sql
    emitSave(this, { sql, queryId, name: null })
    emitDirty(this, { dirty: false })
    this._setStatus('Saved', 'success')
  }

  _setStatus(msg, kind) {
    const status = this._shadow.querySelector('.nubi-qe-status')
    if (!status) return
    status.textContent = msg
    status.className = `nubi-qe-status${kind ? ` status-${kind}` : ''}`
  }

  // --------------------------------------------------------------------------
  // Misc helpers
  // --------------------------------------------------------------------------

  _abort() {
    if (this._ac) { this._ac.abort(); this._ac = null }
  }
}
