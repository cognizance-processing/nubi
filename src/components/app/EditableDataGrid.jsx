/**
 * EditableDataGrid.jsx — Supabase-style table editor with inline cell editing,
 * cell selection + keyboard navigation, a row-detail slide-over, and
 * client-side filter/sort over the loaded page.
 *
 * It renders one connector table and round-trips edits to the backend write
 * contract:
 *   PATCH  /data/{id}/tables/{t}/rows  { pk, set }   → cell edit
 *   POST   /data/{id}/tables/{t}/rows  { values }    → insert row
 *   DELETE /data/{id}/tables/{t}/rows  { pk }        → delete row
 *
 * Gating: edits/insert/delete are only offered when the table is `writable`
 * AND has a primary key (see editableGridUtils.isReadOnly). Otherwise the grid
 * is fully read-only with a clear banner + lock affordances.
 *
 * Layout — a CSS-grid table inside one scroll container so both the sticky
 * header (top:0) and the sticky gutter (left:0) hold under H+V scroll. Columns
 * are sized to fill the available width (distributeColumnWidths) and stay
 * drag-resizable.
 *
 * Interaction model
 * -----------------
 *   - Click a cell → selects it (2px accent focus ring). A single keydown
 *     handler on the scroll container drives navigation (no effects-per-cell):
 *       Arrows / Tab / Shift-Tab  move the selection (moveSelection math)
 *       Enter / F2 / double-click / typing  start editing
 *       Esc  cancel · Enter  commit + move down
 *       Cmd/Ctrl-C  copy the selected cell
 *   - The gutter shows the row number, swapping to a checkbox on hover / when
 *     any row is selected. An expand affordance opens the row-detail panel.
 *
 * Props
 * -----
 *   datastoreId, table, meta (normalized), rows, total, loading, error,
 *   onRetry, onRefresh, onRowsChange(nextRows), onTotalChange(delta)
 */

import { useState, useMemo, useRef, useCallback, useEffect } from 'react'
import {
  RefreshCw, Loader2, AlertCircle, Search, Lock, Plus, Trash2, X, Check,
  ChevronUp, ChevronDown, ChevronsUpDown, ArrowUpAZ, ArrowDownAZ, EyeOff,
  Filter, ArrowUpDown, MoreHorizontal, Hash, Sigma, Type as TypeIcon,
  Calendar, ToggleLeft, Braces, Key, Fingerprint, Database, PanelRightOpen,
} from 'lucide-react'
import {
  formatCell, toEditString, coerceInput, editorKind, baseKind, columnIsEditable,
  isReadOnly, pkObject, sortRows, searchRows, filterRows, FILTER_OPS,
  distributeColumnWidths, moveSelection, copyCellText,
} from './editableGridUtils.js'
import { updateDataRow, insertDataRow, deleteDataRow } from '../../lib/api.js'

const MIN_COL_W = 80
const GUTTER_W = 56
const ROW_H = 34

// ---------------------------------------------------------------------------
// Column type icon
// ---------------------------------------------------------------------------

function KindIcon({ kind, className = 'text-muted/70' }) {
  const p = { size: 12, className: `shrink-0 ${className}`, strokeWidth: 2 }
  switch (kind) {
    case 'int': return <Hash {...p} />
    case 'float':
    case 'number': return <Sigma {...p} />
    case 'bool': return <ToggleLeft {...p} />
    case 'date': return <Calendar {...p} />
    case 'json': return <Braces {...p} />
    case 'uuid': return <Fingerprint {...p} />
    default: return <TypeIcon {...p} />
  }
}

// ---------------------------------------------------------------------------
// Read-only cell rendering
// ---------------------------------------------------------------------------

function CellContent({ value, kind }) {
  const base = baseKind(kind)
  if (value == null) {
    return (
      <span className="inline-block px-1 rounded text-[10px] font-mono leading-[15px] text-muted/45 select-none">
        NULL
      </span>
    )
  }
  if (base === 'bool') {
    return value ? (
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> true
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-muted">
        <span className="w-1.5 h-1.5 rounded-full bg-muted/40" /> false
      </span>
    )
  }
  const { display } = formatCell(value, kind)
  if (base === 'number') {
    return <span className="font-mono tabular-nums tracking-tight">{display}</span>
  }
  if (kind === 'uuid') {
    return <span className="block truncate font-mono text-[11px] text-muted">{display}</span>
  }
  return (
    <span className="block truncate" title={display.length > 40 ? display : undefined}>
      {display}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Inline cell editor
// ---------------------------------------------------------------------------

function CellEditor({ col, initialValue, onCommit, onCommitMove, onCancel, seed }) {
  const kind = editorKind(col.kind)
  const [text, setText] = useState(() =>
    seed != null && kind !== 'checkbox' ? seed : toEditString(initialValue, col.kind),
  )
  const [err, setErr] = useState(null)
  const inputRef = useRef(null)

  // Focus on mount (handler-free effect — no setState).
  useEffect(() => {
    const el = inputRef.current
    if (el) {
      el.focus()
      if (el.select && kind !== 'checkbox' && seed == null) el.select()
    }
  }, [kind, seed])

  const commit = useCallback((rawOverride, move) => {
    const raw = rawOverride !== undefined ? rawOverride : text
    const res = coerceInput(raw, col.kind, col.nullable)
    if (!res.ok) { setErr(res.error); return }
    if (move && onCommitMove) onCommitMove(res.value)
    else onCommit(res.value)
  }, [text, col.kind, col.nullable, onCommit, onCommitMove])

  const commitNull = useCallback(() => {
    const res = coerceInput(null, col.kind, col.nullable)
    if (!res.ok) { setErr(res.error); return }
    onCommit(null)
  }, [col.kind, col.nullable, onCommit])

  // Boolean → quick toggle (true/false, plus NULL).
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
    if (e.key === 'Enter' && !(kind === 'textarea' && e.shiftKey)) {
      e.preventDefault(); e.stopPropagation(); commit(undefined, true)
    } else if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation(); onCancel()
    } else if (e.key === 'Tab') {
      e.preventDefault(); e.stopPropagation(); commit()
    } else {
      e.stopPropagation()
    }
  }
  const onBlur = (e) => {
    if (e.relatedTarget?.dataset?.editorAction) return
    commit()
  }
  const baseCls =
    'w-full px-1.5 text-xs font-mono bg-surface text-fg border border-primary rounded-[3px] outline-none focus:ring-2 focus:ring-primary/30'

  return (
    <div className="absolute inset-0 z-30 p-px bg-surface">
      <div className="flex items-stretch gap-1 h-full">
        {kind === 'textarea' ? (
          <textarea
            ref={inputRef} value={text} onChange={onChange} onKeyDown={onKeyDown} onBlur={onBlur}
            rows={3}
            className={`${baseCls} min-h-[3.5rem] py-1 absolute top-0 left-0 right-0 shadow-lg z-40`}
          />
        ) : (
          <input
            ref={inputRef} value={text} onChange={onChange} onKeyDown={onKeyDown} onBlur={onBlur}
            type="text" inputMode={editorKind(col.kind) === 'number' ? 'decimal' : undefined}
            className={`${baseCls} self-center h-[26px]`}
          />
        )}
        <div className="flex flex-col gap-0.5 shrink-0 self-center">
          <button
            data-editor-action="commit" onMouseDown={(e) => e.preventDefault()}
            onClick={() => commit()}
            className="flex items-center justify-center w-5 h-5 rounded bg-primary text-primary-fg hover:opacity-90"
            title="Save (Enter)"
          ><Check size={11} /></button>
          {col.nullable && (
            <button
              data-editor-action="null" onMouseDown={(e) => e.preventDefault()}
              onClick={commitNull}
              className="flex items-center justify-center w-5 h-5 rounded border border-border text-[9px] text-muted hover:text-fg"
              title="Set NULL"
            >∅</button>
          )}
        </div>
      </div>
      {err && (
        <div className="absolute top-full left-0 mt-0.5 z-40 px-1.5 py-0.5 rounded bg-rose-500 text-white text-[10px] whitespace-nowrap shadow">
          {err}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Header column menu (hover dropdown)
// ---------------------------------------------------------------------------

function MenuItem({ icon: Icon, label, onClick, active }) {
  return (
    <button
      onClick={onClick}
      className={[
        'w-full flex items-center gap-2 px-2.5 py-1.5 text-[11px] text-left hover:bg-surface-2 rounded-md',
        active ? 'text-primary' : 'text-fg',
      ].join(' ')}
    >
      <Icon size={13} className="shrink-0" /> {label}
    </button>
  )
}

function ColumnMenu({ col, sortDir, onSort, onHide, onFilter, onClose }) {
  const ref = useRef(null)
  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose() }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [onClose])
  const close = onClose
  return (
    <div
      ref={ref}
      className="absolute top-full right-0 mt-1 z-50 w-44 p-1 rounded-lg border border-border bg-surface shadow-xl"
    >
      <MenuItem icon={ArrowUpAZ} label="Sort ascending" active={sortDir === 'asc'} onClick={() => { onSort('asc'); close() }} />
      <MenuItem icon={ArrowDownAZ} label="Sort descending" active={sortDir === 'desc'} onClick={() => { onSort('desc'); close() }} />
      <div className="my-1 h-px bg-border" />
      <MenuItem icon={Filter} label="Filter on this column" onClick={() => { onFilter(col.name); close() }} />
      <MenuItem icon={EyeOff} label="Hide column" onClick={() => { onHide(col.name); close() }} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Filter / Sort toolbar popovers
// ---------------------------------------------------------------------------

function Popover({ children, onClose, align = 'left' }) {
  const ref = useRef(null)
  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose() }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [onClose])
  return (
    <div
      ref={ref}
      className={[
        'absolute top-full mt-1.5 z-50 w-[340px] p-3 rounded-xl border border-border bg-surface shadow-2xl',
        align === 'right' ? 'right-0' : 'left-0',
      ].join(' ')}
    >
      {children}
    </div>
  )
}

function FilterPanel({ columns, filters, setFilters, onClose }) {
  const add = () => setFilters((f) => [...f, { column: columns[0]?.name ?? '', op: 'eq', value: '' }])
  const update = (i, patch) => setFilters((f) => f.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const remove = (i) => setFilters((f) => f.filter((_, j) => j !== i))
  return (
    <Popover onClose={onClose}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-fg">Filters</span>
        {filters.length > 0 && (
          <button onClick={() => setFilters([])} className="text-[10px] text-muted hover:text-fg">Clear all</button>
        )}
      </div>
      {filters.length === 0 && (
        <p className="text-[11px] text-muted mb-2">No filters applied to loaded rows.</p>
      )}
      <div className="space-y-1.5">
        {filters.map((f, i) => {
          const opMeta = FILTER_OPS.find((o) => o.id === f.op)
          return (
            <div key={i} className="flex items-center gap-1.5">
              <select
                value={f.column}
                onChange={(e) => update(i, { column: e.target.value })}
                className="flex-1 min-w-0 h-7 px-1.5 text-[11px] font-mono bg-surface-2 border border-border rounded text-fg focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
              <select
                value={f.op}
                onChange={(e) => update(i, { op: e.target.value })}
                className="h-7 px-1 text-[11px] bg-surface-2 border border-border rounded text-fg focus:outline-none focus:ring-1 focus:ring-ring"
              >
                {FILTER_OPS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
              {opMeta?.value && (
                <input
                  value={f.value ?? ''}
                  onChange={(e) => update(i, { value: e.target.value })}
                  placeholder="value"
                  className="w-20 h-7 px-1.5 text-[11px] font-mono bg-surface-2 border border-border rounded text-fg placeholder:text-muted/50 focus:outline-none focus:ring-1 focus:ring-ring"
                />
              )}
              <button onClick={() => remove(i)} className="shrink-0 text-muted hover:text-rose-500" title="Remove">
                <X size={13} />
              </button>
            </div>
          )
        })}
      </div>
      <button
        onClick={add}
        className="mt-2 inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
      >
        <Plus size={12} /> Add filter
      </button>
    </Popover>
  )
}

function SortPanel({ columns, sort, setSort, onClose }) {
  return (
    <Popover onClose={onClose} align="right">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-fg">Sort</span>
        {sort.key && (
          <button onClick={() => setSort({ key: null, dir: null })} className="text-[10px] text-muted hover:text-fg">Clear</button>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <select
          value={sort.key ?? ''}
          onChange={(e) => setSort({ key: e.target.value || null, dir: e.target.value ? (sort.dir ?? 'asc') : null })}
          className="flex-1 h-7 px-1.5 text-[11px] font-mono bg-surface-2 border border-border rounded text-fg focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="">No sort</option>
          {columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
        <div className="flex rounded border border-border overflow-hidden">
          {['asc', 'desc'].map((d) => (
            <button
              key={d}
              disabled={!sort.key}
              onClick={() => setSort((s) => ({ ...s, dir: d }))}
              className={[
                'px-2 h-7 text-[11px]',
                sort.dir === d && sort.key ? 'bg-primary text-primary-fg' : 'bg-surface-2 text-muted hover:text-fg',
                !sort.key ? 'opacity-40' : '',
              ].join(' ')}
            >
              {d === 'asc' ? 'Asc' : 'Desc'}
            </button>
          ))}
        </div>
      </div>
    </Popover>
  )
}

// ---------------------------------------------------------------------------
// Row-detail slide-over panel (Supabase signature)
// ---------------------------------------------------------------------------

function RowDetailPanel({ row, columns, readOnly, writable, hasPk, onClose, onSave, busy, error }) {
  const [draft, setDraft] = useState({})
  const [localErr, setLocalErr] = useState(null)
  const dirtyKeys = Object.keys(draft)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const fieldVal = (col) => (col.name in draft ? draft[col.name] : toEditString(row[col.name], col.kind))
  const setField = (name, v) => setDraft((d) => ({ ...d, [name]: v }))

  const save = () => {
    const set = {}
    for (const name of dirtyKeys) {
      const col = columns.find((c) => c.name === name)
      if (!col) continue
      const res = coerceInput(draft[name], col.kind, col.nullable)
      if (!res.ok) { setLocalErr(`${name}: ${res.error}`); return }
      set[name] = res.value
    }
    setLocalErr(null)
    onSave(set)
  }

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/20 dark:bg-black/40" onClick={onClose} />
      <aside className="fixed top-0 right-0 bottom-0 z-50 w-full max-w-md bg-surface border-l border-border shadow-2xl flex flex-col animate-[slideIn_.18s_ease-out]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="shrink-0 flex items-center justify-between px-4 h-12 border-b border-border">
          <div className="flex items-center gap-2">
            <PanelRightOpen size={15} className="text-primary" />
            <h3 className="text-sm font-semibold text-fg">Row detail</h3>
          </div>
          <button onClick={onClose} className="text-muted hover:text-fg" title="Close (Esc)"><X size={16} /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {columns.map((col) => {
            const editable = columnIsEditable(col, writable, hasPk)
            const v = fieldVal(col)
            return (
              <label key={col.name} className="block">
                <span className="flex items-center gap-1.5 mb-1 text-[10px] uppercase tracking-wide text-muted">
                  <KindIcon kind={col.kind} className="text-muted/70" />
                  <span className="font-mono normal-case tracking-normal text-[11px] text-fg/80">{col.name}</span>
                  {col.pk && <Key size={9} className="text-amber-500" />}
                  <span className="ml-auto text-[9px] text-muted/60">{col.type}</span>
                </span>
                {col.kind === 'bool' ? (
                  <select
                    disabled={readOnly || !editable}
                    value={v === '' ? '' : String(v)}
                    onChange={(e) => setField(col.name, e.target.value)}
                    className="w-full h-8 px-2 text-xs bg-surface-2 border border-border rounded-lg text-fg disabled:opacity-60 focus:outline-none focus:ring-1 focus:ring-ring"
                  >
                    <option value="">—</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : baseKind(col.kind) === 'json' ? (
                  <textarea
                    disabled={readOnly || !editable}
                    value={v}
                    onChange={(e) => setField(col.name, e.target.value)}
                    rows={3}
                    className="w-full px-2 py-1.5 text-xs font-mono bg-surface-2 border border-border rounded-lg text-fg disabled:opacity-60 focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                ) : (
                  <input
                    disabled={readOnly || !editable}
                    value={v}
                    onChange={(e) => setField(col.name, e.target.value)}
                    className="w-full h-8 px-2 text-xs font-mono bg-surface-2 border border-border rounded-lg text-fg disabled:opacity-60 focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                )}
              </label>
            )
          })}
        </div>

        {(error || localErr) && (
          <div className="shrink-0 px-4 py-2 text-[11px] text-rose-500 border-t border-rose-500/20 bg-rose-500/5">
            {localErr || error}
          </div>
        )}
        {!readOnly && (
          <div className="shrink-0 flex items-center gap-2 px-4 h-14 border-t border-border">
            <button
              onClick={save}
              disabled={busy || dirtyKeys.length === 0}
              className="inline-flex items-center gap-1.5 px-3 h-8 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 disabled:opacity-40"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
              Save{dirtyKeys.length > 0 ? ` (${dirtyKeys.length})` : ''}
            </button>
            <button onClick={onClose} className="px-3 h-8 rounded-lg border border-border text-muted hover:text-fg text-xs">
              Cancel
            </button>
          </div>
        )}
      </aside>
    </>
  )
}

// ---------------------------------------------------------------------------
// Insert-row form (sticky bottom drawer)
// ---------------------------------------------------------------------------

function InsertRowForm({ columns, onSubmit, onCancel, busy }) {
  const [values, setValues] = useState({})
  const [error, setError] = useState(null)

  const submit = () => {
    const out = {}
    for (const col of columns) {
      const raw = values[col.name]
      if (raw === undefined || raw === '') continue // omitted → backend default
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
        <span className="text-[10px] text-muted">leave a field blank for its default / NULL</span>
      </div>
      <div className="flex flex-wrap gap-3">
        {columns.map((col) => (
          <label key={col.name} className="flex flex-col gap-1 min-w-[140px]">
            <span className="flex items-center gap-1 text-[10px] font-mono text-muted">
              <KindIcon kind={col.kind} />
              {col.name}
              {col.pk && <Key size={9} className="text-amber-500" />}
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
                placeholder={col.type}
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
          onClick={submit} disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 h-7 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 disabled:opacity-50"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
          Save row
        </button>
        <button
          onClick={onCancel} disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 h-7 rounded-lg border border-border text-muted hover:text-fg text-xs"
        >Cancel</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main grid
// ---------------------------------------------------------------------------

export default function EditableDataGrid({
  datastoreId, table, meta, rows, total, loading, error,
  onRetry, onRefresh, onRowsChange, onTotalChange,
}) {
  const allColumns = useMemo(() => meta?.columns ?? [], [meta])
  const { readOnly, reason } = useMemo(() => isReadOnly(meta), [meta])
  const hasPk = (meta?.primaryKey?.length ?? 0) > 0

  // ── UI state ──────────────────────────────────────────────────────────────
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState({ key: null, dir: null })
  const [filters, setFilters] = useState([])
  const [hidden, setHidden] = useState(() => new Set()) // hidden column names
  const [widths, setWidths] = useState({}) // {colName: px} explicit (resized)
  const [containerW, setContainerW] = useState(0)

  const [editing, setEditing] = useState(null) // {srcIdx, col, seed?}
  const [selCell, setSelCell] = useState(null) // {row: viewIdx, col: visIdx}
  const [selected, setSelected] = useState(() => new Set()) // src row indices
  const [detailIdx, setDetailIdx] = useState(null) // src idx for row panel

  const [savingCell, setSavingCell] = useState(null)
  const [cellError, setCellError] = useState(null)
  const [savedCell, setSavedCell] = useState(null)
  const [inserting, setInserting] = useState(false)
  const [insertBusy, setInsertBusy] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [detailBusy, setDetailBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [openMenu, setOpenMenu] = useState(null) // column name with open header menu
  const [openPanel, setOpenPanel] = useState(null) // 'filter' | 'sort' | null

  const scrollRef = useRef(null)

  const columns = useMemo(
    () => allColumns.filter((c) => !hidden.has(c.name)),
    [allColumns, hidden],
  )

  // ── Measure container width (ref callback + ResizeObserver, no setState-in-effect) ──
  const setScrollEl = useCallback((el) => {
    scrollRef.current = el
    if (!el) return
    const measure = () => setContainerW(el.clientWidth)
    measure()
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(measure)
      ro.observe(el)
      el._ro = ro
    }
  }, [])
  useEffect(() => () => { if (scrollRef.current?._ro) scrollRef.current._ro.disconnect() }, [])

  // ── Derived rows (search + filter + sort over the loaded page) ─────────────
  const viewRows = useMemo(() => {
    const searched = searchRows(rows, allColumns, query)
    const filtered = filterRows(searched, filters)
    return sortRows(filtered, sort.key, sort.dir)
  }, [rows, allColumns, query, filters, sort])

  const sourceIndexOf = useCallback((viewRow) => rows.indexOf(viewRow), [rows])

  // ── Column widths (fill available, honour resizes) ─────────────────────────
  const renderWidths = useMemo(() => {
    const avail = Math.max(0, (containerW || 0) - GUTTER_W - 1)
    return distributeColumnWidths(columns, widths, avail, MIN_COL_W)
  }, [columns, widths, containerW])

  const gridTemplate = useMemo(() => {
    const cols = columns.map((c) => `${renderWidths[c.name]}px`).join(' ')
    return `${GUTTER_W}px ${cols}`
  }, [columns, renderWidths])

  // ── Column resize (pointer drag) ───────────────────────────────────────────
  const resizeRef = useRef(null)
  const onResizeDown = useCallback((e, name) => {
    e.preventDefault(); e.stopPropagation()
    resizeRef.current = { name, startX: e.clientX, startW: renderWidths[name] ?? MIN_COL_W }
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
  }, [renderWidths])

  const toggleSort = useCallback((name) => {
    setSort((s) => {
      if (s.key !== name) return { key: name, dir: 'asc' }
      if (s.dir === 'asc') return { key: name, dir: 'desc' }
      return { key: null, dir: null }
    })
  }, [])

  const setColumnSort = useCallback((name, dir) => setSort({ key: name, dir }), [])
  const hideColumn = useCallback((name) => setHidden((h) => new Set(h).add(name)), [])
  const filterColumn = useCallback((name) => {
    setFilters((f) => [...f, { column: name, op: 'eq', value: '' }])
    setOpenPanel('filter')
  }, [])

  // ── Inline edit commit ─────────────────────────────────────────────────────
  const commitEdit = useCallback(async (srcIdx, col, newValue, moveDown) => {
    const cellKey = `${srcIdx}:${col.name}`
    setEditing(null)
    if (moveDown) setSelCell((c) => (c ? { ...c, row: Math.min(viewRows.length - 1, c.row + 1) } : c))
    scrollRef.current?.focus()
    if (srcIdx < 0) return

    const original = rows[srcIdx]
    if (!original || original[col.name] === newValue) return // no-op

    const pk = pkObject(original, meta.primaryKey)
    if (!pk) { setCellError({ key: cellKey, message: 'Cannot identify row (no PK)' }); return }

    const optimistic = rows.map((r, i) => (i === srcIdx ? { ...r, [col.name]: newValue } : r))
    onRowsChange(optimistic)
    setSavingCell(cellKey)
    setCellError(null)
    try {
      const updated = await updateDataRow(datastoreId, table, pk, { [col.name]: newValue })
      if (updated && typeof updated === 'object' && !Array.isArray(updated)) {
        onRowsChange(optimistic.map((r, i) => (i === srcIdx ? { ...r, ...updated } : r)))
      }
      setSavedCell(cellKey)
      setTimeout(() => setSavedCell((k) => (k === cellKey ? null : k)), 1100)
    } catch (err) {
      onRowsChange(rows.map((r, i) => (i === srcIdx ? original : r)))
      setCellError({ key: cellKey, message: err?.message ?? 'Save failed' })
    } finally {
      setSavingCell((k) => (k === cellKey ? null : k))
    }
  }, [rows, meta, datastoreId, table, onRowsChange, viewRows.length])

  // ── Row-detail save (multi-field PATCH) ────────────────────────────────────
  const saveDetail = useCallback(async (set) => {
    if (detailIdx == null) return
    const original = rows[detailIdx]
    const pk = pkObject(original, meta.primaryKey)
    if (!pk) { setActionError('Cannot identify row (no PK)'); return }
    setDetailBusy(true)
    setActionError(null)
    const optimistic = rows.map((r, i) => (i === detailIdx ? { ...r, ...set } : r))
    onRowsChange(optimistic)
    try {
      const updated = await updateDataRow(datastoreId, table, pk, set)
      if (updated && typeof updated === 'object' && !Array.isArray(updated)) {
        onRowsChange(optimistic.map((r, i) => (i === detailIdx ? { ...r, ...updated } : r)))
      }
      setDetailIdx(null)
    } catch (err) {
      onRowsChange(rows.map((r, i) => (i === detailIdx ? original : r)))
      setActionError(err?.message ?? 'Save failed')
    } finally {
      setDetailBusy(false)
    }
  }, [detailIdx, rows, meta, datastoreId, table, onRowsChange])

  // ── Insert ──────────────────────────────────────────────────────────────────
  const doInsert = useCallback(async (values) => {
    setInsertBusy(true); setActionError(null)
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
    setDeleteBusy(true); setActionError(null)
    const indices = [...selected]
    const survivors = []
    let failures = 0
    for (let i = 0; i < rows.length; i++) {
      if (!selected.has(i)) { survivors.push(rows[i]); continue }
      const pk = pkObject(rows[i], meta.primaryKey)
      if (!pk) { survivors.push(rows[i]); failures++; continue }
      try { await deleteDataRow(datastoreId, table, pk) }
      catch { survivors.push(rows[i]); failures++ }
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
      if (next.has(srcIdx)) next.delete(srcIdx); else next.add(srcIdx)
      return next
    })
  }, [])
  const allSelected = rows.length > 0 && selected.size === rows.length
  const toggleSelectAll = useCallback(() => {
    setSelected((prev) => (prev.size === rows.length ? new Set() : new Set(rows.map((_, i) => i))))
  }, [rows])

  // ── Keyboard navigation (single handler on the scroll container) ───────────
  const onGridKeyDown = useCallback((e) => {
    if (editing) return // editor owns the keyboard
    if (!selCell) return
    const colCount = columns.length
    const rowCount = viewRows.length
    if (rowCount === 0 || colCount === 0) return
    const { row, col } = selCell
    const curCol = columns[col]
    const viewRow = viewRows[row]
    const srcIdx = viewRow ? sourceIndexOf(viewRow) : -1
    const editableCol = curCol && columnIsEditable(curCol, meta?.writable, hasPk)

    const move = (dir) => {
      e.preventDefault()
      setSelCell(moveSelection(selCell, dir, rowCount, colCount))
    }
    switch (e.key) {
      case 'ArrowUp': return move('up')
      case 'ArrowDown': return move('down')
      case 'ArrowLeft': return move('left')
      case 'ArrowRight': return move('right')
      case 'Tab': return move(e.shiftKey ? 'shiftTab' : 'tab')
      case 'Enter':
      case 'F2':
        if (editableCol && srcIdx >= 0) { e.preventDefault(); setEditing({ srcIdx, col: curCol.name }) }
        return
      case 'Escape':
        setSelCell(null); return
      case 'c':
      case 'C':
        if (e.metaKey || e.ctrlKey) {
          e.preventDefault()
          const txt = copyCellText(viewRow?.[curCol.name], curCol?.kind)
          if (navigator.clipboard) navigator.clipboard.writeText(txt).catch(() => {})
          setSavedCell(`${srcIdx}:${curCol.name}`)
          setTimeout(() => setSavedCell((k) => (k === `${srcIdx}:${curCol.name}` ? null : k)), 600)
        }
        return
      default:
        // Type-to-edit: a printable single char starts editing with that seed.
        if (editableCol && srcIdx >= 0 && e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
          e.preventDefault()
          setEditing({ srcIdx, col: curCol.name, seed: e.key })
        }
    }
  }, [editing, selCell, columns, viewRows, sourceIndexOf, meta, hasPk])

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
          ><RefreshCw size={12} /> Retry</button>
        )}
      </div>
    )
  }

  const shown = rows.length
  const insertCols = allColumns

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Toolbar */}
      <div className="shrink-0 flex items-center gap-2 px-4 h-12 border-b border-border bg-surface">
        <div className="flex items-center gap-2 min-w-0">
          <Database size={15} className="text-primary shrink-0" />
          <h2 className="text-sm font-semibold font-mono text-fg truncate">{table}</h2>
          {!loading && (
            <span className="text-[11px] text-muted shrink-0 tabular-nums">
              {(total ?? shown).toLocaleString()} {(total ?? shown) === 1 ? 'row' : 'rows'}
            </span>
          )}
          {readOnly && (
            <span
              className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20"
              title={reason ?? 'Read-only'}
            ><Lock size={10} /> read-only</span>
          )}
        </div>

        <div className="w-px h-5 bg-border mx-1" />

        {/* Filter */}
        <div className="relative">
          <button
            onClick={() => setOpenPanel((p) => (p === 'filter' ? null : 'filter'))}
            className={[
              'inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md border text-[11px] font-medium',
              filters.length > 0
                ? 'border-primary/40 bg-primary/10 text-primary'
                : 'border-border text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            <Filter size={12} /> Filter{filters.length > 0 ? ` (${filters.length})` : ''}
          </button>
          {openPanel === 'filter' && (
            <FilterPanel columns={allColumns} filters={filters} setFilters={setFilters} onClose={() => setOpenPanel(null)} />
          )}
        </div>

        {/* Sort */}
        <div className="relative">
          <button
            onClick={() => setOpenPanel((p) => (p === 'sort' ? null : 'sort'))}
            className={[
              'inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md border text-[11px] font-medium',
              sort.key
                ? 'border-primary/40 bg-primary/10 text-primary'
                : 'border-border text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            <ArrowUpDown size={12} /> Sort{sort.key ? ` (1)` : ''}
          </button>
          {openPanel === 'sort' && (
            <SortPanel columns={allColumns} sort={sort} setSort={setSort} onClose={() => setOpenPanel(null)} />
          )}
        </div>

        {hidden.size > 0 && (
          <button
            onClick={() => setHidden(new Set())}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md border border-border text-muted hover:text-fg hover:bg-surface-2 text-[11px]"
            title="Show all hidden columns"
          ><EyeOff size={12} /> {hidden.size} hidden</button>
        )}

        <div className="flex-1" />

        {/* Search */}
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <input
            type="text" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search loaded rows…"
            className="h-7 pl-6 pr-2 text-[11px] bg-surface-2 border border-border rounded-md text-fg placeholder:text-muted/60 focus:outline-none focus:ring-1 focus:ring-ring w-36 focus:w-52 transition-all"
          />
        </div>

        {!readOnly && selected.size > 0 && (
          <button
            onClick={doDeleteSelected} disabled={deleteBusy}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400 text-[11px] font-medium hover:bg-rose-500/20 disabled:opacity-50"
          >
            {deleteBusy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
            Delete {selected.size}
          </button>
        )}

        {!readOnly && (
          <button
            onClick={() => setInserting((v) => !v)}
            className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md bg-primary text-primary-fg text-[11px] font-medium hover:opacity-90 shadow-sm"
          ><Plus size={12} /> Insert row</button>
        )}

        <button
          onClick={onRefresh} disabled={loading} title="Refresh"
          className="flex items-center justify-center w-7 h-7 rounded-md border border-border text-muted hover:text-fg hover:bg-surface-2 disabled:opacity-40"
        ><RefreshCw size={13} className={loading ? 'animate-spin' : ''} /></button>
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
      <div
        ref={setScrollEl}
        tabIndex={-1}
        onKeyDown={onGridKeyDown}
        className="flex-1 min-h-0 overflow-auto bg-surface relative outline-none"
      >
        {loading ? (
          <GridSkeleton cols={Math.min(allColumns.length || 5, 7)} />
        ) : allColumns.length === 0 ? (
          <EmptyState icon={Database} title="No columns" sub="This table has no columns to display." />
        ) : (
          <div role="grid" className="grid min-w-full text-fg" style={{ gridTemplateColumns: gridTemplate }}>
            {/* ── Header: gutter ── */}
            <div
              role="columnheader"
              className="sticky top-0 left-0 z-40 flex items-center justify-center bg-surface-2 border-b border-r border-border"
              style={{ height: ROW_H }}
            >
              {!readOnly ? (
                <input
                  type="checkbox" checked={allSelected} onChange={toggleSelectAll}
                  className="accent-[var(--primary)] w-3.5 h-3.5" aria-label="Select all rows"
                />
              ) : <span className="text-[10px] text-muted/60 font-mono">#</span>}
            </div>

            {/* ── Header: columns ── */}
            {columns.map((col, visIdx) => {
              const sorted = sort.key === col.name ? sort.dir : null
              const editableCol = columnIsEditable(col, meta?.writable, hasPk)
              return (
                <div
                  key={col.name}
                  role="columnheader"
                  className="sticky top-0 z-30 relative bg-surface-2 border-b border-r border-border h-[34px] group/h select-none"
                >
                  <div className="flex items-center gap-1.5 h-full pl-2.5 pr-7">
                    <KindIcon kind={col.kind} />
                    {col.pk && <Key size={10} className="text-amber-500 shrink-0" />}
                    <button
                      onClick={() => toggleSort(col.name)}
                      className="flex items-center gap-1 min-w-0 text-[11px] font-semibold text-fg/80 hover:text-fg"
                      title={`Sort by ${col.name}`}
                    >
                      <span className="truncate font-mono">{col.name}</span>
                    </button>
                    <span className="shrink-0 ml-auto flex items-center">
                      {sorted === 'asc' && <ChevronUp size={12} className="text-primary" />}
                      {sorted === 'desc' && <ChevronDown size={12} className="text-primary" />}
                      {!sorted && <ChevronsUpDown size={11} className="text-muted/25 group-hover/h:text-muted/50" />}
                    </span>
                    {!editableCol && !readOnly && (
                      <Lock size={9} className="text-muted/40 shrink-0" title={col.pk ? 'Primary key — not editable' : 'Read-only column'} />
                    )}
                  </div>

                  {/* Header menu trigger */}
                  <button
                    onClick={() => setOpenMenu((m) => (m === col.name ? null : col.name))}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center justify-center w-5 h-5 rounded text-muted/0 group-hover/h:text-muted hover:text-fg hover:bg-surface"
                    title="Column options"
                  ><MoreHorizontal size={13} /></button>
                  {openMenu === col.name && (
                    <ColumnMenu
                      col={col} sortDir={sorted}
                      onSort={(d) => setColumnSort(col.name, d)}
                      onHide={hideColumn} onFilter={filterColumn}
                      onClose={() => setOpenMenu(null)}
                    />
                  )}

                  {/* Resize handle */}
                  <div
                    onPointerDown={(e) => onResizeDown(e, col.name)}
                    onDoubleClick={() => setWidths((w) => { const n = { ...w }; delete n[col.name]; return n })}
                    className="absolute -right-1 top-0 bottom-0 w-2 cursor-col-resize flex items-center justify-center z-10"
                    title="Drag to resize · double-click to auto-fit"
                  >
                    <div className="w-px h-full bg-transparent group-hover/h:bg-primary/30 hover:!bg-primary" />
                  </div>
                  {visIdx === columns.length - 1 && <span />}
                </div>
              )
            })}

            {/* ── Body ── */}
            {viewRows.length === 0 ? (
              <div className="col-span-full py-16 text-center text-xs text-muted" style={{ gridColumn: '1 / -1' }}>
                {query || filters.length > 0
                  ? 'No loaded rows match your filters.'
                  : 'This table has no rows.'}
              </div>
            ) : (
              viewRows.map((row, viewIdx) => {
                const srcIdx = sourceIndexOf(row)
                return (
                  <Row
                    key={srcIdx >= 0 ? srcIdx : `v${viewIdx}`}
                    row={row} srcIdx={srcIdx} viewIdx={viewIdx}
                    columns={columns}
                    isSel={selected.has(srcIdx)}
                    readOnly={readOnly} writable={meta?.writable} hasPk={hasPk}
                    onToggleSelect={toggleSelect}
                    anySelected={selected.size > 0}
                    onOpenDetail={() => setDetailIdx(srcIdx)}
                    onFocusGrid={() => scrollRef.current?.focus()}
                    editing={editing} setEditing={setEditing}
                    selCell={selCell} setSelCell={setSelCell}
                    onCommitEdit={commitEdit}
                    savingCell={savingCell} savedCell={savedCell}
                    cellError={cellError} clearCellError={() => setCellError(null)}
                  />
                )
              })
            )}
          </div>
        )}
      </div>

      {/* Footer / pagination range */}
      {!loading && allColumns.length > 0 && (
        <div className="shrink-0 flex items-center gap-3 px-4 h-8 border-t border-border bg-surface text-[10px] text-muted tabular-nums">
          <span>
            {viewRows.length === shown
              ? `1–${shown.toLocaleString()}`
              : `${viewRows.length.toLocaleString()} of ${shown.toLocaleString()} loaded`}
            {total != null && total > shown && (
              <span className="text-muted/70"> · {total.toLocaleString()} total in table</span>
            )}
          </span>
          {total != null && total > shown && (
            <span className="text-amber-600/80 dark:text-amber-400/80">
              Showing the first {shown.toLocaleString()} rows — refine or filter at the source for more.
            </span>
          )}
          <span className="ml-auto flex items-center gap-3">
            {selected.size > 0 && <span className="text-primary">{selected.size} selected</span>}
            <span>{columns.length} of {allColumns.length} cols</span>
          </span>
        </div>
      )}

      {/* Insert form */}
      {inserting && !readOnly && (
        <InsertRowForm columns={insertCols} onSubmit={doInsert} onCancel={() => setInserting(false)} busy={insertBusy} />
      )}

      {/* Row-detail slide-over */}
      {detailIdx != null && rows[detailIdx] && (
        <RowDetailPanel
          row={rows[detailIdx]} columns={allColumns}
          readOnly={readOnly} writable={meta?.writable} hasPk={hasPk}
          onClose={() => setDetailIdx(null)} onSave={saveDetail}
          busy={detailBusy} error={actionError}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Row (memo-friendly subcomponent)
// ---------------------------------------------------------------------------

function Row({
  row, srcIdx, viewIdx, columns, isSel, readOnly, writable, hasPk,
  onToggleSelect, anySelected, onOpenDetail, onFocusGrid,
  editing, setEditing, selCell, setSelCell, onCommitEdit,
  savingCell, savedCell, cellError, clearCellError,
}) {
  const rowSelected = selCell?.row === viewIdx
  return (
    <div role="row" className="contents group/row">
      {/* Gutter: row number ⇄ checkbox + expand */}
      <div
        className={[
          'sticky left-0 z-20 flex items-center justify-center gap-1 border-b border-r border-border/70 pl-1',
          isSel ? 'bg-primary/10' : rowSelected ? 'bg-primary/[0.04]' : 'bg-surface group-hover/row:bg-surface-2/70',
        ].join(' ')}
        style={{ height: ROW_H }}
      >
        {!readOnly ? (
          <>
            {/* number → checkbox swap */}
            <span className="relative w-5 flex items-center justify-center">
              {(anySelected || isSel) ? (
                <input
                  type="checkbox" checked={isSel} onChange={() => onToggleSelect(srcIdx)}
                  className="accent-[var(--primary)] w-3.5 h-3.5" aria-label={`Select row ${viewIdx + 1}`}
                />
              ) : (
                <>
                  <span className="text-[10px] text-muted/50 font-mono tabular-nums group-hover/row:opacity-0">{viewIdx + 1}</span>
                  <input
                    type="checkbox" checked={false} onChange={() => onToggleSelect(srcIdx)}
                    className="accent-[var(--primary)] w-3.5 h-3.5 absolute opacity-0 group-hover/row:opacity-100"
                    aria-label={`Select row ${viewIdx + 1}`}
                  />
                </>
              )}
            </span>
            <button
              onClick={onOpenDetail}
              className="opacity-0 group-hover/row:opacity-100 text-muted/60 hover:text-primary"
              title="Expand row"
            ><PanelRightOpen size={12} /></button>
          </>
        ) : (
          <>
            <span className="text-[10px] text-muted/50 font-mono tabular-nums group-hover/row:opacity-0">{viewIdx + 1}</span>
            <button
              onClick={onOpenDetail}
              className="opacity-0 group-hover/row:opacity-100 text-muted/60 hover:text-primary absolute"
              title="Expand row"
            ><PanelRightOpen size={12} /></button>
          </>
        )}
      </div>

      {columns.map((col, visIdx) => {
        const cellKey = `${srcIdx}:${col.name}`
        const editableCol = columnIsEditable(col, writable, hasPk)
        const isEditing = editing && editing.srcIdx === srcIdx && editing.col === col.name
        const isCellSel = selCell?.row === viewIdx && selCell?.col === visIdx
        const isSaving = savingCell === cellKey
        const isSaved = savedCell === cellKey
        const hasErr = cellError && cellError.key === cellKey
        const { align } = formatCell(row[col.name], col.kind)

        return (
          <div
            role="gridcell"
            key={col.name}
            onMouseDown={() => { setSelCell({ row: viewIdx, col: visIdx }); onFocusGrid?.() }}
            onDoubleClick={editableCol ? () => setEditing({ srcIdx, col: col.name }) : undefined}
            className={[
              'relative border-b border-r border-border/50 px-2.5 text-xs flex items-center min-w-0',
              align === 'right' ? 'justify-end text-right' : '',
              isSel ? 'bg-primary/[0.05]' : rowSelected ? 'bg-primary/[0.025]' : 'bg-surface group-hover/row:bg-surface-2/40',
              editableCol ? 'cursor-text' : 'cursor-default',
              isCellSel ? 'z-10 ring-2 ring-inset ring-primary' : '',
              hasErr ? 'ring-2 ring-inset ring-rose-500 z-10' : '',
              isSaved ? 'ring-2 ring-inset ring-emerald-500/70 z-10' : '',
            ].join(' ')}
            style={{ height: ROW_H }}
            title={editableCol && !isEditing ? 'Double-click or Enter to edit' : undefined}
          >
            {isEditing ? (
              <CellEditor
                col={col} initialValue={row[col.name]} seed={editing.seed}
                onCommit={(v) => onCommitEdit(srcIdx, col, v, false)}
                onCommitMove={(v) => onCommitEdit(srcIdx, col, v, true)}
                onCancel={() => { setEditing(null); onFocusGrid?.() }}
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
                  >{cellError.message}</button>
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

function GridSkeleton({ cols = 5, rows = 14 }) {
  return (
    <div>
      {/* header */}
      <div className="flex border-b border-border bg-surface-2" style={{ height: ROW_H }}>
        <div className="shrink-0 border-r border-border" style={{ width: GUTTER_W }} />
        {Array.from({ length: cols }).map((_, i) => (
          <div key={i} className="flex-1 border-r border-border flex items-center px-2.5">
            <div className="h-2.5 w-20 rounded bg-border/60 animate-pulse" />
          </div>
        ))}
      </div>
      {/* rows */}
      {Array.from({ length: rows }).map((_, ri) => (
        <div key={ri} className="flex border-b border-border/40" style={{ height: ROW_H }}>
          <div className="shrink-0 border-r border-border/50 flex items-center justify-center" style={{ width: GUTTER_W }}>
            <div className="h-2 w-3 rounded bg-border/40 animate-pulse" />
          </div>
          {Array.from({ length: cols }).map((_, ci) => (
            <div key={ci} className="flex-1 border-r border-border/30 flex items-center px-2.5">
              <div
                className="h-2.5 rounded bg-border/40 animate-pulse"
                style={{ width: `${40 + ((ri * 7 + ci * 13) % 45)}%`, animationDelay: `${(ri * cols + ci) * 20}ms` }}
              />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
