/**
 * PythonCell.jsx — Python notebook cell.
 *
 * Features:
 *   - Monaco Python editor (mirrors the PythonConfig in NodeInspector.jsx)
 *   - CellToolbar: ordinal badge, run button, move-up/down, delete
 *   - Inline results grid (DataTable) after running
 *   - Row count + elapsed ms in the results footer
 *   - Error display when the preview call fails
 *   - Collapsed / expanded toggle for the results panel
 *
 * Props:
 *   index         {number}           0-based position in the notebook
 *   cell          {object}           CellSpec (kind='python', cell_type='python')
 *   onCellChange  {Function(cell)}   called when code changes
 *   onMoveUp      {Function|null}
 *   onMoveDown    {Function|null}
 *   onDelete      {Function}
 *   onRun         {Function}         called to execute the cell preview;
 *                                    returns { rows, columns, row_count, elapsed_ms, error? }
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import Editor from '@monaco-editor/react'
import { ChevronDown, ChevronRight, AlertCircle, CheckCircle2, Clock, ChevronDown as SnippetIcon } from 'lucide-react'
import CellToolbar from './CellToolbar.jsx'
import useMonacoTheme from './useMonacoTheme.js'
import Button from '../../components/ui/Button.jsx'
import CellConfigAnnotations from './CellConfigAnnotations.jsx'
import SecretsMenu from './SecretsMenu.jsx'
import DataTable from '../../components/DataTable.jsx'
import { PYTHON_EXAMPLES } from '../pythonExamples.js'
import { listIngestTemplates } from '../../lib/flows.js'

// Backend ingest starter templates (GET /flows/ingest-templates) are fetched
// once and cached for the lifetime of the module so every Python cell shares
// the same list without re-hitting the API. Resolves to [{label, code}].
let _ingestTemplatesCache = null
function loadIngestTemplates() {
  if (!_ingestTemplatesCache) {
    _ingestTemplatesCache = listIngestTemplates()
      .then((tpls) =>
        (tpls || []).map((t) => ({ label: t.title || t.id, code: t.code || '' })),
      )
      .catch(() => [])
  }
  return _ingestTemplatesCache
}

// Monaco Python editor options
const MONACO_PY_OPTS = {
  fontSize: 13,
  minimap: { enabled: false },
  lineNumbers: 'on',
  scrollBeyondLastLine: false,
  padding: { top: 8, bottom: 8 },
  wordWrap: 'on',
  tabSize: 4,
  insertSpaces: true,
  automaticLayout: true,
} as const

const EDITOR_MIN_H = 120
const EDITOR_MAX_H = 420
const LINE_H = 20

function editorHeight(code) {
  const lines = (code ?? '').split('\n').length
  return Math.min(EDITOR_MAX_H, Math.max(EDITOR_MIN_H, lines * LINE_H + 24))
}

export default function PythonCell({
  index,
  cell,
  onCellChange,
  onMoveUp,
  onMoveDown,
  onDelete,
  onRun,
}) {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [runError, setRunError] = useState(null)
  const [resultsOpen, setResultsOpen] = useState(true)
  const [snippetOpen, setSnippetOpen] = useState(false)
  const [ingestTemplates, setIngestTemplates] = useState([])
  const monacoRef = useRef(null)
  const monacoTheme = useMonacoTheme()

  // Fetch the backend ingest starter templates once (cached at module level).
  // setState lives inside the async callback, not the effect body, to satisfy
  // react-hooks/set-state-in-effect.
  useEffect(() => {
    let cancelled = false
    loadIngestTemplates().then((tpls) => {
      if (!cancelled) setIngestTemplates(tpls)
    })
    return () => { cancelled = true }
  }, [])

  const code = cell?.config?.code ?? '# Write your Python code here\nresult = {}'

  const handleCodeChange = useCallback((val) => {
    onCellChange?.({ ...cell, config: { ...cell.config, code: val ?? '' } })
  }, [cell, onCellChange])

  const handleRun = useCallback(async () => {
    setRunning(true)
    setRunError(null)
    try {
      const res = await onRun?.(cell)
      if (res?.error) {
        setRunError(res.error)
        setResult(null)
      } else if (res) {
        setResult(res)
        setResultsOpen(true)
      }
    } catch (err) {
      setRunError(err.message ?? 'Preview failed')
    } finally {
      setRunning(false)
    }
  }, [cell, onRun])

  const insertSnippet = useCallback((snippetCode) => {
    onCellChange?.({ ...cell, config: { ...cell.config, code: snippetCode } })
    setSnippetOpen(false)
  }, [cell, onCellChange])

  // Insert a `secrets["NAME"]` reference at the cursor (Monaco executeEdits);
  // falls back to appending a comment line when the editor isn't mounted yet.
  const insertSecret = useCallback((name) => {
    const text = `secrets["${name}"]`
    const editor = monacoRef.current
    if (editor) {
      const sel = editor.getSelection()
      editor.executeEdits('insert-secret', [{ range: sel, text, forceMoveMarkers: true }])
      editor.focus()
    } else {
      onCellChange?.({ ...cell, config: { ...cell.config, code: `${code}\n# ${text}` } })
    }
  }, [cell, onCellChange, code])

  const tableColumns = result?.columns?.map(c => ({ key: c, label: c, type: 'string' })) ?? []
  const tableRows = result?.rows ?? []
  const editorH = editorHeight(code)

  return (
    <div className="overflow-hidden border border-frame-line bg-surface" style={{ borderRadius: 2 }}>
      {/* Toolbar */}
      <CellToolbar
        index={index}
        cell={cell}
        running={running}
        onRun={handleRun}
        onMoveUp={onMoveUp}
        onMoveDown={onMoveDown}
        onDelete={onDelete}
      />

      {/* Cell-config annotation strip (materialized / for_each / run_when) */}
      <CellConfigAnnotations config={cell?.config} />

      {/* Snippet picker sub-toolbar */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
        <p className="m-0 text-xs text-muted">
          Bind output to{' '}
          <code className="font-mono bg-surface-2 px-1" style={{ borderRadius: 2 }}>result</code>.
          Available:{' '}
          <code className="font-mono bg-surface-2 px-1" style={{ borderRadius: 2 }}>inputs</code>,{' '}
          <code className="font-mono bg-surface-2 px-1" style={{ borderRadius: 2 }}>params</code>,{' '}
          <code className="font-mono bg-surface-2 px-1" style={{ borderRadius: 2 }}>secrets</code>.
        </p>

        {/* Secrets dropdown */}
        <div className="ml-auto">
          <SecretsMenu onInsert={insertSecret} />
        </div>

        {/* Snippet dropdown */}
        <div className="relative">
          <Button type="button" variant="secondary" size="xs" onClick={() => setSnippetOpen(v => !v)} aria-expanded={snippetOpen}>
            <SnippetIcon size={12} className={`transition-transform ${snippetOpen ? 'rotate-180' : ''}`} />
            Examples
          </Button>
          {snippetOpen && (
            <div className="nubi-dropdown right-0 top-[calc(100%+6px)] z-20 max-h-80 min-w-[220px] overflow-auto">
              {PYTHON_EXAMPLES.map(ex => (
                <button
                  key={ex.label}
                  onClick={() => insertSnippet(ex.code)}
                  className="nubi-dropdown-item"
                >
                  {ex.label}
                </button>
              ))}
              {ingestTemplates.length > 0 && (
                <>
                  <div className="t-label mt-1 border-t border-border px-3 pb-1 pt-2">
                    Ingest starters
                  </div>
                  {ingestTemplates.map(ex => (
                    <button
                      key={ex.label}
                      onClick={() => insertSnippet(ex.code)}
                      className="nubi-dropdown-item"
                    >
                      {ex.label}
                    </button>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Python editor */}
      <div
        className="border-b border-border"
        style={{ height: editorH }}
      >
        <Editor
          language="python"
          value={code}
          onChange={handleCodeChange}
          theme={monacoTheme}
          options={MONACO_PY_OPTS}
          onMount={(editor) => { monacoRef.current = editor }}
        />
      </div>

      {/* Error banner */}
      {runError && (
        <div className="nubi-notice nubi-notice-error m-3 text-xs" role="alert">
          <AlertCircle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span className="flex-1 whitespace-pre-wrap break-all font-mono">{runError}</span>
        </div>
      )}

      {/* Results panel */}
      {result && !runError && (
        <div>
          {/* Results header */}
          <button
            onClick={() => setResultsOpen(v => !v)}
            aria-expanded={resultsOpen}
            className="nubi-focus-inset flex w-full items-center gap-2 border-b border-border bg-surface-2 px-3 py-1.5 text-left transition-colors hover:bg-frame-hover"
          >
            {resultsOpen
              ? <ChevronDown size={13} className="shrink-0 text-muted" aria-hidden="true" />
              : <ChevronRight size={13} className="shrink-0 text-muted" aria-hidden="true" />
            }
            <span className="nubi-dot nubi-dot-success" aria-hidden="true" />
            <span className="ap-meta" style={{ color: 'var(--text)' }}>
              {(result.row_count ?? tableRows.length).toLocaleString()} rows
            </span>
            {result.elapsed_ms != null && (
              <span className="ap-meta ml-auto flex items-center gap-1">
                <Clock size={11} aria-hidden="true" />
                {result.elapsed_ms} ms
              </span>
            )}
          </button>

          {/* Results table */}
          {resultsOpen && tableColumns.length > 0 && (
            <div className="max-h-72 overflow-auto">
              <DataTable
                columns={tableColumns}
                rows={tableRows}
                loading={false}
                toolbar={false}
                pageSize={100}
              />
            </div>
          )}

          {/* Empty result */}
          {resultsOpen && tableColumns.length === 0 && (
            <div className="px-4 py-3 text-xs text-muted text-center">
              Cell returned no tabular output. Bind a dict or list to{' '}
              <code className="font-mono bg-surface-2 px-1" style={{ borderRadius: 2 }}>result</code>.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
