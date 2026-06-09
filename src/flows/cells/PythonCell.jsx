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

import { useState, useCallback } from 'react'
import Editor from '@monaco-editor/react'
import { ChevronDown, ChevronRight, AlertCircle, CheckCircle2, Clock, ChevronDown as SnippetIcon } from 'lucide-react'
import CellToolbar from './CellToolbar.jsx'
import CellConfigAnnotations from './CellConfigAnnotations.jsx'
import DataTable from '../../components/DataTable.jsx'
import { PYTHON_EXAMPLES } from '../pythonExamples.js'

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
}

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

  const tableColumns = result?.columns?.map(c => ({ key: c, label: c, type: 'string' })) ?? []
  const tableRows = result?.rows ?? []
  const editorH = editorHeight(code)

  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
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
      <div className="flex items-center gap-2 px-3 py-1.5 bg-surface-2/20 border-b border-border">
        <p className="text-[10px] text-muted">
          Bind output to{' '}
          <code className="font-mono bg-surface-2 px-1 rounded">result</code>.
          Available:{' '}
          <code className="font-mono bg-surface-2 px-1 rounded">inputs</code>,{' '}
          <code className="font-mono bg-surface-2 px-1 rounded">params</code>.
        </p>

        {/* Snippet dropdown */}
        <div className="relative ml-auto">
          <button
            type="button"
            onClick={() => setSnippetOpen(v => !v)}
            className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded border border-border bg-surface hover:bg-surface-2 text-muted hover:text-fg transition-colors"
          >
            <SnippetIcon size={10} className={`transition-transform ${snippetOpen ? 'rotate-180' : ''}`} />
            Examples
          </button>
          {snippetOpen && (
            <div className="absolute z-20 top-full right-0 mt-1 min-w-[200px] py-1.5 rounded-xl bg-surface border border-border shadow-lg shadow-black/10">
              {PYTHON_EXAMPLES.map(ex => (
                <button
                  key={ex.label}
                  onClick={() => insertSnippet(ex.code)}
                  className="w-full text-left px-3 py-2 text-xs text-fg hover:bg-surface-2 transition-colors"
                >
                  {ex.label}
                </button>
              ))}
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
          theme="vs-dark"
          options={MONACO_PY_OPTS}
        />
      </div>

      {/* Error banner */}
      {runError && (
        <div className="flex items-start gap-2 px-3 py-2 bg-red-500/5 border-b border-red-500/20 text-xs text-red-600 dark:text-red-400">
          <AlertCircle size={12} className="shrink-0 mt-0.5" />
          <span className="flex-1 font-mono whitespace-pre-wrap break-all">{runError}</span>
        </div>
      )}

      {/* Results panel */}
      {result && !runError && (
        <div>
          {/* Results header */}
          <button
            onClick={() => setResultsOpen(v => !v)}
            className="w-full flex items-center gap-2 px-3 py-1.5 bg-surface-2/30 hover:bg-surface-2/60 transition-colors border-b border-border text-left"
          >
            {resultsOpen
              ? <ChevronDown size={12} className="text-muted shrink-0" />
              : <ChevronRight size={12} className="text-muted shrink-0" />
            }
            <CheckCircle2 size={11} className="text-green-500 shrink-0" />
            <span className="text-[11px] font-medium text-fg">
              {result.row_count ?? tableRows.length} rows
            </span>
            {result.elapsed_ms != null && (
              <span className="flex items-center gap-1 ml-auto text-[10px] text-muted">
                <Clock size={10} />
                {result.elapsed_ms}ms
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
              <code className="font-mono bg-surface-2 px-1 rounded">result</code>.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
