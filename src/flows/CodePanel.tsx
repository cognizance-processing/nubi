/**
 * CodePanel.jsx — Editable nubi.flows Python SDK panel.
 *
 * Replaces the read-only CodeViewer with a first-class code editor.
 * Code is primary: edits here drive the canvas via "Apply code".
 *
 * Props:
 *   flowId         {string|null}  — persisted flow id; null for unsaved flows
 *   spec           {object|null}  — current FlowSpec (used when flowId is null)
 *   onSpecChange   {Function}     — called with updated spec after Apply
 *   onClose        {Function}     — dismiss the panel
 *
 * Behaviour:
 *   - On mount / when flowId or spec changes, fetches generated Python source
 *     via POST /flows/{id}/codegen or POST /flows/codegen.
 *   - The editor is EDITABLE — user can freely modify the generated scaffold.
 *   - "Apply code" round-trips Python → FlowSpec via POST /flows/compile
 *     (subprocess-sandboxed on the backend) and calls onSpecChange with the
 *     result, syncing the canvas.
 *   - Copy-to-clipboard always available.
 *   - Unsaved edits are tracked: a dot indicator shows the panel is dirty.
 *   - Errors (compile failures, network) surface inline — never silent.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import Editor from '@monaco-editor/react'
import {
  X,
  Copy,
  Check,
  Loader2,
  AlertCircle,
  Code2,
  Play,
  RotateCcw,
  Dot,
} from 'lucide-react'
import { post } from '../lib/api.js'
import { compileCode } from '../lib/flows.js'

// ---------------------------------------------------------------------------
// Codegen fetcher (same as CodeViewer but we keep it local)
// ---------------------------------------------------------------------------

async function fetchCodegen(flowId, spec) {
  const BASE = '/flows'
  try {
    let data
    if (flowId) {
      data = await post(`${BASE}/${flowId}/codegen`, {})
    } else if (spec) {
      data = await post(`${BASE}/codegen`, { spec })
    } else {
      return { source: null, error: 'No flow id or spec provided.' }
    }
    return { source: data?.source ?? data?.code ?? null, error: null }
  } catch (err) {
    if (err?.status === 404 || err?.message?.includes('404')) {
      return { source: null, error: null, unavailable: true }
    }
    return { source: null, error: err.message ?? 'Codegen request failed.' }
  }
}

// ---------------------------------------------------------------------------
// CodePanel
// ---------------------------------------------------------------------------

export default function CodePanel({ flowId, spec, onSpecChange, onClose }) {
  // Async load state
  const [loadState, setLoadState] = useState({
    loading: true,
    source: null,
    error: null,
    unavailable: false,
  })

  // Current value in the editor (may differ from loadState.source once edited)
  const [editorValue, setEditorValue] = useState(null)

  // Whether the user has edited the source since the last load/apply
  const [dirty, setDirty] = useState(false)

  // Apply / compile state
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState(null)
  const [applySuccess, setApplySuccess] = useState(false)

  // Copy state
  const [copied, setCopied] = useState(false)

  // Track the last spec/flowId we loaded so we only refetch when they change.
  const lastLoadKeyRef = useRef(null)

  // Stable key for deciding when to re-fetch.
  const loadKey = flowId
    ? `id:${flowId}`
    : spec
      ? `spec:${JSON.stringify(spec)}`
      : null

  useEffect(() => {
    if (!loadKey || loadKey === lastLoadKeyRef.current) return
    lastLoadKeyRef.current = loadKey

    let cancelled = false

    // Kick off the fetch. All state writes happen inside async callbacks so
    // the effect body itself never calls setState synchronously.
    const run = async () => {
      setLoadState({ loading: true, source: null, error: null, unavailable: false })
      setDirty(false)
      setApplyError(null)
      setApplySuccess(false)

      const result = await fetchCodegen(flowId, spec)
      if (cancelled) return
      const src = result.source ?? null
      setLoadState({
        loading: false,
        source: src,
        error: result.error ?? null,
        unavailable: result.unavailable ?? false,
      })
      setEditorValue(src)
    }
    run()

    return () => { cancelled = true }
  }, [loadKey, flowId, spec])

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleEditorChange = useCallback((value) => {
    setEditorValue(value ?? '')
    setDirty(true)
    setApplyError(null)
    setApplySuccess(false)
  }, [])

  const handleCopy = useCallback(() => {
    const src = editorValue ?? loadState.source
    if (!src) return
    navigator.clipboard.writeText(src).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }, [editorValue, loadState.source])

  const handleReset = useCallback(() => {
    setEditorValue(loadState.source)
    setDirty(false)
    setApplyError(null)
    setApplySuccess(false)
  }, [loadState.source])

  const handleApply = useCallback(async () => {
    const src = editorValue ?? ''
    if (!src.trim()) {
      setApplyError('Editor is empty — write some nubi.flows Python first.')
      return
    }
    setApplying(true)
    setApplyError(null)
    setApplySuccess(false)

    const result = await compileCode(src)

    setApplying(false)

    if (!result || result.error) {
      setApplyError(result?.error ?? 'Compile failed — check your code.')
      return
    }

    if (!result.spec) {
      setApplyError('Backend returned no spec. Ensure your code calls .compile().')
      return
    }

    setApplySuccess(true)
    setDirty(false)
    setTimeout(() => setApplySuccess(false), 2000)
    onSpecChange?.(result.spec)
  }, [editorValue, onSpecChange])

  const { loading, source, error, unavailable } = loadState
  const displayValue = editorValue ?? source ?? ''

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full bg-surface overflow-hidden">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="shrink-0 flex items-center justify-between px-3 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <Code2 size={14} className="text-violet-500 shrink-0" />
          <div className="min-w-0">
            <h3 className="text-xs font-semibold text-fg truncate leading-tight">
              Flow code
              {dirty && (
                <span
                  className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 ml-1.5 mb-0.5"
                  title="Unsaved edits"
                />
              )}
            </h3>
            <p className="text-[10px] text-muted leading-tight">nubi.flows Python SDK</p>
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {/* Copy */}
          {displayValue && (
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border border-border bg-surface-2 hover:bg-surface text-fg transition-colors"
              title="Copy to clipboard"
            >
              {copied ? <Check size={11} className="text-green-500" /> : <Copy size={11} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          )}

          {/* Reset to generated */}
          {dirty && loadState.source && (
            <button
              onClick={handleReset}
              className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border border-border bg-surface-2 hover:bg-surface text-muted hover:text-fg transition-colors"
              title="Discard edits and reset to generated code"
            >
              <RotateCcw size={11} />
              Reset
            </button>
          )}

          {/* Close */}
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-md text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            title="Close code panel"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* ── Apply code bar ───────────────────────────────────────────────── */}
      <div className="shrink-0 px-3 py-2 border-b border-border bg-violet-500/5 flex items-center gap-2">
        <button
          onClick={handleApply}
          disabled={applying || loading}
          className={[
            'flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold rounded-md transition-all',
            applySuccess
              ? 'bg-green-500/15 border border-green-500/30 text-green-600 dark:text-green-400'
              : 'bg-violet-500 hover:bg-violet-600 text-white disabled:opacity-50 disabled:cursor-not-allowed',
          ].join(' ')}
          title="Compile code → apply FlowSpec to canvas"
        >
          {applying
            ? <Loader2 size={11} className="animate-spin" />
            : applySuccess
              ? <Check size={11} />
              : <Play size={11} />
          }
          {applying ? 'Compiling…' : applySuccess ? 'Applied!' : 'Apply code'}
        </button>
        <p className="text-[10px] text-muted leading-tight">
          Compile &amp; apply to canvas
        </p>
      </div>

      {/* ── Error banner ─────────────────────────────────────────────────── */}
      {applyError && (
        <div className="shrink-0 flex items-start gap-2 mx-3 mt-2 mb-0 p-2.5 rounded-lg border border-rose-500/20 bg-rose-500/5 text-[11px] text-rose-600 dark:text-rose-400">
          <AlertCircle size={12} className="shrink-0 mt-0.5" />
          <span className="flex-1 min-w-0 break-words font-mono leading-snug whitespace-pre-wrap">{applyError}</span>
          <button
            onClick={() => setApplyError(null)}
            className="shrink-0 opacity-60 hover:opacity-100 mt-0.5"
          >
            <X size={11} />
          </button>
        </div>
      )}

      {/* ── Editor area ──────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden mt-1">
        {loading && (
          <div className="flex items-center justify-center h-full gap-2 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" />
            Generating code…
          </div>
        )}

        {!loading && error && (
          <div className="flex items-start gap-2 m-4 p-3 rounded-xl border border-rose-500/20 bg-rose-500/5 text-xs text-rose-600 dark:text-rose-400">
            <AlertCircle size={13} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {!loading && unavailable && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
            <Code2 size={28} className="text-muted/40" />
            <div>
              <p className="text-sm font-medium text-fg">Codegen not available</p>
              <p className="text-xs text-muted mt-1">
                The codegen endpoint is not yet deployed on this backend.
                Save the flow and try again after upgrading.
              </p>
            </div>
          </div>
        )}

        {!loading && !error && !unavailable && (
          <Editor
            language="python"
            value={displayValue}
            onChange={handleEditorChange}
            theme="vs-dark"
            options={{
              readOnly: false,
              fontSize: 12,
              minimap: { enabled: false },
              lineNumbers: 'on',
              scrollBeyondLastLine: false,
              padding: { top: 10, bottom: 12 },
              wordWrap: 'on',
              folding: true,
              renderLineHighlight: 'line',
              contextmenu: true,
              scrollbar: { vertical: 'auto', horizontal: 'auto' },
              automaticLayout: true,
              tabSize: 4,
              insertSpaces: true,
              // Smooth typing UX
              cursorSmoothCaretAnimation: 'on',
              smoothScrolling: true,
            }}
          />
        )}
      </div>
    </div>
  )
}
