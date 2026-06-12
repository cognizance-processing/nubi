/**
 * EditableDataGrid.jsx — Supabase-style table editor with inline cell editing.
 *
 * A focused, write-capable grid (distinct from the read-only analytical
 * DataGrid). It renders one connector table and round-trips edits to the
 * backend write contract:
 *   PATCH  /data/{id}/tables/{t}/rows  { pk, set }   → cell edit
 *   POST   /data/{id}/tables/{t}/rows  { values }    → insert row
 *   DELETE /data/{id}/tables/{t}/rows  { pk }        → delete row
 *
 * Gating: edits/insert/delete are only offered when the table is `writable`
 * AND has a primary key (see editableGridUtils.isReadOnly). Otherwise the grid
 * is fully read-only with a clear banner + lock affordances.
 *
 * Layout — a CSS-grid table:
 *   - sticky header row (top:0)
 *   - sticky left selector/row-number column (left:0)
 *   - horizontal + vertical scroll inside one container so both stickies hold
 *   - per-column resize handles (drag), click-to-sort headers
 *
 * Props
 * -----
 *   datastoreId   string|null
 *   table         string
 *   meta          normalized meta (from editableGridUtils.normalizeColumnMeta)
 *   rows          Array<row object>
 *   total         number|null         (server row count, if known)
 *   loading       bool
 *   error         string|null
 *   onRetry       () => void
 *   onRefresh     () => void
 *   onRowsChange  (nextRows) => void   (optimistic local mutation)
 *   onTotalChange (delta) => void      (insert/delete adjust the count)
 */

import { useState, useMemo, useRef, useCallback, useEffect } from 'react'
import {
  RefreshCw,
  Loader2,
  AlertCircle,
  Search,
  Lock,
  Plus,
  Trash2,
  X,
  Check,
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
  Hash,
  Type as TypeIcon,
  Calendar,
  ToggleLeft,
  Braces,
  Key,
  Database,
} from 'lucide-react'
import {
  formatCell,
  toEditString,
  coerceInput,
  editorKind,
  columnIsEditable,
  isReadOnly,
  pkObject,
  sortRows,
  searchRows,
} from './editableGridUtils.js'
import {
  updateDataRow,
  insertDataRow,
  deleteDataRow,
} from '../../lib/api.js'

const MIN_COL_W = 80
const DEFAULT_COL_W = 180
const SELECTOR_W = 52
const ROW_H = 36

// ---------------------------------------------------------------------------
// Column type icon
// ---------------------------------------------------------------------------

function KindIcon({ kind, className = 'text-muted/70' }) {
  const p = { size: 11, className: `shrink-0 ${className}` }
  switch (kind) {
    case 'number': return <Hash {...p} />
    case 'bool': return <ToggleLeft {...p} />
    case 'date': return <Calendar {...p} />
    case 'json': return <Braces {...p} />
    default: return <TypeIcon {...p} />
  }
}

// ---------------------------------------------------------------------------
// Read-only cell rendering
// ---------------------------------------------------------------------------

function CellContent({ value, kind }) {
  if (value == null) {
    return (
      <span className="inline-block px-1.5 rounded text-[10px] font-mono leading-4 bg-surface-2 text-muted/50 border border-border/50 select-none">
        NULL
      </span>
    )
  }
  if (kind === 'bool') {
    return value ? (
      <span className="inline-flex items-center px-2 rounded-full text-[10px] font-semibold leading-5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
        true
      </span>
    ) : (
      <span className="inline-flex items-center px-2 rounded-full text-[10px] font-semibold leading-5 bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20">
        false
      </span>
    )
  }
  const { display } = formatCell(value, kind)
  if (kind === 'number') {
    return <span className="font-mono tabular-nums">{display}</span>
  }
  return (
    <span className="block truncate" title={display.length > 48 ? display : undefined}>
      {display}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Inline cell editor
// ---------------------------------------------------------------------------

function CellEditor({ col, initialValue, onCommit, onCancel }) {
  const kind = editorKind(col.kind)
  const [text, setText] = useState(() => toEditString(initialValue, col.kind))
  const [err, setErr] = useState(null)
  const inputRef = useRef(null)

  // Focus on mount (handler-free effect — no setState).
  useEffect(() => {
    const el = inputRef.current
    if (el) {
      el.focus()
      if (el.select && kind !== 'checkbox') el.select()
    }
  }, [kind])

  const commit = useCallback((rawOverride) => {
    const raw = rawOverride !== undefined ? rawOverride : text
    const res = coerceInput(raw, col.kind, col.nullable)
    if (!res.ok) { setErr(res.error); return }
    onCommit(res.value)
  }, [text, col.kind, col.nullable, onCommit])

  const commitNull = useCallback(() => {
    const res = coerceInput(null, col.kind, col.nullable)
    if (!res.ok) { setErr(res.error); return }
    onCommit(null)
  }, [col.kind, col.nullable, onCommit])

  // Boolean → immediate three-state-ish toggle (true/false, plus NULL btn).
  if (kind === 'checkbox') {
    return (
      <div className="flex items-center gap-1.5">
        <button
          ref={inputRef}
          onClick={() => commit(!initialValue)}
          className="inline-flex items-center gap-1 px-2 h-6 rounded border border-primary/40 bg-primary/5 text-[11px] text-fg hover:bg-primary/10"
          title="Toggle"
        >
          {initialValue ? 'true → false' : 'false → true'}
        </button>
        {col.nullable && (
          <button onClick={commitNull} className="text-[10px] text-muted hover:text-fg px-1" title="Set NULL">∅</button>
        )}
        <button onClick={onCancel} className="text-muted hover:text-fg" title="Cancel (Esc)"><X size={12} /></button>
      </div>
    )
  }

  const onChange = (e) => { setText(e.target.value); if (err) setErr(null) }
  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !(kind === 'textarea' && e.shiftKey)) { e.preventDefault(); commit() }
    else if (e.key === 'Escape') { e.preventDefault(); onCancel() }
  }
  const onBlur = (e) => {
    // Blur commits — unless focus moved to one of our own action buttons.
    if (e.relatedTarget?.dataset?.editorAction) return
    commit()
  }
  const baseCls =
    'w-full px-1.5 text-xs font-mono bg-surface text-fg border border-primary rounded outline-none focus:ring-2 focus:ring-primary/30'

  return (
    <div className="relative">
      <div className="flex items-stretch gap-1">
        {kind === 'textarea' ? (
          <textarea
            ref={inputRef}
            value={text}
            onChange={onChange}
            onKeyDown={onKeyDown}
            onBlur={onBlur}
            rows={3}
            className={`${baseCls} min-h-[3.5rem] py-1`}
          />
        ) : (
          <input
            ref={inputRef}
            value={text}
            onChange={onChange}
            onKeyDown={onKeyDown}
            onBlur={onBlur}
            type="text"
            inputMode={kind === 'number' ? 'decimal' : undefined}
            className={`${baseCls} h-7`}
          />
        )}
        <div className="flex flex-col gap-0.5 shrink-0">
          <button
            data-editor-action="commit"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => commit()}
            className="flex items-center justify-center w-5 h-5 rounded bg-primary text-primary-fg hover:opacity-90"
            title="Save (Enter)"
          >
            <Check size={11} />
          </button>
          <button
            data-editor-action="cancel"
            onMouseDown={(e) => e.preventDefault()}
            onClick={onCancel}
            className="flex items-center justify-center w-5 h-5 rounded border border-border text-muted hover:text-fg"
            title="Cancel (Esc)"
          >
            <X size={11} />
          </button>
        </div>
      </div>
      {col.nullable && (
        <button
          data-editor-action="null"
          onMouseDown={(e) => e.preventDefault()}
          onClick={commitNull}
          className="mt-0.5 text-[9px] text-muted hover:text-fg"
          title="Set NULL"
        >
          set NULL
        </button>
      )}
      {err && (
        <div className="absolute top-full left-0 mt-0.5 z-30 px-1.5 py-0.5 rounded bg-rose-500 text-white text-[10px] whitespace-nowrap shadow">
          {err}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Insert-row form (a sticky bottom drawer)
// ---------------------------------------------------------------------------

function InsertRowForm({ columns, onSubmit, onCancel, busy }) {
  const editable = columns.filter((c) => !c.pkAutoSkip)
  const [values, setValues] = useState({})
  const [error, setError] = useState(null)

  const submit = () => {
    const out = {}
    for (const col of editable) {
      const raw = values[col.name]
      if (raw === undefined) continue // omitted → backend default
      const res = coerceInput(raw, col.kind, col.nullable)
      if (!res.ok) { setError(`${col.name}: ${res.error}`); return }
      out[col.name] = res.value
    }
    setError(null)
    onSubmit(out)
  }

  return (
    <div className="shrink-0 border-t border-border bg-surface-2/60 px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <Plus size={13} className="text-primary" />
        <span className="text-xs font-semibold text-fg">Insert row</span>
        <span className="text-[10px] text-muted">leave a field blank to use its default / NULL</span>
      </div>
      <div className="flex flex-wrap gap-3">
        {editable.map((col) => (
          <label key={col.name} className="flex flex-col gap-1 min-w-[140px]">
            <span className="flex items-center gap-1 text-[10px] font-mono text-muted">
              <KindIcon kind={col.kind} />
              {col.name}
              {!col.nullable && <span className="text-rose-500" title="NOT NULL">*</span>}
            </span>
            {col.kind === 'bool' ? (
              <select
                className="h-7 px-1.5 text-xs bg-surface border border-border rounded text-fg focus:outline-none focus:ring-1 focus:ring-ring"
                value={values[col.name] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [col.name]: e.target.value }))}
              >
                <option value="">—</option>
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            ) : (
              <input
                type="text"
                className="h-7 px-1.5 text-xs font-mono bg-surface border border-border rounded text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
                placeholder={col.kind}
                value={values[col.name] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [col.name]: e.target.value }))}
              />
            )}
          </label>
        ))}
      </div>
      {error && <p className="mt-2 text-[11px] text-rose-500">{error}</p>}
      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={submit}
          disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 h-7 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 disabled:opacity-50"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
          Save row
        </button>
        <button
          onClick={onCancel}
          disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 h-7 rounded-lg border border-border text-muted hover:text-fg text-xs"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main grid
// ---------------------------------------------------------------------------

export default function EditableDataGrid({
  datastoreId,
  table,
  meta,
  rows,
  total,
  loading,
  error,
  onRetry,
  onRefresh,
  onRowsChange,
  onTotalChange,
}) {
  const columns = useMemo(() => meta?.columns ?? [], [meta])
  const { readOnly, reason } = useMemo(() => isReadOnly(meta), [meta])
  const hasPk = (meta?.primaryKey?.length ?? 0) > 0

  // ── UI state ──────────────────────────────────────────────────────────────
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState({ key: null, dir: null })
  const [widths, setWidths] = useState({}) // {colName: px}
  const [editing, setEditing] = useState(null) // {rowIdx, col}
  const [selected, setSelected] = useState(() => new Set()) // row indices
  const [savingCell, setSavingCell] = useState(null) // `${rowIdx}:${col}`
  const [cellError, setCellError] = useState(null) // {key, message}
  const [savedCell, setSavedCell] = useState(null) // key flashed green
  const [inserting, setInserting] = useState(false)
  const [insertBusy, setInsertBusy] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const scrollRef = useRef(null)

  // ── Derived rows (search + sort, client-side over the loaded page) ─────────
  const viewRows = useMemo(() => {
    const searched = searchRows(rows, columns, query)
    return sortRows(searched, sort.key, sort.dir)
  }, [rows, columns, query, sort])

  // Map a view row back to its index in the source `rows` array (for mutation).
  const sourceIndexOf = useCallback(
    (viewRow) => rows.indexOf(viewRow),
    [rows],
  )

  const colWidth = useCallback((name) => widths[name] ?? DEFAULT_COL_W, [widths])

  // ── Column resize (pointer drag) ───────────────────────────────────────────
  const resizeRef = useRef(null)
  const onResizeDown = useCallback((e, name) => {
    e.preventDefault()
    e.stopPropagation()
    resizeRef.current = { name, startX: e.clientX, startW: colWidth(name) }
    const onMove = (ev) => {
      const r = resizeRef.current
      if (!r) return
      const next = Math.max(MIN_COL_W, r.startW + (ev.clientX - r.startX))
      setWidths((w) => ({ ...w, [r.name]: next }))
    }
    const onUp = () => {
      resizeRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [colWidth])

  const toggleSort = useCallback((name) => {
    setSort((s) => {
      if (s.key !== name) return { key: name, dir: 'asc' }
      if (s.dir === 'asc') return { key: name, dir: 'desc' }
      return { key: null, dir: null }
    })
  }, [])

  // ── Inline edit commit ─────────────────────────────────────────────────────
  const commitEdit = useCallback(async (viewRow, col, newValue) => {
    const srcIdx = sourceIndexOf(viewRow)
    const cellKey = `${srcIdx}:${col.name}`
    setEditing(null)
    if (srcIdx < 0) return

    const original = rows[srcIdx]
    if (original[col.name] === newValue) return // no-op

    const pk = pkObject(original, meta.primaryKey)
    if (!pk) {
      setCellError({ key: cellKey, message: 'Cannot identify row (no PK)' })
      return
    }

    // Optimistic update.
    const optimistic = rows.map((r, i) => (i === srcIdx ? { ...r, [col.name]: newValue } : r))
    onRowsChange(optimistic)
    setSavingCell(cellKey)
    setCellError(null)

    try {
      const updated = await updateDataRow(datastoreId, table, pk, { [col.name]: newValue })
      // Reconcile with server's authoritative row when it returns one.
      if (updated && typeof updated === 'object' && !Array.isArray(updated)) {
        onRowsChange(optimistic.map((r, i) => (i === srcIdx ? { ...r, ...updated } : r)))
      }
      setSavedCell(cellKey)
      setTimeout(() => setSavedCell((k) => (k === cellKey ? null : k)), 1200)
    } catch (err) {
      // Revert.
      onRowsChange(rows.map((r, i) => (i === srcIdx ? original : r)))
      setCellError({ key: cellKey, message: err?.message ?? 'Save failed' })
    } finally {
      setSavingCell((k) => (k === cellKey ? null : k))
    }
  }, [rows, sourceIndexOf, meta, datastoreId, table, onRowsChange])

  // ── Insert ──────────────────────────────────────────────────────────────────
  const doInsert = useCallback(async (values) => {
    setInsertBusy(true)
    setActionError(null)
    try {
      const created = await insertDataRow(datastoreId, table, values)
      const newRow = created && typeof created === 'object' && !Array.isArray(created) ? created : values
      onRowsChange([newRow, ...rows])
      onTotalChange?.(1)
      setInserting(false)
    } catch (err) {
      setActionError(err?.message ?? 'Insert failed')
    } finally {
      setInsertBusy(false)
    }
  }, [datastoreId, table, rows, onRowsChange, onTotalChange])

  // ── Delete selected ───────────────────────────────────────────────────────
  const doDeleteSelected = useCallback(async () => {
    if (selected.size === 0) return
    if (!window.confirm(`Delete ${selected.size} row${selected.size > 1 ? 's' : ''}? This cannot be undone.`)) return
    setDeleteBusy(true)
    setActionError(null)
    const indices = [...selected]
    const survivors = []
    let failures = 0
    // Delete sequentially so partial failures are clear.
    for (let i = 0; i < rows.length; i++) {
      if (!selected.has(i)) { survivors.push(rows[i]); continue }
      const pk = pkObject(rows[i], meta.primaryKey)
      if (!pk) { survivors.push(rows[i]); failures++; continue }
      try {
        await deleteDataRow(datastoreId, table, pk)
      } catch {
        survivors.push(rows[i])
        failures++
      }
    }
    onRowsChange(survivors)
    onTotalChange?.(-(indices.length - failures))
    setSelected(new Set())
    setDeleteBusy(false)
    if (failures) setActionError(`${failures} row(s) could not be deleted.`)
  }, [selected, rows, meta, datastoreId, table, onRowsChange, onTotalChange])

  const toggleSelect = useCallback((srcIdx) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(srcIdx)) next.delete(srcIdx)
      else next.add(srcIdx)
      return next
    })
  }, [])

  const allSelected = rows.length > 0 && selected.size === rows.length
  const toggleSelectAll = useCallback(() => {
    setSelected((prev) => (prev.size === rows.length ? new Set() : new Set(rows.map((_, i) => i))))
  }, [rows])

  // Total grid template width (selector + columns).
  const gridTemplate = useMemo(() => {
    const cols = columns.map((c) => `${colWidth(c.name)}px`).join(' ')
    return `${SELECTOR_W}px ${cols}`
  }, [columns, colWidth])

  // ── Render: error ───────────────────────────────────────────────────────────
  if (error && !loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="w-10 h-10 rounded-full bg-rose-500/10 border border-rose-500/20 flex items-center justify-center">
          <AlertCircle size={18} className="text-rose-500" />
        </div>
        <p className="text-sm font-medium text-fg">Couldn’t load this table</p>
        <p className="text-xs text-muted font-mono max-w-sm">{error}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs text-muted hover:text-fg hover:bg-surface-2"
          >
            <RefreshCw size={12} /> Retry
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Toolbar */}
      <div className="shrink-0 flex items-center gap-2 px-4 py-2.5 border-b border-border bg-surface flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Database size={15} className="text-primary shrink-0" />
          <h2 className="text-sm font-semibold font-mono text-fg truncate">{table}</h2>
          {!loading && (
            <span className="text-[11px] text-muted shrink-0">
              {(total ?? rows.length).toLocaleString()} {(total ?? rows.length) === 1 ? 'row' : 'rows'}
              {total != null && total > rows.length && ` · showing ${rows.length}`}
            </span>
          )}
        </div>

        {readOnly && (
          <span
            className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20"
            title={reason ?? 'Read-only'}
          >
            <Lock size={10} /> read-only
          </span>
        )}

        <div className="flex-1" />

        {/* Search */}
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search loaded rows…"
            className="h-7 pl-6 pr-2 text-[11px] bg-surface-2 border border-border rounded-md text-fg placeholder:text-muted/60 focus:outline-none focus:ring-1 focus:ring-ring w-36 focus:w-52 transition-all"
          />
        </div>

        {!readOnly && selected.size > 0 && (
          <button
            onClick={doDeleteSelected}
            disabled={deleteBusy}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400 text-[11px] font-medium hover:bg-rose-500/20 disabled:opacity-50"
          >
            {deleteBusy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
            Delete {selected.size}
          </button>
        )}

        {!readOnly && (
          <button
            onClick={() => setInserting((v) => !v)}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md bg-primary text-primary-fg text-[11px] font-medium hover:opacity-90"
          >
            <Plus size={12} /> Insert row
          </button>
        )}

        <button
          onClick={onRefresh}
          disabled={loading}
          title="Refresh"
          className="flex items-center justify-center w-7 h-7 rounded-md border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Read-only banner */}
      {readOnly && reason && (
        <div className="shrink-0 flex items-center gap-2 px-4 py-1.5 bg-amber-500/5 border-b border-amber-500/15 text-[11px] text-amber-700 dark:text-amber-400">
          <Lock size={11} /> {reason}. You can browse but not edit this table.
        </div>
      )}

      {actionError && (
        <div className="shrink-0 flex items-center gap-2 px-4 py-1.5 bg-rose-500/5 border-b border-rose-500/15 text-[11px] text-rose-600 dark:text-rose-400">
          <AlertCircle size={11} /> {actionError}
          <button onClick={() => setActionError(null)} className="ml-auto"><X size={11} /></button>
        </div>
      )}

      {/* Grid body */}
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto bg-surface relative">
        {loading ? (
          <GridSkeleton cols={Math.min(columns.length || 5, 7)} />
        ) : columns.length === 0 ? (
          <EmptyState icon={Database} title="No columns" sub="This table has no columns to display." />
        ) : (
          <div role="grid" className="inline-grid min-w-full" style={{ gridTemplateColumns: gridTemplate }}>
            {/* Header row */}
            <div
              role="columnheader"
              className="sticky top-0 left-0 z-30 flex items-center justify-center bg-surface-2 border-b border-r border-border h-9"
              style={{ width: SELECTOR_W }}
            >
              {!readOnly ? (
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="accent-[var(--primary)]"
                  aria-label="Select all rows"
                />
              ) : (
                <span className="text-[10px] text-muted/60 font-mono">#</span>
              )}
            </div>
            {columns.map((col) => {
              const sorted = sort.key === col.name ? sort.dir : null
              const editableCol = columnIsEditable(col, meta.writable, hasPk)
              return (
                <div
                  key={col.name}
                  role="columnheader"
                  className="sticky top-0 z-20 relative bg-surface-2 border-b border-r border-border/60 h-9 group select-none"
                >
                  <div className="flex items-center gap-1 h-full px-2.5">
                    <KindIcon kind={col.kind} />
                    {col.pk && <Key size={10} className="text-amber-500 shrink-0" title="Primary key" />}
                    <button
                      onClick={() => toggleSort(col.name)}
                      className="flex items-center gap-1 min-w-0 text-[11px] font-semibold text-muted hover:text-fg"
                      title={`Sort by ${col.name}`}
                    >
                      <span className="truncate font-mono">{col.name}</span>
                      <span className="shrink-0">
                        {sorted === 'asc' && <ChevronUp size={11} className="text-primary" />}
                        {sorted === 'desc' && <ChevronDown size={11} className="text-primary" />}
                        {!sorted && <ChevronsUpDown size={10} className="text-muted/30 group-hover:text-muted/60" />}
                      </span>
                    </button>
                    {!editableCol && !readOnly && (
                      <Lock size={9} className="text-muted/40 shrink-0" title={col.pk ? 'Primary key — not editable' : 'Read-only column'} />
                    )}
                  </div>
                  {/* Resize handle */}
                  <div
                    onPointerDown={(e) => onResizeDown(e, col.name)}
                    className="absolute right-0 top-0 bottom-0 w-2 cursor-col-resize flex items-center justify-center opacity-0 group-hover:opacity-100"
                  >
                    <div className="w-0.5 h-4 rounded bg-primary/40 hover:bg-primary" />
                  </div>
                </div>
              )
            })}

            {/* Body rows */}
            {viewRows.length === 0 ? (
              <div
                className="col-span-full py-12 text-center text-xs text-muted"
                style={{ gridColumn: `1 / -1` }}
              >
                {query ? 'No loaded rows match your search.' : 'This table has no rows.'}
              </div>
            ) : (
              viewRows.map((row) => {
                const srcIdx = sourceIndexOf(row)
                const isSel = selected.has(srcIdx)
                return (
                  <Row
                    key={srcIdx}
                    row={row}
                    srcIdx={srcIdx}
                    columns={columns}
                    isSel={isSel}
                    readOnly={readOnly}
                    writable={meta.writable}
                    hasPk={hasPk}
                    onToggleSelect={toggleSelect}
                    editing={editing}
                    setEditing={setEditing}
                    onCommitEdit={commitEdit}
                    savingCell={savingCell}
                    savedCell={savedCell}
                    cellError={cellError}
                    clearCellError={() => setCellError(null)}
                  />
                )
              })
            )}
          </div>
        )}
      </div>

      {/* Insert form */}
      {inserting && !readOnly && (
        <InsertRowForm
          columns={columns.map((c) => ({ ...c, pkAutoSkip: false }))}
          onSubmit={doInsert}
          onCancel={() => setInserting(false)}
          busy={insertBusy}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Row (memo-friendly subcomponent)
// ---------------------------------------------------------------------------

function Row({
  row, srcIdx, columns, isSel, readOnly, writable, hasPk,
  onToggleSelect, editing, setEditing, onCommitEdit,
  savingCell, savedCell, cellError, clearCellError,
}) {
  return (
    <div role="row" className="contents group">
      {/* Selector / row-number sticky cell */}
      <div
        className={[
          'sticky left-0 z-10 flex items-center justify-center border-b border-r border-border/40',
          isSel ? 'bg-primary/10' : 'bg-surface group-hover:bg-surface-2/60',
        ].join(' ')}
        style={{ width: SELECTOR_W, minHeight: ROW_H }}
      >
        {!readOnly ? (
          <input
            type="checkbox"
            checked={isSel}
            onChange={() => onToggleSelect(srcIdx)}
            className="accent-[var(--primary)]"
            aria-label={`Select row ${srcIdx + 1}`}
          />
        ) : (
          <span className="text-[10px] text-muted/50 font-mono tabular-nums">{srcIdx + 1}</span>
        )}
      </div>

      {columns.map((col) => {
        const cellKey = `${srcIdx}:${col.name}`
        const editableCol = columnIsEditable(col, writable, hasPk)
        const isEditing = editing && editing.srcIdx === srcIdx && editing.col === col.name
        const isSaving = savingCell === cellKey
        const isSaved = savedCell === cellKey
        const hasErr = cellError && cellError.key === cellKey
        const { align } = formatCell(row[col.name], col.kind)

        return (
          <div
            role="gridcell"
            key={col.name}
            tabIndex={editableCol ? 0 : -1}
            onDoubleClick={editableCol ? () => setEditing({ srcIdx, col: col.name }) : undefined}
            onKeyDown={(e) => {
              if (editableCol && !isEditing && (e.key === 'Enter' || e.key === 'F2')) {
                e.preventDefault()
                setEditing({ srcIdx, col: col.name })
              }
            }}
            className={[
              'relative border-b border-r border-border/25 px-2.5 text-xs text-fg flex items-center',
              align === 'right' ? 'justify-end text-right' : '',
              isSel ? 'bg-primary/[0.06]' : 'bg-surface group-hover:bg-surface-2/40',
              editableCol && !isEditing ? 'cursor-text hover:ring-1 hover:ring-inset hover:ring-primary/20' : '',
              hasErr ? 'ring-1 ring-inset ring-rose-500' : '',
              isSaved ? 'ring-1 ring-inset ring-emerald-500/60' : '',
            ].join(' ')}
            style={{ minHeight: ROW_H }}
            title={editableCol && !isEditing ? 'Double-click or Enter to edit' : undefined}
          >
            {isEditing ? (
              <CellEditor
                col={col}
                initialValue={row[col.name]}
                onCommit={(v) => onCommitEdit(row, col, v)}
                onCancel={() => setEditing(null)}
              />
            ) : (
              <>
                <CellContent value={row[col.name]} kind={col.kind} />
                {isSaving && (
                  <Loader2 size={11} className="absolute right-1.5 top-1/2 -translate-y-1/2 animate-spin text-primary" />
                )}
                {hasErr && (
                  <button
                    onClick={clearCellError}
                    className="absolute -bottom-px right-0 z-20 px-1.5 py-0.5 rounded-bl bg-rose-500 text-white text-[9px] whitespace-nowrap"
                    title={cellError.message}
                  >
                    {cellError.message}
                  </button>
                )}
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Misc presentational bits
// ---------------------------------------------------------------------------

function EmptyState({ icon: Icon, title, sub }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 p-8 text-center">
      <div className="w-12 h-12 rounded-2xl bg-surface-2 border border-border flex items-center justify-center">
        <Icon size={22} className="text-muted" />
      </div>
      <div>
        <p className="text-sm font-medium text-fg">{title}</p>
        <p className="text-xs text-muted mt-1">{sub}</p>
      </div>
    </div>
  )
}

function GridSkeleton({ cols = 5, rows = 12 }) {
  return (
    <div className="p-3 space-y-2">
      <div className="flex gap-2">
        {Array.from({ length: cols }).map((_, i) => (
          <div key={i} className="h-6 flex-1 rounded bg-surface-2 animate-pulse" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, ri) => (
        <div key={ri} className="flex gap-2">
          {Array.from({ length: cols }).map((_, ci) => (
            <div
              key={ci}
              className="h-5 flex-1 rounded bg-border/50 animate-pulse"
              style={{ animationDelay: `${(ri * cols + ci) * 25}ms` }}
            />
          ))}
        </div>
      ))}
    </div>
  )
}
