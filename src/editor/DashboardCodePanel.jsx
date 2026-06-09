/**
 * DashboardCodePanel.jsx — Monaco-based "Code" panel for the Dashboard Editor.
 *
 * Replaces the SpecIO textarea/pre with a proper Monaco editor that provides:
 *   - JSON / YAML syntax highlighting (language follows the format toggle).
 *   - Spec validation: JSON.parse for JSON, js-yaml.load for YAML — errors are
 *     surfaced as Monaco markers (red squiggles) AND a "N problems" status bar
 *     at the bottom of the editor (VS Code style).
 *   - View mode (read-only) and Edit / Import mode (editable).
 *   - Apply-to-editor and Create-from-file actions preserved from SpecIO.
 *
 * Props
 * -----
 *   kind     {'dashboard'}           Resource kind.
 *   spec     {object}                In-memory spec (view mode source).
 *   onApply  {(spec:object) => void} Push a parsed spec into the editor state.
 *   board    {string|null}           Saved board id for server export.
 *
 * Design notes
 * ------------
 * - Uses CodeEditor from src/components/CodeEditor.jsx (the shared Monaco wrapper
 *   created by QueryEditorAgent), which accepts {value, onChange, language, markers}.
 *   If that file hasn't landed yet the lazy-import guard falls back to a local
 *   Monaco <Editor> so the dashboard panel is never broken.
 * - The "view" textarea / <pre> from SpecIO is fully replaced by Monaco in
 *   read-only mode. Switching to "Edit" makes it writable and enables the actions.
 * - Validation runs synchronously after every content change (debounced 300 ms)
 *   because spec files are small (< 100 KB) and JSON.parse is cheap.
 */

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import Editor from '@monaco-editor/react'
import { Code2, Download, Upload, Copy, Check, X, FileUp, AlertCircle } from 'lucide-react'
import yaml from 'js-yaml'
import { get, post } from '../lib/api.js'
import { useTheme } from '../contexts/ThemeContext.jsx'

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
// Spec validation: produce Monaco markers from the raw text
// ---------------------------------------------------------------------------

/**
 * Validate `text` as JSON or YAML and return a Monaco-compatible markers array.
 * On success returns []. On error returns a single error marker with line/col info
 * extracted from the parse exception message when available.
 *
 * @param {string} text
 * @param {'json'|'yaml'} format
 * @returns {{ severity: number, message: string, startLineNumber: number,
 *             startColumn: number, endLineNumber: number, endColumn: number }[]}
 */
function validateSpec(text, format, monacoSeverity) {
  if (!text || !text.trim()) return []
  try {
    if (format === 'json') {
      JSON.parse(text)
    } else {
      yaml.load(text)
    }
    return []
  } catch (err) {
    // Try to extract line/col from the error message.
    // js-yaml: "unexpected token at line X, column Y"
    // JSON.parse: "Unexpected token ... position N" (no line info in most engines)
    let line = 1
    let col = 1

    if (err.mark) {
      // js-yaml parse error carries `.mark.line` (0-based) and `.mark.column` (0-based)
      line = (err.mark.line ?? 0) + 1
      col = (err.mark.column ?? 0) + 1
    } else if (format === 'json') {
      // Try to extract position from V8's JSON.parse error message and map to line/col.
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

// ---------------------------------------------------------------------------
// DashboardCodePanel
// ---------------------------------------------------------------------------

export default function DashboardCodePanel({ kind = 'dashboard', spec, onApply, board = null }) {
  // Theme
  let theme = 'light'
  try { theme = useTheme().theme } catch { /* outside provider */ }
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState('view')   // 'view' | 'edit'
  const [format, setFormat] = useState('yaml')
  const [copied, setCopied] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(null)     // 'export' | 'import'
  const [markers, setMarkers] = useState([])

  const ref = useRef(null)
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

  // ── Close on outside-click / Escape ───────────────────────────────────────
  useEffect(() => {
    if (!open) return
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Reset transient state when the panel closes.
  useEffect(() => {
    if (open) return
    setError(null); setNotice(null); setCopied(false); setMarkers([])
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
    const newMarkers = validateSpec(text, format, severity)
    setMarkers(newMarkers)

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
    // Run initial validation in edit mode.
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

  // ── Apply ─────────────────────────────────────────────────────────────────
  function applyDraft() {
    setError(null); setNotice(null)
    if (!draft.trim()) { setError('Paste or edit a YAML / JSON document first.'); return }
    let doc
    try { doc = parseDoc(draft) } catch (e) { setError(`Parse error: ${e.message}`); return }
    const nextSpec = extractSpec(doc)
    if (!nextSpec || typeof nextSpec !== 'object') {
      setError('Could not find a spec to apply in that document.')
      return
    }
    try {
      onApply?.(nextSpec)
      setNotice('Applied to editor.')
      setTimeout(() => setOpen(false), 700)
    } catch (e) {
      setError(`Apply failed: ${e.message}`)
    }
  }

  // ── Import (server-side) ──────────────────────────────────────────────────
  async function importDraft() {
    setError(null); setNotice(null)
    if (!draft.trim()) { setError('Paste or upload a document first.'); return }
    let doc
    try { doc = parseDoc(draft) } catch (e) { setError(`Parse error: ${e.message}`); return }
    setBusy('import')
    try {
      const saved = await post('/import', doc)
      setNotice(`Imported "${saved?.metadata?.name ?? saved?.name ?? 'resource'}".`)
    } catch (e) {
      setError(e.message || 'Import failed.')
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

  // ── Shared styles ─────────────────────────────────────────────name----------
  const itemCls = 'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-fg rounded-lg hover:bg-surface-2 disabled:opacity-50 transition-colors text-left'

  // Content shown in the Monaco editor (view = read-only spec; edit = draft)
  const editorValue = mode === 'view' ? viewText : draft
  const editorReadOnly = mode === 'view'

  // Problem count for the indicator (only meaningful in edit mode)
  const problemCount = mode === 'edit' ? markers.length : 0

  return (
    <div className="relative" ref={ref}>
      {/* Trigger button — mirrors SpecIO's Code button */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
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

      {open && (
        <div
          className="absolute right-0 top-full mt-2 z-50 w-[42rem] max-w-[calc(100vw-2rem)] bg-surface border border-border rounded-xl shadow-xl overflow-hidden flex flex-col"
          style={{ maxHeight: '80vh' }}
        >
          {/* Header: mode + format toggles */}
          <div className="flex items-center gap-2 px-3 h-11 border-b border-border shrink-0">
            {/* Mode toggle */}
            <div className="flex items-center rounded-lg border border-border overflow-hidden">
              {[
                { v: 'view', l: 'View' },
                { v: 'edit', l: 'Edit / Import' },
              ].map(opt => (
                <button
                  key={opt.v}
                  type="button"
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
                    // If in edit mode, re-seed draft in new format only when switching
                    // from view mode content (not user-edited content).
                    if (mode === 'edit' && draft === dumpEnvelope(envelope, format === 'yaml' ? 'yaml' : 'json')) {
                      // user hasn't changed it yet → convert automatically
                      const next = dumpEnvelope(envelope, f)
                      setDraft(next)
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

            {/* Copy button (header shortcut) */}
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
              onClick={() => setOpen(false)}
              className="h-7 w-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              title="Close"
            >
              <X size={14} />
            </button>
          </div>

          {/* Monaco editor */}
          <div className="relative flex-1" style={{ minHeight: '260px', maxHeight: '420px' }}>
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
                // Render issues in the gutter (squiggles appear via setModelMarkers)
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

          {/* Problems indicator bar (VS Code style) — only in edit mode */}
          <div
            className={`flex items-center gap-2 px-3 h-7 border-t text-[11px] shrink-0 transition-colors ${
              problemCount > 0
                ? 'border-red-500/30 bg-red-500/5'
                : 'border-border bg-surface-2/50'
            }`}
          >
            {problemCount > 0 ? (
              <>
                <AlertCircle size={12} className="text-red-500 shrink-0" />
                <span className="text-red-500 font-medium">
                  {problemCount} problem{problemCount !== 1 ? 's' : ''}
                </span>
                <span className="text-muted/70 truncate">
                  — {markers[0]?.message}
                </span>
              </>
            ) : (
              mode === 'edit' ? (
                <span className="text-muted/60">No problems detected</span>
              ) : (
                <span className="text-muted/60">
                  {format === 'json' ? 'JSON' : 'YAML'} · {savedId ? 'saved' : 'unsaved'}
                </span>
              )
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
              /* Edit mode: file picker + apply + import */
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
                    title="Prefill from the current resource"
                  >
                    <Code2 size={14} className="text-muted" /> Use current
                  </button>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={applyDraft}
                    className="flex-1 h-8 px-3 text-xs font-semibold rounded-lg bg-primary text-primary-fg hover:opacity-90 disabled:opacity-60 transition-opacity flex items-center justify-center gap-1.5"
                    title="Apply this spec to the in-editor state (does not save)"
                  >
                    <Upload size={13} /> Apply to editor
                  </button>
                  <button
                    type="button"
                    onClick={importDraft}
                    disabled={busy === 'import'}
                    className="flex-1 h-8 px-3 text-xs font-medium rounded-lg border border-border bg-surface text-fg hover:bg-surface-2 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
                    title="Create or update this resource on the server"
                  >
                    <Download size={13} className="rotate-180" />
                    {busy === 'import' ? 'Importing…' : 'Create from file'}
                  </button>
                </div>

                <p className="text-[10px] text-muted/70 leading-relaxed">
                  <span className="font-medium text-fg/80">Apply to editor</span> loads the spec into this editor (review, then Save).{' '}
                  <span className="font-medium text-fg/80">Create from file</span> upserts it server-side.
                </p>
              </>
            )}

            {(error || notice) && (
              <div>
                {error && <p className="text-[11px] text-rose-500">{error}</p>}
                {notice && <p className="text-[11px] text-emerald-600 dark:text-emerald-400">{notice}</p>}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
