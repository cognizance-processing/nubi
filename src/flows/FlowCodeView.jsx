/**
 * FlowCodeView.jsx — VS Code-style "code / files" view for a flow.
 *
 * A full-pane third view (alongside canvas + notebook) that projects the flow
 * as an editable file tree:
 *   • flow.py          — the generated nubi.flows Python SDK source. Editable;
 *                        "Apply" round-trips Python → FlowSpec via /flows/compile
 *                        (same path as CodePanel) and syncs the canvas/notebook.
 *   • cells/NN_key.sql — one editable file per SQL cell    (writes config.sql)
 *   • cells/NN_key.py  — one editable file per Python cell (writes config.code)
 *   • cells/NN_key.md  — one editable file per note cell   (writes config.markdown)
 *   • cells/NN_key.json— read-only config dump for kinds with no single source
 *                        (agent / materialize / branch / map / noop / …).
 *
 * Cell edits write straight back to the spec on change — exactly the pattern
 * SqlCell / PythonCell already use (onChange → config[...] → onSpecChange), so a
 * power user can author the whole flow as files. flow.py is the only file that
 * needs an explicit Apply (Python must be compiled before it becomes a spec).
 *
 * Props:
 *   flow         {object|null}  — persisted flow ({ id, ... }); null for unsaved
 *   spec         {object}       — current FlowSpec (tasks live in spec.tasks)
 *   onSpecChange {Function}     — called with the updated spec after any edit
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import Editor from '@monaco-editor/react'
import {
  Code2,
  Copy,
  Check,
  Loader2,
  AlertCircle,
  Play,
  RotateCcw,
  X,
  ChevronDown,
  ChevronRight,
  FolderTree,
} from 'lucide-react'
import { post } from '../lib/api.js'
import { compileCode } from '../lib/flows.js'
import { useTheme } from '../contexts/ThemeContext.jsx'
import {
  FLOW_PY_ID,
  buildCellFiles,
  deriveLoadKey,
  classifyCodegenError,
  selectActiveId,
  activeSourceFor,
} from './flowCodeView.logic.js'

// ---------------------------------------------------------------------------
// Codegen fetch (flow.py)
// ---------------------------------------------------------------------------

/**
 * Fetch the generated nubi.flows source for the current spec.
 *
 * We always prefer the inline-spec endpoint when a spec is available so the
 * code view reflects the LIVE (possibly unsaved / just-edited) spec rather than
 * the persisted DB row — the saved-flow `/flows/{id}/codegen` route would
 * silently re-generate from stale storage and discard in-memory edits/Apply.
 *
 * Returns a discriminated render state:
 *   { source }                  — generated source ready to edit
 *   { unavailable: true }       — codegen endpoint not deployed (404)
 *   { invalidSpec, error }      — spec can't be generated yet (400/422); a
 *                                 normal transient state for a half-built flow
 *   { error }                   — hard failure (network / 500)
 */
async function fetchCodegen(flowId, spec) {
  try {
    let data
    if (spec) {
      data = await post(`/flows/codegen`, { spec })
    } else if (flowId) {
      data = await post(`/flows/${flowId}/codegen`, {})
    } else {
      return { source: null, error: 'No flow id or spec provided.' }
    }
    return { source: data?.source ?? data?.code ?? null }
  } catch (err) {
    const { kind, message } = classifyCodegenError(err)
    if (kind === 'unavailable') return { source: null, unavailable: true }
    if (kind === 'invalidSpec') return { source: null, invalidSpec: true, error: message }
    return { source: null, error: message }
  }
}

const MONACO_OPTIONS = {
  fontSize: 12,
  minimap: { enabled: false },
  lineNumbers: 'on',
  scrollBeyondLastLine: false,
  padding: { top: 12, bottom: 12 },
  wordWrap: 'on',
  folding: true,
  renderLineHighlight: 'line',
  scrollbar: { vertical: 'auto', horizontal: 'auto' },
  automaticLayout: true,
  tabSize: 4,
  insertSpaces: true,
  cursorSmoothCaretAnimation: 'on',
  smoothScrolling: true,
}

// ---------------------------------------------------------------------------
// File-tree row
// ---------------------------------------------------------------------------

function FileRow({ file, active, dirty, onSelect }) {
  const { Icon } = file
  return (
    <button
      onClick={() => onSelect(file.id)}
      className={[
        'group w-full flex items-center gap-2 pl-6 pr-2 py-1 text-left text-xs rounded-md transition-colors',
        active ? 'bg-primary/10 text-fg' : 'text-muted hover:text-fg hover:bg-surface-2',
      ].join(' ')}
      title={file.name}
    >
      <Icon size={13} className={active ? 'text-primary shrink-0' : 'text-muted/70 shrink-0'} />
      <span className="truncate flex-1">{file.name}</span>
      {dirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" title="Unsaved edits" />}
    </button>
  )
}

// ---------------------------------------------------------------------------
// FlowCodeView
// ---------------------------------------------------------------------------

export default function FlowCodeView({ flow, spec, onSpecChange }) {
  const flowId = flow?.id ?? null
  const { theme } = useTheme()
  const monacoTheme = theme === 'dark' ? 'vs-dark' : 'light'

  const cellFiles = useMemo(() => buildCellFiles(spec), [spec])

  const [selectedId, setSelectedId] = useState(FLOW_PY_ID)
  const [cellsOpen, setCellsOpen] = useState(true)
  const [copied, setCopied] = useState(false)

  // flow.py state (generated; editable with explicit Apply).
  // `invalidSpec` is a SOFT state: the spec is half-built (e.g. an empty SQL
  // cell), so the codegen 400'd — we show a gentle hint, not a hard error.
  const [pyState, setPyState] = useState({
    loading: true, source: null, error: null, unavailable: false, invalidSpec: false,
  })
  const [pyValue, setPyValue] = useState(null)
  const [pyDirty, setPyDirty] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState(null)
  const [applySuccess, setApplySuccess] = useState(false)

  // Re-fetch flow.py when the flow identity OR the live spec content changes —
  // keying on spec content (even for saved flows) keeps the generated code in
  // sync with in-memory edits / Apply instead of the stale persisted row.
  const loadKey = deriveLoadKey(flowId, spec)
  const lastLoadKeyRef = useRef(null)

  useEffect(() => {
    if (!loadKey || loadKey === lastLoadKeyRef.current) return
    let cancelled = false
    const run = async () => {
      setPyState({ loading: true, source: null, error: null, unavailable: false, invalidSpec: false })
      setPyDirty(false)
      setApplyError(null)
      setApplySuccess(false)
      const result = await fetchCodegen(flowId, spec)
      if (cancelled) return
      // Mark the key as loaded only on a completed (non-cancelled) fetch.
      // Setting it eagerly deadlocks the spinner: a re-render with an equal
      // spec (new identity) — or StrictMode's double-effect — cancels the
      // in-flight fetch, and the early-return above then blocks the refetch.
      lastLoadKeyRef.current = loadKey
      const src = result.source ?? null
      setPyState({
        loading: false,
        source: src,
        error: result.error ?? null,
        unavailable: result.unavailable ?? false,
        invalidSpec: result.invalidSpec ?? false,
      })
      setPyValue(src)
    }
    run()
    return () => { cancelled = true }
  }, [loadKey, flowId, spec])

  // Derive the effective selection during render (no setState-in-effect): if the
  // selected cell file disappears (task deleted), fall back to flow.py.
  const activeId = selectActiveId(selectedId, cellFiles)

  const selectedCell = cellFiles.find(f => f.id === activeId) || null

  // ── Cell edits → write straight back to spec (mirrors SqlCell/PythonCell) ──
  const handleCellChange = useCallback((file, value) => {
    if (!file || file.key === null) return
    const tasks = (spec?.tasks ?? []).map((t, i) =>
      i === file.index ? { ...t, config: { ...(t.config ?? {}), [file.key]: value ?? '' } } : t,
    )
    onSpecChange?.({ ...spec, tasks })
  }, [spec, onSpecChange])

  // ── flow.py editing + Apply (Python → spec) ───────────────────────────────
  const handlePyChange = useCallback((value) => {
    setPyValue(value ?? '')
    setPyDirty(true)
    setApplyError(null)
    setApplySuccess(false)
  }, [])

  const handlePyReset = useCallback(() => {
    setPyValue(pyState.source)
    setPyDirty(false)
    setApplyError(null)
    setApplySuccess(false)
  }, [pyState.source])

  const handleApply = useCallback(async () => {
    const src = pyValue ?? ''
    if (!src.trim()) {
      setApplyError('flow.py is empty — write some nubi.flows Python first.')
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
    setPyDirty(false)
    setTimeout(() => setApplySuccess(false), 2000)
    // Applying the compiled spec updates the live spec → loadKey changes →
    // the effect re-generates flow.py from it on the next render (no manual
    // ref reset needed, which previously refetched the STALE persisted row).
    onSpecChange?.(result.spec)
  }, [pyValue, onSpecChange])

  // ── Copy active file ───────────────────────────────────────────────────────
  const activeName = activeId === FLOW_PY_ID ? 'flow.py' : (selectedCell?.name ?? '')
  const activeSource = activeSourceFor({
    activeId,
    pyValue,
    pySource: pyState.source,
    selectedCell,
  })

  const handleCopy = useCallback(() => {
    if (!activeSource) return
    navigator.clipboard.writeText(activeSource).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    })
  }, [activeSource])

  const isFlowPy = activeId === FLOW_PY_ID
  const readOnlyCell = !!selectedCell && selectedCell.key === null

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full overflow-hidden bg-bg">

      {/* ── File explorer ─────────────────────────────────────────────────── */}
      <aside className="w-56 shrink-0 flex flex-col border-r border-border bg-surface-2/30 overflow-hidden">
        <div className="shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-border">
          <FolderTree size={13} className="text-muted" />
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Explorer</span>
        </div>
        <div className="flex-1 overflow-y-auto py-2 px-1.5 space-y-0.5">
          {/* flow.py (root) */}
          <button
            onClick={() => setSelectedId(FLOW_PY_ID)}
            className={[
              'group w-full flex items-center gap-2 px-2 py-1 text-left text-xs rounded-md transition-colors',
              isFlowPy ? 'bg-primary/10 text-fg' : 'text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
            title="Generated nubi.flows Python SDK"
          >
            <Code2 size={13} className={isFlowPy ? 'text-violet-500 shrink-0' : 'text-muted/70 shrink-0'} />
            <span className="truncate flex-1">flow.py</span>
            {pyDirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" title="Unsaved edits" />}
          </button>

          {/* cells/ folder */}
          {cellFiles.length > 0 && (
            <>
              <button
                onClick={() => setCellsOpen(o => !o)}
                className="w-full flex items-center gap-1.5 px-2 py-1 text-left text-xs font-medium text-muted hover:text-fg rounded-md transition-colors"
              >
                {cellsOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <span className="truncate">cells</span>
                <span className="ml-auto text-[10px] text-muted/60">{cellFiles.length}</span>
              </button>
              {cellsOpen && cellFiles.map(file => (
                <FileRow
                  key={file.id}
                  file={file}
                  active={file.id === activeId}
                  dirty={false}
                  onSelect={setSelectedId}
                />
              ))}
            </>
          )}
        </div>
        <div className="shrink-0 px-3 py-2 border-t border-border">
          <p className="text-[10px] leading-snug text-muted/70">
            Edit cells as files — changes sync to the canvas. <code className="text-muted">flow.py</code> needs Apply.
          </p>
        </div>
      </aside>

      {/* ── Editor pane ───────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">

        {/* Active-file tab bar */}
        <div className="shrink-0 flex items-center justify-between gap-2 px-3 py-2 border-b border-border bg-surface">
          <div className="flex items-center gap-2 min-w-0">
            {isFlowPy
              ? <Code2 size={13} className="text-violet-500 shrink-0" />
              : selectedCell && <selectedCell.Icon size={13} className="text-muted shrink-0" />}
            <span className="text-xs font-medium text-fg truncate">{activeName}</span>
            {isFlowPy && pyDirty && (
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" title="Unsaved edits" />
            )}
            {readOnlyCell && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 border border-border text-muted">read-only</span>
            )}
          </div>

          <div className="flex items-center gap-1 shrink-0">
            {activeSource && (
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border border-border bg-surface-2 hover:bg-surface text-fg transition-colors"
                title="Copy to clipboard"
              >
                {copied ? <Check size={11} className="text-green-500" /> : <Copy size={11} />}
                {copied ? 'Copied!' : 'Copy'}
              </button>
            )}
            {isFlowPy && pyDirty && pyState.source && (
              <button
                onClick={handlePyReset}
                className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border border-border bg-surface-2 hover:bg-surface text-muted hover:text-fg transition-colors"
                title="Discard edits and reset to generated code"
              >
                <RotateCcw size={11} />
                Reset
              </button>
            )}
            {isFlowPy && (
              <button
                onClick={handleApply}
                disabled={applying || pyState.loading}
                className={[
                  'flex items-center gap-1.5 px-3 py-1 text-[11px] font-semibold rounded-md transition-all',
                  applySuccess
                    ? 'bg-green-500/15 border border-green-500/30 text-green-600 dark:text-green-400'
                    : 'bg-violet-500 hover:bg-violet-600 text-white disabled:opacity-50 disabled:cursor-not-allowed',
                ].join(' ')}
                title="Compile flow.py → apply FlowSpec to the canvas"
              >
                {applying ? <Loader2 size={11} className="animate-spin" /> : applySuccess ? <Check size={11} /> : <Play size={11} />}
                {applying ? 'Compiling…' : applySuccess ? 'Applied!' : 'Apply'}
              </button>
            )}
          </div>
        </div>

        {/* Apply error banner (flow.py) */}
        {isFlowPy && applyError && (
          <div className="shrink-0 flex items-start gap-2 mx-3 mt-2 p-2.5 rounded-lg border border-rose-500/20 bg-rose-500/5 text-[11px] text-rose-600 dark:text-rose-400">
            <AlertCircle size={12} className="shrink-0 mt-0.5" />
            <span className="flex-1 min-w-0 break-words font-mono leading-snug whitespace-pre-wrap">{applyError}</span>
            <button onClick={() => setApplyError(null)} className="shrink-0 opacity-60 hover:opacity-100 mt-0.5">
              <X size={11} />
            </button>
          </div>
        )}

        {/* Editor body */}
        <div className="flex-1 min-h-0 overflow-hidden">
          {isFlowPy ? (
            <>
              {pyState.loading && (
                <div className="flex items-center justify-center h-full gap-2 text-sm text-muted">
                  <Loader2 size={16} className="animate-spin" />
                  Generating code…
                </div>
              )}
              {/* Half-built spec (e.g. an empty SQL cell): codegen can't run
                  yet. This is the common "doesn't work" case — show a gentle
                  hint with the backend's reason, not a scary red failure. */}
              {!pyState.loading && pyState.invalidSpec && (
                <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
                  <Code2 size={28} className="text-muted/40" />
                  <div className="max-w-sm">
                    <p className="text-sm font-medium text-fg">flow.py isn’t ready yet</p>
                    <p className="text-xs text-muted mt-1">
                      Finish the cells first — every SQL cell needs a query and Python cells need code.
                      The generated <code className="text-muted">flow.py</code> will appear once the
                      flow is valid.
                    </p>
                    {pyState.error && (
                      <p className="mt-2 text-[11px] font-mono text-amber-600 dark:text-amber-400 break-words whitespace-pre-wrap">
                        {pyState.error}
                      </p>
                    )}
                  </div>
                </div>
              )}
              {!pyState.loading && !pyState.invalidSpec && pyState.error && (
                <div className="flex items-start gap-2 m-4 p-3 rounded-xl border border-rose-500/20 bg-rose-500/5 text-xs text-rose-600 dark:text-rose-400">
                  <AlertCircle size={13} className="shrink-0 mt-0.5" />
                  <span>{pyState.error}</span>
                </div>
              )}
              {!pyState.loading && pyState.unavailable && (
                <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-6">
                  <Code2 size={28} className="text-muted/40" />
                  <div>
                    <p className="text-sm font-medium text-fg">Codegen not available</p>
                    <p className="text-xs text-muted mt-1">
                      The codegen endpoint is not deployed on this backend. Save the flow and try again after upgrading.
                    </p>
                  </div>
                </div>
              )}
              {!pyState.loading && !pyState.error && !pyState.unavailable && !pyState.invalidSpec && (
                <Editor
                  language="python"
                  value={pyValue ?? pyState.source ?? ''}
                  onChange={handlePyChange}
                  theme={monacoTheme}
                  options={{ ...MONACO_OPTIONS, readOnly: false, contextmenu: true }}
                />
              )}
            </>
          ) : selectedCell ? (
            <Editor
              key={selectedCell.id}
              language={selectedCell.lang}
              value={activeSource}
              onChange={readOnlyCell ? undefined : (val => handleCellChange(selectedCell, val))}
              theme={monacoTheme}
              options={{ ...MONACO_OPTIONS, readOnly: readOnlyCell, contextmenu: !readOnlyCell }}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-sm text-muted">No file selected.</div>
          )}
        </div>
      </div>
    </div>
  )
}
