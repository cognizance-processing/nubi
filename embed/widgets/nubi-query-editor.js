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

import { resolveToken }            from './shared.js'
import { decodeScopes, hasScope }  from '../nubi-context.js'
import { emitRun, emitSave, emitDirty, emitError } from '../events.js'
import { BASE_STYLES }             from './shared.js'
import { applyTheme }              from '../theme.js'

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
  .scope-badge.readonly { background: #450a0a; color: #fca5a5; }

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
    padding: 16px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .metric-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .metric-label {
    font-size: var(--nubi-font-size-sm, 11px);
    font-weight: 600;
    color: var(--nubi-fg-muted, #718096);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .metric-select, .metric-input {
    padding: 6px 10px;
    background: var(--nubi-bg-2, #1a1f2e);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: var(--nubi-radius-sm, 4px);
    color: var(--nubi-fg, #e2e8f0);
    font-size: var(--nubi-font-size-base, 13px);
  }
  .metric-select:disabled, .metric-input:disabled {
    opacity: 0.4; cursor: not-allowed;
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
  static get observedAttributes() {
    return ['token', 'get-token', 'backend', 'query-id', 'mode', 'theme', 'read-only']
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
      <div class="nubi-qe-metric-builder" style="display:none"></div>
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
      if (!window.__nubiMonacoWorkerConfigured) {
        window.MonacoEnvironment = {
          getWorkerUrl(_moduleId, label) {
            // Fallback: no worker (Monaco degrades gracefully to main-thread)
            return ''
          },
        }
        window.__nubiMonacoWorkerConfigured = true
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
    ta.style.cssText = `
      width: 100%; height: 100%;
      box-sizing: border-box;
      background: #0f1117; color: #e2e8f0;
      border: none; outline: none; resize: none;
      font-family: 'Fira Code', Consolas, monospace;
      font-size: 13px; padding: 12px; line-height: 1.5;
    `
    ta.value = this._currentSql || ''
    ta.addEventListener('input', () => {
      this._currentSql = ta.value
      const dirty = ta.value !== this._savedSql
      emitDirty(this, { dirty })
    })
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
      this._renderMetricBuilder(readOnly)
    }
  }

  // --------------------------------------------------------------------------
  // Metric builder UI
  // --------------------------------------------------------------------------

  _renderMetricBuilder(readOnly) {
    const builder = this._shadow.querySelector('.nubi-qe-metric-builder')
    builder.innerHTML = /* html */ `
      <div class="metric-row">
        <label class="metric-label">Metric</label>
        <select class="metric-select" ${readOnly ? 'disabled' : ''}>
          <option value="">Select a metric…</option>
          <option value="revenue">Revenue</option>
          <option value="orders">Orders</option>
          <option value="users">Active Users</option>
        </select>
      </div>
      <div class="metric-row">
        <label class="metric-label">Dimensions</label>
        <select class="metric-select" multiple style="height:80px" ${readOnly ? 'disabled' : ''}>
          <option value="date">Date</option>
          <option value="region">Region</option>
          <option value="product">Product</option>
          <option value="channel">Channel</option>
        </select>
      </div>
      <div class="metric-row">
        <label class="metric-label">Time Grain</label>
        <select class="metric-select" ${readOnly ? 'disabled' : ''}>
          <option value="day">Day</option>
          <option value="week">Week</option>
          <option value="month">Month</option>
          <option value="quarter">Quarter</option>
          <option value="year">Year</option>
        </select>
      </div>
    `
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
      // Metric mode — gather selections
      const builder = this._shadow.querySelector('.nubi-qe-metric-builder')
      const selects = builder?.querySelectorAll('.metric-select')
      const metricId   = selects?.[0]?.value || ''
      const dimSelect  = selects?.[1]
      const dimensions = dimSelect
        ? [...dimSelect.selectedOptions].map(o => o.value)
        : []
      const timeGrain  = selects?.[2]?.value || 'day'

      this._setStatus('Running metric…', '')
      emitRun(this, { metricId, dimensions, timeGrain })
      this._setStatus('Done', 'success')
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
