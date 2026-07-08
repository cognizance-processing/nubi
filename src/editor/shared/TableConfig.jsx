/**
 * shared/TableConfig.jsx — Table widget configuration panel, including
 * ColumnFormatsEditor and ConditionalRulesEditor sub-panels.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Exports:
 *   TableConfig            React component
 *   ColumnFormatsEditor    React component (also usable standalone)
 *   ConditionalRulesEditor React component (also usable standalone)
 *
 * Props (TableConfig):
 *   widget    object    — spec widget (type='table')
 *   onChange  (w)=>void — full widget update callback
 *
 * Props (ColumnFormatsEditor):
 *   columns   string[]
 *   value     object    — { col: { type, decimals, ... } }
 *   onChange  (cf)=>void
 *
 * Props (ConditionalRulesEditor):
 *   columns   string[]
 *   rules     array
 *   onChange  (rules)=>void
 */

import { Table2, Hash, Palette } from 'lucide-react'
import { inputCls, selectCls, FieldLabel, Section } from './inspectorPrimitives.jsx'
import { useColumnIntrospection } from './useInspectorData.js'
import { FORMAT_OPS, COLUMN_FORMAT_TYPES } from './constants.js'

/** Coerce props.columns (array | comma-string | undefined) to a string[]. */
function columnsToArray(raw) {
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string' && raw) return raw.split(',').map(c => c.trim()).filter(Boolean)
  return []
}

export function ColumnFormatsEditor({ columns, value, onChange }) {
  const setFmt = (col, patch) => {
    const next = { ...value }
    const merged = { ...(next[col] ?? {}), ...patch }
    if (!merged.type) delete next[col]
    else next[col] = merged
    onChange(next)
  }
  if (columns.length === 0) {
    return (
      <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center">
        No columns to format yet.
      </p>
    )
  }
  return (
    <div className="space-y-2">
      {columns.map(col => {
        const fmt = value[col] ?? {}
        return (
          <div key={col} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface">
            <div className="flex items-center gap-1.5">
              <span className="flex-1 text-xs font-mono text-fg truncate">{col}</span>
              <select className={`${selectCls} w-28`} value={fmt.type ?? ''} onChange={e => setFmt(col, { type: e.target.value })}>
                <option value="">raw</option>
                {COLUMN_FORMAT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            {(fmt.type === 'number' || fmt.type === 'currency' || fmt.type === 'percent') && (
              <div className="flex gap-1.5">
                <input type="number" min={0} max={10} placeholder="decimals" className={`${inputCls} flex-1`}
                  value={fmt.decimals ?? ''}
                  onChange={e => setFmt(col, { decimals: e.target.value === '' ? undefined : parseInt(e.target.value, 10) })} />
                {fmt.type === 'currency' && (
                  <input type="text" placeholder="USD" className={`${inputCls} w-20`}
                    value={fmt.currency ?? ''} onChange={e => setFmt(col, { currency: e.target.value || undefined })} />
                )}
              </div>
            )}
            {fmt.type === 'date' && (
              <select className={selectCls} value={fmt.dateStyle ?? 'short'} onChange={e => setFmt(col, { dateStyle: e.target.value })}>
                {['short', 'medium', 'long', 'full'].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function ConditionalRulesEditor({ columns, rules, onChange }) {
  const addRule = () => onChange([...rules, {
    column: columns[0] ?? '', op: 'gt', value: '', scope: 'cell',
    style: { backgroundColor: '#dcfce7', color: '#166534' },
  }])
  const setRule = (idx, patch) => onChange(rules.map((r, i) => i === idx ? { ...r, ...patch } : r))
  const setStyle = (idx, patch) => onChange(rules.map((r, i) => i === idx ? { ...r, style: { ...r.style, ...patch } } : r))
  const removeRule = (idx) => onChange(rules.filter((_, i) => i !== idx))

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[10px] text-muted/70">When a cell matches, apply a style.</p>
        <button onClick={addRule}
          className="text-[11px] font-medium px-2 h-6 rounded-lg border border-dashed border-border hover:border-primary text-muted hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
          + Add rule
        </button>
      </div>
      {rules.length === 0 && (
        <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center">
          No rules yet.
        </p>
      )}
      {rules.map((r, idx) => (
        <div key={idx} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface">
          <div className="flex items-center gap-1.5">
            <select className={`${selectCls} flex-1`} value={r.column ?? ''} onChange={e => setRule(idx, { column: e.target.value })}>
              {!r.column && <option value="">column…</option>}
              {columns.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className={`${selectCls} w-24`} value={r.op ?? 'gt'} onChange={e => setRule(idx, { op: e.target.value })}>
              {FORMAT_OPS.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
            <button onClick={() => removeRule(idx)} title="Remove rule"
              className="w-7 h-7 shrink-0 flex items-center justify-center text-xs rounded-lg border border-transparent hover:border-border hover:bg-surface-2 text-muted hover:text-fg transition-colors">✕</button>
          </div>
          <div className="flex gap-1.5">
            <input type="text" placeholder="value" className={`${inputCls} flex-1`}
              value={r.value ?? ''} onChange={e => setRule(idx, { value: e.target.value })} />
            {r.op === 'between' && (
              <input type="text" placeholder="and" className={`${inputCls} flex-1`}
                value={r.value2 ?? ''} onChange={e => setRule(idx, { value2: e.target.value })} />
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex h-7 rounded-lg border border-border overflow-hidden">
              {['cell', 'row'].map(sc => (
                <button key={sc} onClick={() => setRule(idx, { scope: sc })}
                  className={`px-2.5 text-[11px] font-medium capitalize transition-colors ${(r.scope ?? 'cell') === sc ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary'}`}>
                  {sc}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">bg
              <input type="color" className="h-6 w-6 rounded border border-border bg-surface cursor-pointer"
                value={r.style?.backgroundColor ?? '#dcfce7'}
                onChange={e => setStyle(idx, { backgroundColor: e.target.value })} />
            </label>
            <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">text
              <input type="color" className="h-6 w-6 rounded border border-border bg-surface cursor-pointer"
                value={r.style?.color ?? '#166534'}
                onChange={e => setStyle(idx, { color: e.target.value })} />
            </label>
            <button onClick={() => setStyle(idx, { fontWeight: r.style?.fontWeight === 'bold' ? undefined : 'bold' })}
              className={`w-7 h-7 text-[11px] rounded-lg border font-bold transition-colors ${r.style?.fontWeight === 'bold' ? 'border-primary text-primary bg-primary/5' : 'border-border text-muted hover:text-fg'}`}>
              B
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

export function TableConfig({ widget, onChange }) {
  const { columns: allCols, introspecting } = useColumnIntrospection(widget.query_id)
  const props = widget.props ?? {}
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })

  const selected = columnsToArray(props.columns)
  const toggleCol = (col) => {
    const next = selected.includes(col) ? selected.filter(c => c !== col) : [...selected, col]
    setProps('columns', next)
  }
  const fmtCols = selected.length > 0 ? selected : allCols

  return (
    <div className="space-y-3">
      <Section title="Rows & columns" icon={Table2}>
        <div>
          <FieldLabel>Row limit</FieldLabel>
          <input type="number" min={1} max={10000} className={inputCls} value={props.limit ?? 50}
            onChange={e => setProps('limit', parseInt(e.target.value, 10) || 50)} />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <FieldLabel className="mb-0">Visible columns</FieldLabel>
            {selected.length > 0 && (
              <button onClick={() => setProps('columns', [])} className="text-[10px] font-medium text-muted hover:text-primary transition-colors">
                show all
              </button>
            )}
          </div>
          {introspecting && <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>}
          {!introspecting && allCols.length === 0 && (
            <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center">
              Pick a query to list columns.
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {allCols.map(col => {
              const on = selected.length === 0 || selected.includes(col)
              return (
                <button key={col} onClick={() => toggleCol(col)}
                  className={`px-2 h-7 text-[11px] font-mono rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${
                    on && selected.includes(col) ? 'bg-primary text-primary-fg border-primary'
                      : on ? 'bg-surface text-fg border-border hover:border-primary'
                      : 'bg-surface text-muted/50 border-border line-through'
                  }`}>
                  {col}
                </button>
              )
            })}
          </div>
          {allCols.length > 0 && <p className="text-[10px] text-muted/70">None selected → all columns shown.</p>}
        </div>
      </Section>

      <Section title="Column formats" defaultOpen={false} icon={Hash}>
        <ColumnFormatsEditor
          columns={fmtCols}
          value={widget.columnFormats ?? {}}
          onChange={cf => onChange({ ...widget, columnFormats: cf })}
        />
      </Section>

      <Section title="Conditional formatting" defaultOpen={false} icon={Palette}>
        <ConditionalRulesEditor
          columns={fmtCols}
          rules={widget.formattingRules ?? []}
          onChange={rules => onChange({ ...widget, formattingRules: rules })}
        />
      </Section>
    </div>
  )
}
