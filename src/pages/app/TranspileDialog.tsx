/**
 * TranspileDialog — translate SQL between dialects.
 *
 * Props:
 *   open        {boolean}             — whether the dialog is visible
 *   onClose     {Function}            — called to close without applying
 *   initialSql  {string}              — SQL to pre-fill in the source area
 *   onApply     {Function(string)}    — called with translated SQL when "Replace" is clicked
 *
 * Endpoint: POST /transpile { sql, from_dialect, to_dialect }
 * Response: { sql: string } (translated SQL)
 */

import { useState, useEffect, useCallback } from 'react'
import { X, ArrowLeftRight, Loader2, AlertCircle, Copy, Check, RefreshCw } from 'lucide-react'
import { post } from '../../lib/api.js'
import Modal from '../../components/ui/Modal.jsx'
import Button from '../../components/ui/Button.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DIALECTS = [
  { value: 'duckdb',     label: 'DuckDB' },
  { value: 'postgres',   label: 'PostgreSQL' },
  { value: 'bigquery',   label: 'BigQuery' },
  { value: 'snowflake',  label: 'Snowflake' },
  { value: 'trino',      label: 'Trino' },
  { value: 'clickhouse', label: 'ClickHouse' },
  { value: 'mysql',      label: 'MySQL' },
]

const SELECT_CLS = [
  'h-8 px-2.5 text-xs bg-surface border border-border rounded-lg text-fg',
  'focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer transition-colors hover:bg-surface-2',
].join(' ')

// ---------------------------------------------------------------------------
// TranspileDialog
// ---------------------------------------------------------------------------

export default function TranspileDialog({ open, onClose, initialSql = '', onApply }) {
  const [fromDialect, setFromDialect] = useState('duckdb')
  const [toDialect, setToDialect] = useState('postgres')
  const [translated, setTranslated] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)

  // Reset translated output when dialog opens with new SQL
  useEffect(() => {
    if (open) {
      setTranslated('')
      setError(null)
      setCopied(false)
    }
  }, [open, initialSql])

  const handleTranslate = useCallback(async () => {
    const sql = initialSql?.trim()
    if (!sql) {
      setError('No SQL to translate.')
      return
    }
    if (fromDialect === toDialect) {
      setError('From and To dialects must be different.')
      return
    }
    setLoading(true)
    setError(null)
    setTranslated('')
    try {
      const data = await post('/transpile', { sql, from_dialect: fromDialect, to_dialect: toDialect })
      setTranslated(data?.sql ?? data?.translated_sql ?? '')
    } catch (err) {
      setError(err?.message || 'Translation failed.')
    } finally {
      setLoading(false)
    }
  }, [initialSql, fromDialect, toDialect])

  const handleCopy = useCallback(async () => {
    if (!translated) return
    try {
      await navigator.clipboard.writeText(translated)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch { /* ignore: clipboard write may be blocked/unavailable */ }
  }, [translated])

  const handleReplace = useCallback(() => {
    if (!translated) return
    onApply?.(translated)
    onClose?.()
  }, [translated, onApply, onClose])

  const handleSwapDialects = useCallback(() => {
    setFromDialect(toDialect)
    setToDialect(fromDialect)
    setTranslated('')
    setError(null)
  }, [fromDialect, toDialect])

  return (
    <Modal open={open} onClose={onClose} size="2xl" hideClose className="overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-surface-2/60 shrink-0">
        <ArrowLeftRight size={16} className="text-primary shrink-0" />
        <h2 className="text-sm font-semibold text-fg font-display">Translate SQL</h2>
        <div className="flex-1" />
        <button
          onClick={onClose}
          aria-label="Close dialog"
          className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          title="Close"
        >
          <X size={14} />
        </button>
      </div>

      {/* Dialect pickers */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-surface-2/30 shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label htmlFor="transpile-from" className="text-xs font-medium text-muted shrink-0">From</label>
          <select
            id="transpile-from"
            value={fromDialect}
            onChange={(e) => { setFromDialect(e.target.value); setTranslated(''); setError(null) }}
            className={SELECT_CLS}
          >
            {DIALECTS.map(d => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
        </div>

        <button
          onClick={handleSwapDialects}
          title="Swap dialects"
          aria-label="Swap dialects"
          className="h-7 w-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors shrink-0"
        >
          <RefreshCw size={12} />
        </button>

        <div className="flex items-center gap-1.5">
          <label htmlFor="transpile-to" className="text-xs font-medium text-muted shrink-0">To</label>
          <select
            id="transpile-to"
            value={toDialect}
            onChange={(e) => { setToDialect(e.target.value); setTranslated(''); setError(null) }}
            className={SELECT_CLS}
          >
            {DIALECTS.map(d => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
        </div>

        <div className="flex-1" />

        <Button
          onClick={handleTranslate}
          disabled={!initialSql?.trim()}
          loading={loading}
          size="sm"
        >
          {!loading && <ArrowLeftRight size={12} />}
          {loading ? 'Translating…' : 'Translate'}
        </Button>
      </div>

      {/* Source SQL (read-only preview) */}
      <div className="px-4 pt-3 pb-1 shrink-0">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted mb-1.5">Source SQL</p>
        <div className="rounded-lg border border-border bg-surface-2/30 overflow-auto max-h-36">
          <pre className="p-3 text-xs font-mono text-fg whitespace-pre-wrap break-words leading-relaxed">
            {initialSql || <span className="italic text-muted/50">No SQL to translate</span>}
          </pre>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div role="alert" className="mx-4 mt-2 flex items-center gap-2 text-xs text-danger">
          <AlertCircle size={13} className="shrink-0" />
          {error}
        </div>
      )}

      {/* Translated SQL */}
      <div className="px-4 pt-3 pb-3 flex-1 min-h-0 overflow-hidden flex flex-col">
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">
            Translated SQL
            {translated && (
              <span className="ml-1.5 normal-case text-muted/60 font-normal">
                ({DIALECTS.find(d => d.value === toDialect)?.label ?? toDialect})
              </span>
            )}
          </p>
          {translated && (
            <button
              onClick={handleCopy}
              className="inline-flex items-center gap-1 h-6 px-2 rounded-md border border-border text-[10px] font-medium text-muted hover:text-fg hover:bg-surface-2 transition-colors"
              title="Copy to clipboard"
            >
              {copied ? <Check size={10} className="text-success" /> : <Copy size={10} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          )}
        </div>
        <div className="rounded-lg border border-border bg-surface-2/30 flex-1 min-h-0 overflow-auto">
          {loading ? (
            <div className="flex items-center gap-2 p-4 text-xs text-muted">
              <Loader2 size={13} className="animate-spin" /> Translating…
            </div>
          ) : translated ? (
            <pre className="p-3 text-xs font-mono text-fg whitespace-pre-wrap break-words leading-relaxed">
              {translated}
            </pre>
          ) : (
            <div className="p-4 text-xs italic text-muted/50">
              Click "Translate" to see the result here.
            </div>
          )}
        </div>
      </div>

      {/* Footer actions */}
      <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border bg-surface-2/30 shrink-0">
        <Button onClick={onClose} variant="secondary" size="sm">
          Cancel
        </Button>
        <Button onClick={handleReplace} disabled={!translated} size="sm">
          Replace in editor
        </Button>
      </div>
    </Modal>
  )
}
