/**
 * SpecIO.jsx — Export / Import / "view-as-code" for portable, LLM-editable
 * dashboard & query resources.
 *
 * A resource is represented as a versioned ENVELOPE:
 *
 *   { kind, apiVersion: 'nubi/v1', metadata: { name, id?, project? }, spec }
 *
 * where `kind` is 'dashboard' or 'query'. (Connectors are intentionally NOT
 * supported.) The envelope is the unit of portability: YAML is the primary,
 * human/LLM-friendly representation; JSON is offered as a toggle.
 *
 * Backend contract (built by a parallel agent):
 *   GET  /export/{kind}/{id}?format=yaml|json  → the resource as an envelope
 *   POST /import  (YAML or JSON body)          → upsert, returns the resource
 *
 * Everything in this menu works on the IN-MEMORY spec, so "View as code" and
 * "Export" function even for unsaved edits. "Create from file" round-trips
 * through POST /import for cross-resource portability.
 *
 * Props
 * -----
 * kind     {'dashboard'|'query'}  Resource kind (drives the envelope + filename).
 * spec     {object}               The in-memory spec to view/export.
 * onApply  {(spec) => void}       Apply a parsed spec back into the editor state.
 * board    {string|null}          Dashboard id when saved (kind === 'dashboard').
 * query    {object|null}          Query record when saved (kind === 'query').
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Code2, Download, Upload, Copy, Check, X, FileUp, AlertCircle } from 'lucide-react'
import yaml from 'js-yaml'
import { get, post } from '../lib/api.js'
import CodeEditor from './CodeEditor.jsx'

// ---------------------------------------------------------------------------
// helpers
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

/** Resolve a human name + saved id for the resource being viewed. */
function resolveMeta(kind, spec, board, query) {
  if (kind === 'dashboard') {
    return { name: spec?.title || 'Untitled dashboard', id: board || undefined }
  }
  // query
  return { name: query?.name || 'Untitled query', id: query?.id || undefined }
}

/** Build the canonical envelope from the in-memory spec. */
function buildEnvelope(kind, spec, board, query) {
  const meta = resolveMeta(kind, spec, board, query)
  const metadata = { name: meta.name }
  if (meta.id) metadata.id = meta.id
  return { kind, apiVersion: API_VERSION, metadata, spec: spec ?? {} }
}

/** Serialise an envelope to YAML or JSON text. */
function dumpEnvelope(envelope, format) {
  if (format === 'json') return JSON.stringify(envelope, null, 2)
  return yaml.dump(envelope, { noRefs: true, lineWidth: 100, sortKeys: false })
}

/**
 * Parse a pasted/uploaded document (YAML or JSON) into an object. js-yaml's
 * loader is a strict superset of JSON, so a single call handles both.
 */
function parseDoc(text) {
  return yaml.load(text)
}

/** Extract the `spec` from a doc that may be a full envelope or a bare spec. */
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
// component
// ---------------------------------------------------------------------------

/**
 * Parse JSON text and return an array of Monaco-style markers for any
 * syntax errors. Returns [] when the JSON is valid.
 */
function jsonMarkers(text) {
  try {
    JSON.parse(text)
    return []
  } catch (e) {
    // V8's SyntaxError message often contains "at position N" or "line M col N".
    const msg = e.message ?? 'JSON syntax error'
    // Try to extract line/col from the message.
    const posMatch = msg.match(/line (\d+) column (\d+)/i)
    const line = posMatch ? parseInt(posMatch[1], 10) : 1
    const col = posMatch ? parseInt(posMatch[2], 10) : 1
    return [{ line, col, message: msg, severity: 'error' }]
  }
}

/**
 * Parse YAML text and return Monaco markers for parse errors.
 * js-yaml throws YAMLException with mark.line / mark.column.
 */
function yamlMarkers(text) {
  try {
    yaml.load(text)
    return []
  } catch (e) {
    const line = (e.mark?.line ?? 0) + 1
    const col = (e.mark?.column ?? 0) + 1
    return [{ line, col, message: e.reason ?? e.message ?? 'YAML error', severity: 'error' }]
  }
}

export default function SpecIO({ kind, spec, onApply, board = null, query = null }) {
  const [open, setOpen] = useState(false)
  // 'view' | 'edit' — view shows code; edit accepts a paste + Apply/Import.
  const [mode, setMode] = useState('view')
  const [format, setFormat] = useState('yaml') // 'yaml' | 'json'
  const [copied, setCopied] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null) // 'export' | 'import'
  const [notice, setNotice] = useState(null)
  const ref = useRef(null)
  const fileRef = useRef(null)
  // Track problem count from the Monaco view editor (for the problems indicator)
  const [viewMarkerCount, setViewMarkerCount] = useState(0)

  const envelope = useMemo(
    () => buildEnvelope(kind, spec, board, query),
    [kind, spec, board, query],
  )
  const codeText = useMemo(() => {
    try { return dumpEnvelope(envelope, format) } catch (e) { return `# Failed to serialise: ${e.message}` }
  }, [envelope, format])

  // Compute Monaco markers for the view-mode code (highlight spec errors).
  const viewMarkers = useMemo(() => {
    if (format === 'json') return jsonMarkers(codeText)
    // For YAML, validate; but serialisation errors shouldn't normally happen
    // since we build the envelope ourselves. Still, guard for robustness.
    return yamlMarkers(codeText)
  }, [codeText, format])

  // Keep the problems count in sync so we can show the badge.
  useEffect(() => {
    setViewMarkerCount(viewMarkers.length)
  }, [viewMarkers])

  const baseName = slugify(envelope.metadata.name)
  const savedId = envelope.metadata.id

  // Close on Escape. NOTE: the panel is portaled to <body> as a fixed
  // slide-over (see below), so it lives outside `ref` — an outside-click
  // listener would fire on the panel's own content and close it immediately.
  // The explicit close (X) button is the only pointer affordance.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // Reset transient state when the menu closes.
  useEffect(() => {
    if (open) return
    setError(null); setNotice(null); setCopied(false)
  }, [open])

  function copyCode() {
    navigator.clipboard?.writeText(codeText).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  // ── Export: download envelope (.yaml/.json) from the in-memory spec ─────────
  async function exportFile() {
    setError(null)
    const ext = format === 'json' ? 'json' : 'yaml'
    const mime = format === 'json' ? 'application/json' : 'application/yaml'
    // When saved, prefer the server's canonical envelope (JSON path, since the
    // shared HTTP client parses JSON). Fall back to the in-memory render.
    if (savedId && format === 'json') {
      setBusy('export')
      try {
        const data = await get(`/export/${kind}/${savedId}?format=json`)
        download(`${baseName}.json`, JSON.stringify(data, null, 2), mime)
        setBusy(null)
        return
      } catch {
        // Fall through to client-side render below.
        setBusy(null)
      }
    }
    download(`${baseName}.${ext}`, codeText, mime)
  }

  // ── Apply: parse the draft and push the spec into the editor state ──────────
  function applyDraft() {
    setError(null); setNotice(null)
    if (!draft.trim()) { setError('Paste a YAML or JSON document first.'); return }
    let doc
    try { doc = parseDoc(draft) } catch (e) { setError(`Parse error: ${e.message}`); return }
    const nextSpec = extractSpec(doc)
    if (!nextSpec || typeof nextSpec !== 'object') {
      setError('Could not find a spec to apply in that document.')
      return
    }
    try {
      onApply?.(nextSpec)
      setNotice('Applied to the editor.')
      setTimeout(() => setOpen(false), 700)
    } catch (e) {
      setError(`Apply failed: ${e.message}`)
    }
  }

  // ── Import: POST the draft to the server (create/update the resource) ───────
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

  function onPickFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => { setDraft(String(reader.result ?? '')); setMode('edit'); setError(null); setNotice(null) }
    reader.readAsText(file)
    e.target.value = '' // allow re-picking the same file
  }

  const itemCls =
    'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-fg rounded-lg hover:bg-surface-2 disabled:opacity-50 transition-colors text-left'

  // The slide-over panel. Portaled to <body> as a `position: fixed` element
  // so it can NEVER be clipped by an ancestor's overflow. WHY this matters:
  // SpecIO's trigger lives in the query-workspace toolbar, which is portaled
  // into the AppShell topbar slot (`overflow-x-auto`). Per the CSS spec,
  // `overflow-x: auto` forces `overflow-y` to compute to `auto` too, so an
  // `position: absolute` dropdown anchored there was clipped to the ~56px bar
  // and rendered at ~0px height — the Code button "did nothing". Fixed-position
  // portaling mirrors the dashboard fix (src/editor/DashboardCodePanel.jsx).
  const panel = (
    <aside
      data-testid="query-code-panel"
      aria-label="Query code editor"
      className="fixed right-0 top-14 bottom-0 z-40 w-[min(42rem,100vw)] bg-surface border-l border-border shadow-2xl flex flex-col"
    >
      {/* Header: title + mode + format toggles */}
      <div className="flex items-center gap-2 px-3 h-11 border-b border-border shrink-0">
            <Code2 size={14} className="text-muted shrink-0" />
            <span className="text-xs font-semibold text-fg whitespace-nowrap hidden sm:inline">
              {kind === 'query' ? 'Query code' : 'Resource code'}
            </span>
            <div className="flex items-center rounded-lg border border-border overflow-hidden ml-1">
              {[
                { v: 'view', l: 'View' },
                { v: 'edit', l: 'Edit / Import' },
              ].map(opt => (
                <button
                  key={opt.v}
                  type="button"
                  onClick={() => { setMode(opt.v); setError(null); setNotice(null) }}
                  className={`h-7 px-2.5 text-[11px] font-medium transition-colors ${
                    mode === opt.v ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2'
                  } ${opt.v === 'edit' ? 'border-l border-border' : ''}`}
                >
                  {opt.l}
                </button>
              ))}
            </div>

            <div className="flex-1" />

            <div className="flex items-center rounded-lg border border-border overflow-hidden">
              {['yaml', 'json'].map(f => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFormat(f)}
                  className={`h-7 px-2.5 text-[11px] font-medium uppercase transition-colors ${
                    format === f ? 'bg-primary/10 text-primary' : 'bg-surface text-muted hover:text-fg hover:bg-surface-2'
                  } ${f === 'json' ? 'border-l border-border' : ''}`}
                >
                  {f}
                </button>
              ))}
            </div>

            <button
              type="button"
              data-testid="query-code-panel-close"
              onClick={() => setOpen(false)}
              className="h-7 w-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              title="Close (Esc)"
            >
              <X size={14} />
            </button>
          </div>

          {/* Body */}
          {mode === 'view' ? (
            <div className="p-3 space-y-2.5 flex-1 min-h-0 flex flex-col">
              {/* Monaco editor — read-only, with error squiggles for invalid JSON/YAML */}
              <div className="relative flex-1 min-h-0">
                <CodeEditor
                  value={codeText}
                  language={format === 'json' ? 'json' : 'yaml'}
                  markers={viewMarkers}
                  markerId="nubi-spec"
                  height="100%"
                  readOnly
                  fontSize={12}
                  lineNumbers="off"
                  wordWrap="on"
                  minimap={false}
                  padding={{ top: 6, bottom: 6 }}
                />
                {/* Problems badge — shown when there are errors */}
                {viewMarkerCount > 0 && (
                  <div className="absolute bottom-1.5 left-1.5 flex items-center gap-1 px-1.5 py-0.5 rounded bg-rose-500/15 border border-rose-500/30 text-rose-500 text-[10px] font-semibold pointer-events-none select-none">
                    <AlertCircle size={9} />
                    {viewMarkerCount} problem{viewMarkerCount !== 1 ? 's' : ''}
                  </div>
                )}
                {/* Copy button */}
                <button
                  type="button"
                  onClick={copyCode}
                  className="absolute top-1.5 right-1.5 p-1.5 rounded-lg border border-border bg-surface hover:text-primary hover:border-primary text-muted transition-colors z-10"
                  title="Copy to clipboard"
                >
                  {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
                </button>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  type="button"
                  onClick={exportFile}
                  disabled={busy === 'export'}
                  className={itemCls + ' border border-border !w-auto flex-1 justify-center'}
                >
                  <Download size={15} className="text-muted" />
                  {busy === 'export' ? 'Exporting…' : `Download .${format === 'json' ? 'json' : 'yaml'}`}
                </button>
                <button
                  type="button"
                  onClick={copyCode}
                  className={itemCls + ' border border-border !w-auto flex-1 justify-center'}
                >
                  {copied ? <Check size={15} className="text-emerald-500" /> : <Copy size={15} className="text-muted" />}
                  Copy
                </button>
              </div>
              <p className="text-[10px] text-muted/70 leading-relaxed shrink-0">
                The <span className="font-mono">{API_VERSION}</span> envelope above reflects your current
                {savedId ? ' saved' : ' unsaved'} edits — portable & LLM-editable.
              </p>
            </div>
          ) : (
            <div className="p-3 space-y-2.5 flex-1 min-h-0 flex flex-col overflow-y-auto">
              <textarea
                value={draft}
                onChange={e => { setDraft(e.target.value); setError(null); setNotice(null) }}
                placeholder={`Paste a ${format.toUpperCase()} envelope (or bare spec) here…`}
                spellCheck={false}
                className="w-full flex-1 min-h-[12rem] text-[11px] leading-relaxed font-mono bg-surface-2 border border-border rounded-lg p-2.5 text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-ring/40 resize-none"
              />

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  className={itemCls + ' border border-border !w-auto justify-center'}
                  title="Load a .yaml / .json file into the editor below"
                >
                  <FileUp size={15} className="text-muted" /> File…
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
                  onClick={() => setDraft(codeText)}
                  className={itemCls + ' border border-border !w-auto justify-center'}
                  title="Prefill from the current resource"
                >
                  <Code2 size={15} className="text-muted" /> Use current
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
                <span className="font-medium text-fg/80">Apply to editor</span> loads the spec into this editor (review, then Save).
                <span className="font-medium text-fg/80"> Create from file</span> upserts it server-side via <span className="font-mono">/import</span>.
              </p>
            </div>
          )}

      {(error || notice) && (
        <div className="px-3 pb-3 -mt-1 shrink-0" data-testid="query-code-panel-status">
          {error && <p className="text-[11px] text-rose-500">{error}</p>}
          {notice && <p className="text-[11px] text-emerald-600 dark:text-emerald-400">{notice}</p>}
        </div>
      )}
    </aside>
  )

  return (
    <div className="relative" ref={ref}>
      {/* Trigger — stays inline in the (overflow-clipped) toolbar; the panel
          itself is portaled to <body> so it can never be clipped. */}
      <button
        type="button"
        data-testid="query-code-btn"
        onClick={() => setOpen(o => !o)}
        aria-pressed={open}
        className={`px-2.5 h-8 text-xs font-medium rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring/60 flex items-center gap-1.5 whitespace-nowrap ${
          open ? 'bg-surface-2 border-primary text-primary' : 'bg-surface text-fg border-border hover:bg-surface-2'
        }`}
        title="View as code, export or import this resource"
      >
        <Code2 size={14} />
        <span className="hidden sm:inline">Code</span>
      </button>

      {open && createPortal(panel, document.body)}
    </div>
  )
}
