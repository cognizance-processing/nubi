/**
 * VisualQueryBuilder — a no-code path to a SELECT, for the query editor.
 *
 * Deliberately scoped to a SINGLE table: pick a table, pick columns, add
 * simple AND-only filters, sort, limit. It does not attempt multi-table
 * joins — the schema catalog (GET /query/schema) returns only
 * `{tables: {name: [columns]}}`, with no foreign-key/relationship metadata,
 * so there is no reliable way to propose a *correct* join without the user
 * hand-specifying the ON clause anyway. Generating join SQL that merely
 * *looks* plausible would be worse than not offering joins at all.
 *
 * Output is plain generated SQL text via onGenerate(sql) — this builder does
 * not run the query itself; the caller inserts the text into the SQL editor
 * exactly like a Template or an AI-generated suggestion.
 */

import { useEffect, useMemo, useState } from 'react'
import { Plus, Trash2, Wand2 } from 'lucide-react'
import { inputCls, selectCls } from '../editor/shared/inspectorPrimitives.jsx'

const OPERATORS = [
  { value: '=', label: '=' },
  { value: '!=', label: '≠' },
  { value: '>', label: '>' },
  { value: '<', label: '<' },
  { value: '>=', label: '≥' },
  { value: '<=', label: '≤' },
  { value: 'LIKE', label: 'contains' },
  { value: 'IS NULL', label: 'is empty' },
  { value: 'IS NOT NULL', label: 'is not empty' },
]

const NO_VALUE_OPS = new Set(['IS NULL', 'IS NOT NULL'])

/** Quote a bare identifier only if it needs it (keeps the generated SQL readable). */
function ident(name) {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : `"${name.replace(/"/g, '""')}"`
}

/** Best-effort literal formatting — numeric values stay bare, everything else is a quoted string. */
function literal(value) {
  const trimmed = String(value ?? '').trim()
  if (trimmed !== '' && !Number.isNaN(Number(trimmed))) return trimmed
  return `'${trimmed.replace(/'/g, "''")}'`
}

function buildSql({ table, columns, filters, orderBy, orderDir, limit }) {
  if (!table) return ''
  const cols = columns.length > 0 ? columns.map(ident).join(', ') : '*'
  let sql = `SELECT ${cols}\nFROM ${ident(table)}`

  const clauses = filters
    .filter(f => f.field && (NO_VALUE_OPS.has(f.op) || String(f.value ?? '').trim() !== ''))
    .map(f => {
      if (NO_VALUE_OPS.has(f.op)) return `${ident(f.field)} ${f.op}`
      if (f.op === 'LIKE') return `${ident(f.field)} LIKE '%${String(f.value).replace(/'/g, "''")}%'`
      return `${ident(f.field)} ${f.op} ${literal(f.value)}`
    })
  if (clauses.length > 0) sql += `\nWHERE ${clauses.join('\n  AND ')}`

  if (orderBy) sql += `\nORDER BY ${ident(orderBy)} ${orderDir}`
  if (limit) sql += `\nLIMIT ${Number(limit) || 100}`

  return sql
}

export default function VisualQueryBuilder({ schema, onGenerate, onClose }) {
  const tables = useMemo(() => Object.keys(schema?.tables ?? {}).sort(), [schema])
  const [table, setTable] = useState(tables[0] ?? '')
  const [columns, setColumns] = useState<string[]>([])
  const [filters, setFilters] = useState([{ field: '', op: '=', value: '' }])
  const [orderBy, setOrderBy] = useState('')
  const [orderDir, setOrderDir] = useState('ASC')
  const [limit, setLimit] = useState('100')

  const availableColumns = schema?.tables?.[table] ?? []

  // Reset column/filter/sort selections when the table changes underneath them.
  useEffect(() => {
    setColumns([])
    setFilters([{ field: '', op: '=', value: '' }])
    setOrderBy('')
  }, [table])

  const toggleColumn = (c) => {
    setColumns(prev => prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c])
  }
  const updateFilter = (i, patch) => {
    setFilters(prev => prev.map((f, idx) => idx === i ? { ...f, ...patch } : f))
  }
  const addFilter = () => setFilters(prev => [...prev, { field: '', op: '=', value: '' }])
  const removeFilter = (i) => setFilters(prev => prev.filter((_, idx) => idx !== i))

  const sql = useMemo(
    () => buildSql({ table, columns, filters, orderBy, orderDir, limit }),
    [table, columns, filters, orderBy, orderDir, limit],
  )

  if (tables.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-2/30 p-4 text-center">
        <p className="text-[11px] text-muted leading-relaxed">
          No tables in the schema catalog yet — the visual builder needs at least
          one registered query's output to know what tables/columns exist. Write
          and save a SQL query first, or use the SQL editor directly.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-surface-2/30 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-fg">
          <Wand2 size={12} className="text-primary" /> Build a query
        </span>
        <button onClick={onClose} className="text-[11px] text-muted hover:text-fg transition-colors">Close</button>
      </div>

      <div className="flex items-center gap-2">
        <label className="text-[11px] font-medium text-muted shrink-0 w-14">Table</label>
        <select className={selectCls} value={table} onChange={e => setTable(e.target.value)}>
          {tables.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>

      <div>
        <label className="text-[11px] font-medium text-muted block mb-1.5">
          Columns <span className="text-muted/60">(none selected = *)</span>
        </label>
        <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto">
          {availableColumns.map(c => (
            <button
              key={c}
              onClick={() => toggleColumn(c)}
              className={`px-2 py-0.5 rounded-full border text-[10.5px] font-mono transition-colors ${
                columns.includes(c) ? 'bg-primary text-primary-fg border-primary' : 'border-border text-muted hover:text-fg'
              }`}
            >
              {c}
            </button>
          ))}
          {availableColumns.length === 0 && <p className="text-[10.5px] text-muted/70">No columns known for this table.</p>}
        </div>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label className="text-[11px] font-medium text-muted">Filters (AND)</label>
          <button onClick={addFilter} className="flex items-center gap-1 text-[11px] text-primary hover:text-primary/80 transition-colors">
            <Plus size={11} /> Add filter
          </button>
        </div>
        {filters.map((f, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <select className={selectCls + ' flex-1'} value={f.field} onChange={e => updateFilter(i, { field: e.target.value })}>
              <option value="">Column…</option>
              {availableColumns.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className={selectCls + ' w-28'} value={f.op} onChange={e => updateFilter(i, { op: e.target.value })}>
              {OPERATORS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            {!NO_VALUE_OPS.has(f.op) && (
              <input
                type="text"
                className={inputCls + ' flex-1'}
                placeholder="value"
                value={f.value}
                onChange={e => updateFilter(i, { value: e.target.value })}
              />
            )}
            <button onClick={() => removeFilter(i)} className="p-1 text-muted hover:text-red-500 shrink-0">
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <label className="text-[11px] font-medium text-muted shrink-0 w-14">Sort</label>
        <select className={selectCls} value={orderBy} onChange={e => setOrderBy(e.target.value)}>
          <option value="">None</option>
          {availableColumns.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        {orderBy && (
          <select className={selectCls + ' w-24'} value={orderDir} onChange={e => setOrderDir(e.target.value)}>
            <option value="ASC">ASC</option>
            <option value="DESC">DESC</option>
          </select>
        )}
        <label className="text-[11px] font-medium text-muted shrink-0 ml-2">Limit</label>
        <input
          type="number"
          min={1}
          className={inputCls + ' w-20'}
          value={limit}
          onChange={e => setLimit(e.target.value)}
        />
      </div>

      <pre className="text-[10.5px] font-mono text-fg bg-surface rounded-lg px-2.5 py-2 overflow-x-auto whitespace-pre-wrap break-words border border-border/60">
        {sql || '— pick a table to preview the generated SQL —'}
      </pre>

      <button
        onClick={() => sql && onGenerate(sql)}
        disabled={!sql}
        className="w-full h-8 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
      >
        Insert into editor
      </button>
    </div>
  )
}
