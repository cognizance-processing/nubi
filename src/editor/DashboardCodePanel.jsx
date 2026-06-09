/**
 * DashboardCodePanel.jsx — Monaco-based "Code" panel for the Dashboard Editor.
 *
 * The trigger button lives in the editor toolbar (which is portaled into the
 * app topbar). The panel itself is rendered through `createPortal` into
 * `document.body` as a fixed right-hand slide-over.
 *
 * WHY a portal: the topbar slot and the editor toolbar are `overflow-x-auto`
 * containers (so the toolbar can scroll on narrow screens). Per the CSS spec,
 * `overflow-x: auto` forces `overflow-y` to compute to `auto` as well, so any
 * `position: absolute` dropdown anchored inside them is clipped to the ~56px
 * bar — the panel used to open but was 100% invisible ("Code does nothing").
 * Portaling to <body> with `position: fixed` makes the panel immune to every
 * ancestor overflow/transform.
 *
 * Features
 * --------
 *   - JSON / YAML syntax highlighting (language follows the format toggle).
 *   - Two-stage validation surfaced inline (never silently dropped):
 *       1. Parse errors  → Monaco markers (red squiggles) + problems bar.
 *       2. Spec errors   → structural DashboardSpec validation via
 *          `src/dashboards/validateSpec.js` (mirrors the backend validator in
 *          backend/app/dashboards/spec.py). Invalid specs cannot be applied.
 *   - View mode (read-only, follows live editor state) and Edit mode.
 *   - Live flow: Edit → "Apply to editor" re-renders the canvas next to the
 *     panel (the slide-over leaves the canvas visible) → main Save button
 *     persists. "Save to server" upserts directly via POST /import, which
 *     re-validates server-side.
 *
 * Props
 * -----
 *   kind     {'dashboard'}           Resource kind.
 *   spec     {object}                In-memory spec (view mode source).
 *   onApply  {(spec:object) => void} Push a parsed spec into the editor state.
 *   board    {string|null}           Saved board id for server export/upsert.
 */

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import Editor from '@monaco-editor/react'
import { Code2, Download, Upload, Copy, Check, X, FileUp, AlertCircle, CloudUpload } from 'lucide-react'
import yaml from 'js-yaml'
import { get, post } from '../lib/api.js'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { validateDashboardSpec } from '../dashboards/validateSpec.js'

// ---------------------------------------------------------------------------
// Helpers (mirror SpecIO helpers — kept local so SpecIO is untouched)
// ---------------------------------------------------------------------------

const API_VERSION = 'nubi/v1'

function slugify(s) {
  return (
    (s || 'resource')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '') || 'resource'
  )
}

function buildEnvelope(kind, spec, board) {
  const name = spec?.title || 'Untitled dashboard'
  const metadata = { name }
  if (board) metadata.id = board
  return { kind, apiVersion: API_VERSION, metadata, spec: spec ?? {} }
}

function dumpEnvelope(envelope, format) {
  if (format === 'json') return JSON.stringify(envelope, null, 2)
  return yaml.dump(envelope, { noRefs: true, lineWidth: 100, sortKeys: false })
}

function parseDoc(text) {
  return yaml.load(text) // handles both YAML and JSON
}

function extractSpec(doc) {
  if (doc && typeof doc === 'object' && 'spec' in doc && doc.spec && typeof doc.spec === 'object') {
    return doc.spec
  }
  return doc
}

function download(filename, content, mime) {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ---------------------------------------------------------------------------
// Validation: parse markers (Monaco) + structural spec issues
// ---------------------------------------------------------------------------

/**
 * Validate `text` as JSON or YAML and return a Monaco-compatible markers array.
 * On success returns []. On error returns a single error marker with line/col
 * info extracted from the parse exception when available.
 */
function parseMarkers(text, format, monacoSeverity) {
  if (!text || !text.trim()) return []
  try {
    if (format === 'json') {
      JSON.parse(text)
    } else {
      yaml.load(text)
    }
    return []
  } catch (err) {
    let line = 1
    let col = 1
    if (err.mark) {
      // js-yaml parse error carries `.mark.line/.column` (0-based)
      line = (err.mark.line ?? 0) + 1
      col = (err.mark.column ?? 0) + 1
    } else if (format === 'json') {
      // Map V8's "... at position N" to line/col.
      const posMatch = err.message?.match(/position\s+(\d+)/i)
      if (posMatch) {
        const pos = parseInt(posMatch[1], 10)
        const before = text.slice(0, pos)
        line = (before.match(/\n/g) ?? []).length + 1
        col = pos - before.lastIndexOf('\n')
      }
    }
    return [{
      severity: monacoSeverity ?? 8, // 8 = MarkerSeverity.Error
      message: err.message || 'Parse error',
      startLineNumber: line,
      startColumn: col,
      endLineNumber: line,
      endColumn: col + 1,
    }]
  }
}

/**
 * Structural spec issues for the current draft. Only meaningful when the
 * document parses; parse failures are reported by `parseMarkers` instead.
 * @returns {string[]}
 */
function specIssuesFor(text, format) {
  if (!text || !text.trim()) return []
  let doc
  try {
    doc = format === 'json' ? JSON.parse(text) : yaml.load(text)
  } catch {
    return [] // parse error already surfaced as a marker
  }
  return validateDashboardSpec(extractSpec(doc))
}

// ---------------------------------------------------------------------------
// DashboardCodePanel
// ---------------------------------------------------------------------------

export default function DashboardCodePanel({ kind = 'dashboard', spec, onApply, board = null }) {
  // Theme — ThemeProvider wraps the entire app (src/main.jsx), so this is safe.
  const { theme } = useTheme()
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState('view')   // 'view' | 'edit'
  const [format, setFormat] = useState('yaml')
  const [copied, setCopied] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(null)     // 'export' | 'import'
  const [markers, setMarkers] = useState([])       // parse errors (Monaco markers)
  const [specIssues, setSpecIssues] = useState([]) // structural spec issues

  const fileRef = useRef(null)
  const editorRef = useRef(null)
  const monacoRef = useRef(null)
  const validateTimer = useRef(null)

  // ── Derived: the envelope text for the current spec + format ──────────────
  const envelope = useMemo(() => buildEnvelope(kind, spec, board), [kind, spec, board])
  const viewText = useMemo(() => {
    try { return dumpEnvelope(envelope, format) } catch (e) { return `# Serialisation failed: ${e.message}` }
  }, [envelope, format])

  const baseName = slugify(envelope.metadata.name)
  const savedId = envelope.metadata.id

  // Monaco language maps to format
  const language = format === 'json' ? 'json' : 'yaml'

  // ── Close on Escape (the slide-over intentionally does NOT close on
  //     outside click — clicking the canvas is part of the preview flow) ─────
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // Reset transient state when the panel closes.
  useEffect(() => {
    if (open) return
    setError(null); setNotice(null); setCopied(false); setMarkers([]); setSpecIssues([])
  }, [open])

  // When switching to edit mode, seed the draft from the current view text.
  useEffect(() => {
    if (mode === 'edit' && !draft) {
      setDraft(viewText)
    }
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Re-validate edit draft when format changes.
  useEffect(() => {
    if (mode === 'edit' && draft) {
      scheduleValidation(draft)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [format])

  // ── Validation ────────────────────────────────────────────────────────────
  const runValidation = useCallback((text) => {
    const monaco = monacoRef.current
    const editor = editorRef.current
    const severity = monaco?.MarkerSeverity?.Error ?? 8
    const newMarkers = parseMarkers(text, format, severity)
    setMarkers(newMarkers)
    setSpecIssues(newMarkers.length ? [] : specIssuesFor(text, format))

    if (monaco && editor) {
      const model = editor.getModel()
      if (model) {
        monaco.editor.setModelMarkers(model, 'nubi-spec', newMarkers)
      }
    }
  }, [format])

  const scheduleValidation = useCallback((text) => {
    if (validateTimer.current) clearTimeout(validateTimer.current)
    validateTimer.current = setTimeout(() => runValidation(text), 300)
  }, [runValidation])

  useEffect(() => () => { if (validateTimer.current) clearTimeout(validateTimer.current) }, [])

  // ── Monaco mount ──────────────────────────────────────────────────────────
  const handleMount = useCallback((editor, monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco
    if (mode === 'edit') {
      runValidation(editor.getModel()?.getValue() ?? '')
    }
  }, [mode, runValidation])

  const handleChange = useCallback((val) => {
    const next = val ?? ''
    setDraft(next)
    setError(null); setNotice(null)
    scheduleValidation(next)
  }, [scheduleValidation])

  // ── Copy ──────────────────────────────────────────────────────────────────
  function copyCode() {
    const text = mode === 'view' ? viewText : draft
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  // ── Export ────────────────────────────────────────────────────────────────
  async function exportFile() {
    setError(null)
    const ext = format === 'json' ? 'json' : 'yaml'
    const mime = format === 'json' ? 'application/json' : 'application/yaml'
    if (savedId && format === 'json') {
      setBusy('export')
      try {
        const data = await get(`/export/${kind}/${savedId}?format=json`)
        download(`${baseName}.json`, JSON.stringify(data, null, 2), mime)
        setBusy(null)
        return
      } catch {
        setBusy(null)
      }
    }
    download(`${baseName}.${ext}`, mode === 'view' ? viewText : draft, mime)
  }

  /**
   * Parse + structurally validate the current draft.
   * Surfaces errors inline and returns null when invalid.
   * @returns {object|null} the extracted spec, or null
   */
  function validatedDraftSpec() {
    if (!draft.trim()) { setError('Paste or edit a YAML / JSON document first.'); return null }
    let doc
    try { doc = parseDoc(draft) } catch (e) { setError(`Parse error: ${e.message}`); return null }
    const nextSpec = extractSpec(doc)
    if (!nextSpec || typeof nextSpec !== 'object') {
      setError('Could not find a spec to apply in that document.')
      return null
    }
    const issues = validateDashboardSpec(nextSpec)
    setSpecIssues(issues)
    if (issues.length) {
      setError(`Spec validation failed (${issues.length} issue${issues.length !== 1 ? 's' : ''}) — fix the problems listed below.`)
      return null
    }
    return nextSpec
  }

  // ── Apply (validated) ─────────────────────────────────────────────────────
  function applyDraft() {
    setError(null); setNotice(null)
    const nextSpec = validatedDraftSpec()
    if (!nextSpec) return
    try {
      onApply?.(nextSpec)
      setNotice('Applied — the canvas now previews this spec. Use Save to persist.')
    } catch (e) {
      setError(`Apply failed: ${e.message}`)
    }
  }

  // ── Save to server (validated client-side; server re-validates) ──────────
  async function importDraft() {
    setError(null); setNotice(null)
    const nextSpec = validatedDraftSpec()
    if (!nextSpec) return
    setBusy('import')
    try {
      // Always send a well-formed envelope so /import can route + upsert.
      const env = buildEnvelope(kind, nextSpec, savedId ?? null)
      const saved = await post('/import', env)
      setNotice(`Saved "${saved?.name ?? env.metadata.name}" to the server.`)
    } catch (e) {
      setError(e.message || 'Save failed.')
    } finally {
      setBusy(null)
    }
  }

  // ── File picker ───────────────────────────────────────────────────────────
  function onPickFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const text = String(reader.result ?? '')
      setDraft(text)
      setMode('edit')
      setError(null); setNotice(null)
      scheduleValidation(text)
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  // ── Shared styles ─────────────────────────────────────────────────────────
  const itemCls = 'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-fg rounded-lg hover:bg-surface-2 disabled:opacity-50 transition-colors text-left'

  // Content shown in the Monaco editor (view = read-only spec; edit = draft)
  const editorValue = mode === 'view' ? viewText : draft
  const editorReadOnly = mode === 'view'

  // Problems = parse errors + structural spec issues (only meaningful in edit)
  const problemCount = mode === 'edit' ? markers.length + specIssues.length : 0

  const panel = (
    <aside
      data-testid="dashboard-code-panel"
      aria-label="Dashboard code editor"
      className="fixed right-0 top-14 bottom-0 z-40 w-[min(42rem,100vw)] bg-surface border-l border-border shadow-2xl flex flex-col"
    >
      {/* Header: title + mode + format toggles */}
      <div className="flex items-center gap-2 px-3 h-11 border-b border-border shrink-0">
        <Code2 size={14} className="text-muted shrink-0" />
        <span className="text-xs font-semibold text-fg whitespace-nowrap hidden sm:inline">Dashboard code</span>

        {/* Mode toggle */}
        <div className="flex items-center rounded-lg border border-border overflow-hidden ml-1">
          {[
            { v: 'view', l: 'View' },
            { v: 'edit', l: 'Edit' },
          ].map(opt => (
            <button
              key={opt.v}
              type="button"
              data-testid={`code-mode-${opt.v}`}
              onClick={() => {
                if (opt.v === 'edit' && !draft) setDraft(viewText)
                setMode(opt.v)
                setError(null); setNotice(null)
              }}
              className={`h-7 px-2.5 text-[11px] font-medium transition-colors ${
                mode === opt.v
                  ? 'bg-primary/10 text-primary'
                  : 'bg-surface text-muted hover:text-fg hover:bg-surface-2'
              } ${opt.v === 'edit' ? 'border-l border-border' : ''}`}
            >
              {opt.l}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        {/* Format toggle */}
        <div className="flex items-center rounded-lg border border-border overflow-hidden">
          {['yaml', 'json'].map(f => (
            <button
              key={f}
              type="button"
              onClick={() => {
                setFormat(f)
                // If the edit draft is still the untouched view text, convert it.
                if (mode === 'edit' && draft === dumpEnvelope(envelope, format === 'yaml' ? 'yaml' : 'json')) {
                  setDraft(dumpEnvelope(envelope, f))
                }
              }}
              className={`h-7 px-2.5 text-[11px] font-medium uppercase transition-colors ${
                format === f
                  ? 'bg-primary/10 text-primary'
                  : 'bg-surface text-muted hover:text-fg hover:bg-surface-2'
              } ${f === 'json' ? 'border-l border-border' : ''}`}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Copy */}
        <button
          type="button"
          onClick={copyCode}
          className="h-7 w-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          title="Copy to clipboard"
        >
          {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
        </button>

        {/* Close */}
        <button
          type="button"
          data-testid="code-panel-close"
          onClick={() => setOpen(false)}
          className="h-7 w-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
          title="Close (Esc)"
        >
          <X size={14} />
        </button>
      </div>

      {/* Monaco editor — fills the panel height */}
      <div className="relative flex-1 min-h-0">
        <Editor
          height="100%"
          language={language}
          theme={monacoTheme}
          value={editorValue}
          onChange={editorReadOnly ? undefined : handleChange}
          onMount={handleMount}
          options={{
            readOnly: editorReadOnly,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 12,
            lineNumbers: 'on',
            wordWrap: 'on',
            tabSize: 2,
            automaticLayout: true,
            padding: { top: 8, bottom: 8 },
            overviewRulerLanes: editorReadOnly ? 0 : 3,
            quickSuggestions: !editorReadOnly,
            scrollbar: { vertical: 'auto', horizontal: 'auto' },
            glyphMargin: !editorReadOnly,
            lineDecorationsWidth: editorReadOnly ? 0 : 8,
          }}
          loading={
            <div className="flex items-center justify-center h-full text-xs text-muted bg-surface-2">
              Loading editor…
            </div>
          }
        />
      </div>

      {/* Problems bar (VS Code style) — parse + spec issues, edit mode only */}
      <div
        data-testid="code-problems"
        className={`px-3 py-1.5 border-t text-[11px] shrink-0 transition-colors ${
          problemCount > 0
            ? 'border-red-500/30 bg-red-500/5'
            : 'border-border bg-surface-2/50'
        }`}
      >
        {problemCount > 0 ? (
          <div className="space-y-0.5 max-h-20 overflow-y-auto">
            <div className="flex items-center gap-1.5">
              <AlertCircle size={12} className="text-red-500 shrink-0" />
              <span className="text-red-500 font-medium">
                {problemCount} problem{problemCount !== 1 ? 's' : ''}
              </span>
            </div>
            {markers.map((m, i) => (
              <p key={`m${i}`} className="text-red-500/90 pl-[18px] truncate" title={m.message}>
                Ln {m.startLineNumber}: {m.message}
              </p>
            ))}
            {specIssues.map((msg, i) => (
              <p key={`s${i}`} className="text-red-500/90 pl-[18px] truncate" title={msg}>
                {msg}
              </p>
            ))}
          </div>
        ) : (
          <div className="h-4 flex items-center">
            {mode === 'edit' ? (
              <span className="text-muted/60">No problems detected</span>
            ) : (
              <span className="text-muted/60">
                {format === 'json' ? 'JSON' : 'YAML'} · {savedId ? 'saved board' : 'unsaved board'} · read-only — switch to Edit to change
              </span>
            )}
          </div>
        )}
      </div>

      {/* Actions footer */}
      <div className="p-3 space-y-2.5 border-t border-border shrink-0">
        {mode === 'view' ? (
          /* View mode: export + copy */
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={exportFile}
              disabled={busy === 'export'}
              className={itemCls + ' border border-border !w-auto flex-1 justify-center py-1.5'}
            >
              <Download size={14} className="text-muted" />
              {busy === 'export' ? 'Exporting…' : `Download .${format === 'json' ? 'json' : 'yaml'}`}
            </button>
            <button
              type="button"
              onClick={copyCode}
              className={itemCls + ' border border-border !w-auto flex-1 justify-center py-1.5'}
            >
              {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} className="text-muted" />}
              Copy
            </button>
          </div>
        ) : (
          /* Edit mode: file picker + apply + save */
          <>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className={itemCls + ' border border-border !w-auto justify-center py-1.5'}
                title="Load a .yaml / .json file"
              >
                <FileUp size={14} className="text-muted" /> File…
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".yaml,.yml,.json,application/json,text/yaml"
                className="hidden"
                onChange={onPickFile}
              />
              <button
                type="button"
                onClick={() => {
                  const text = viewText
                  setDraft(text)
                  scheduleValidation(text)
                }}
                className={itemCls + ' border border-border !w-auto justify-center py-1.5'}
                title="Reset the draft to the current editor state"
              >
                <Code2 size={14} className="text-muted" /> Use current
              </button>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                data-testid="code-apply-btn"
                onClick={applyDraft}
                disabled={problemCount > 0}
                className="flex-1 h-8 px-3 text-xs font-semibold rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity flex items-center justify-center gap-1.5"
                title="Validate and apply this spec to the canvas (does not save)"
              >
                <Upload size={13} /> Apply to editor
              </button>
              <button
                type="button"
                data-testid="code-save-btn"
                onClick={importDraft}
                disabled={busy === 'import' || problemCount > 0}
                className="flex-1 h-8 px-3 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-1.5"
                title={savedId ? 'Validate and save this spec to the server (updates this board)' : 'Validate and create a new board from this spec'}
              >
                <CloudUpload size={13} />
                {busy === 'import' ? 'Saving…' : savedId ? 'Save to server' : 'Create on server'}
              </button>
            </div>

            <p className="text-[10px] text-muted/70 leading-relaxed">
              <span className="font-medium text-fg/80">Apply to editor</span> previews the spec on the canvas (then use the main Save button).{' '}
              <span className="font-medium text-fg/80">{savedId ? 'Save to server' : 'Create on server'}</span> validates and persists it directly.
            </p>
          </>
        )}

        {(error || notice) && (
          <div data-testid="code-panel-status">
            {error && <p className="text-[11px] text-rose-500">{error}</p>}
            {notice && <p className="text-[11px] text-emerald-600 dark:text-emerald-400">{notice}</p>}
          </div>
        )}
      </div>
    </aside>
  )

  return (
    <>
      {/* Trigger button — lives in the (overflow-clipped) toolbar; the panel
          itself is portaled to <body> so it can never be clipped. */}
      <button
        type="button"
        data-testid="dashboard-code-btn"
        onClick={() => setOpen(o => !o)}
        aria-pressed={open}
        className={`px-2.5 h-8 text-xs font-medium rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring/60 flex items-center gap-1.5 whitespace-nowrap ${
          open
            ? 'bg-surface-2 border-primary text-primary'
            : 'bg-surface text-fg border-border hover:bg-surface-2'
        }`}
        title="View or edit the dashboard spec as code"
      >
        <Code2 size={14} />
        <span className="hidden sm:inline">Code</span>
      </button>

      {open && createPortal(panel, document.body)}
    </>
  )
}
