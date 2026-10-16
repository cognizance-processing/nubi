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

import { Table2, Hash, Palette, Rows3, FileSpreadsheet } from 'lucide-react'
import {
  inputCls, selectCls, FieldLabel, SectionLabel, Section, ColorSwatch, ColorField, ToggleRow,
} from './inspectorPrimitives.jsx'
import { useColumnIntrospection } from './useInspectorData.js'
import { FORMAT_OPS, COLUMN_FORMAT_TYPES, PIVOT_AGGS } from './constants.js'

/** Coerce props.columns (array | comma-string | undefined) to a string[]. */
function columnsToArray(raw) {
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string' && raw) return raw.split(',').map(c => c.trim()).filter(Boolean)
  return []
}

/**
 * A small multi-select of column names, rendered as toggle chips. Used for the
 * left/right pin lists, where order matters less than membership and the set is
 * usually one or two columns.
 */
function PinList({ label, columns, value, onChange }) {
  const toggle = (col) => {
    onChange(value.includes(col) ? value.filter(c => c !== col) : [...value, col])
  }
  return (
    <div className="space-y-1.5">
      <FieldLabel className="mb-0">{label}</FieldLabel>
      {columns.length === 0
        ? <p className="text-[10px] text-muted/70">Pick a query to list columns.</p>
        : (
          <div className="flex flex-wrap gap-1.5">
            {columns.map(col => (
              <button key={col} onClick={() => toggle(col)}
                className={`px-2 h-7 text-[11px] font-mono rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${
                  value.includes(col)
                    ? 'bg-[var(--ink)] text-[var(--ink-fg)] border-[var(--ink)]'
                    : 'bg-surface text-muted border-border hover:border-fg'
                }`}>
                {col}
              </button>
            ))}
          </div>
        )}
    </div>
  )
}

/**
 * Font / fill / alignment fields for ONE Excel cell style (the header row or
 * the body cells). Colours must be literal hex — a theme token has no meaning
 * inside a workbook, so the writer drops anything else.
 */
function ExportStyleFields({ value, onChange }) {
  const set = (key, v) => {
    const next = { ...value, [key]: v }
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k]
    onChange(Object.keys(next).length ? next : undefined)
  }
  return (
    <div className="space-y-1.5">
      <div className="flex gap-1.5">
        <div className="flex-1">
          <FieldLabel>Text</FieldLabel>
          <ColorField value={value.fontColor} placeholder="#000000"
            onChange={v => set('fontColor', v || undefined)} />
        </div>
      </div>
      <div className="flex gap-1.5">
        <div className="flex-1">
          <FieldLabel>Fill</FieldLabel>
          <ColorField value={value.backgroundColor} placeholder="none"
            onChange={v => set('backgroundColor', v || undefined)} />
        </div>
      </div>
      <div className="flex gap-1.5">
        <div className="w-20">
          <FieldLabel>Size</FieldLabel>
          <input type="number" min={6} max={72} className={inputCls} placeholder="auto"
            value={value.fontSize ?? ''}
            onChange={e => set('fontSize', e.target.value === '' ? undefined : (parseInt(e.target.value, 10) || undefined))} />
        </div>
        <div className="flex-1">
          <FieldLabel>Align</FieldLabel>
          <select className={selectCls} value={value.alignment ?? ''}
            onChange={e => set('alignment', e.target.value || undefined)}>
            <option value="">default</option>
            <option value="left">left</option>
            <option value="center">center</option>
            <option value="right">right</option>
          </select>
        </div>
      </div>
      <div className="flex items-center gap-3">
        {[['fontBold', 'Bold'], ['fontItalic', 'Italic'], ['fontUnderline', 'Underline']].map(([k, lbl]) => (
          <label key={k} className="flex items-center gap-1.5 text-[11px] text-muted cursor-pointer">
            <input type="checkbox" checked={!!value[k]} onChange={e => set(k, e.target.checked || undefined)} />
            {lbl}
          </label>
        ))}
      </div>
    </div>
  )
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
            {(fmt.type === 'number' || fmt.type === 'percent') && (
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1.5 text-[11px] text-muted cursor-pointer">
                  <input type="checkbox" checked={!!fmt.delta}
                    onChange={e => setFmt(col, { delta: e.target.checked || undefined })} />
                  Color by sign
                </label>
                <label className="flex items-center gap-1.5 text-[11px] text-muted cursor-pointer">
                  <input type="checkbox" checked={!!fmt.bar}
                    onChange={e => setFmt(col, { bar: e.target.checked || undefined })} />
                  Show bar
                </label>
              </div>
            )}
            {/* Subtotal shown on a group row. Numeric columns default to a sum;
                this is how you get a count or an average instead. */}
            <div>
              <FieldLabel>Group subtotal</FieldLabel>
              <select className={selectCls} value={fmt.aggregation ?? ''}
                onChange={e => setFmt(col, { aggregation: e.target.value || undefined })}>
                <option value="">Default (sum for numbers)</option>
                <option value="none">None</option>
                {PIVOT_AGGS.map(a => <option key={a} value={a === 'avg' ? 'mean' : a}>{a}</option>)}
              </select>
            </div>
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
          className="text-[11px] font-medium px-2 h-6 rounded-lg border border-dashed border-border hover:border-fg text-muted hover:text-fg transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
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
                  className={`px-2.5 text-[11px] font-medium capitalize transition-colors ${(r.scope ?? 'cell') === sc ? 'bg-[var(--ink)] text-[var(--ink-fg)]' : 'bg-surface text-muted hover:text-fg'}`}>
                  {sc}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">bg
              <ColorSwatch size="sm" title="Row background"
                value={r.style?.backgroundColor} fallback="#dcfce7"
                onChange={v => setStyle(idx, { backgroundColor: v })} />
            </label>
            <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">text
              <ColorSwatch size="sm" title="Row text colour"
                value={r.style?.color} fallback="#166534"
                onChange={v => setStyle(idx, { color: v })} />
            </label>
            <button onClick={() => setStyle(idx, { fontWeight: r.style?.fontWeight === 'bold' ? undefined : 'bold' })}
              className={`w-7 h-7 text-[11px] rounded-lg border font-bold transition-colors ${r.style?.fontWeight === 'bold' ? 'border-[var(--ink)] text-fg bg-surface-2' : 'border-border text-muted hover:text-fg'}`}>
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

  // exportSettings lives on props so it travels with the widget (and through
  // the library/ref merge) like every other authored setting.
  const xs = props.exportSettings ?? {}
  const setExport = (key, val) => {
    const next = { ...xs, [key]: val }
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k]
    setProps('exportSettings', Object.keys(next).length ? next : undefined)
  }
  const setExportNumber = (key, val) => {
    const nf = { ...(xs.numberFormat ?? {}), [key]: val }
    for (const k of Object.keys(nf)) if (nf[k] === undefined) delete nf[k]
    setExport('numberFormat', Object.keys(nf).length ? nf : undefined)
  }

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
              <button onClick={() => setProps('columns', [])} className="text-[10px] font-medium text-muted hover:text-fg transition-colors">
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
                    on && selected.includes(col) ? 'bg-[var(--ink)] text-[var(--ink-fg)] border-[var(--ink)]'
                      : on ? 'bg-surface text-fg border-border hover:border-fg'
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

      <Section title="Layout & paging" defaultOpen={false} icon={Rows3}>
        <div>
          <FieldLabel>Row density</FieldLabel>
          <select className={selectCls} value={props.density ?? 'compact'}
            onChange={e => setProps('density', e.target.value)}>
            <option value="compact">Compact</option>
            <option value="standard">Standard</option>
            <option value="comfortable">Comfortable</option>
          </select>
        </div>
        <ToggleRow
          label="Paginate"
          hint="Off scrolls the whole result set instead"
          checked={props.pagination !== false}
          onChange={v => setProps('pagination', v ? undefined : false)}
        />
        {props.pagination !== false && (
          <div>
            <FieldLabel>Rows per page</FieldLabel>
            <input type="number" min={1} max={1000} className={inputCls} value={props.pageSize ?? 50}
              onChange={e => setProps('pageSize', parseInt(e.target.value, 10) || 50)} />
          </div>
        )}
        <ToggleRow
          label="Show toolbar"
          hint="Search, column, density and export controls"
          checked={props.toolbar !== false}
          onChange={v => setProps('toolbar', v ? undefined : false)}
        />
        <div>
          <FieldLabel>Group rows by</FieldLabel>
          <select
            className={selectCls}
            value={columnsToArray(props.groupBy)[0] ?? ''}
            onChange={e => setProps('groupBy', e.target.value ? [e.target.value] : undefined)}
          >
            <option value="">— no grouping —</option>
            {fmtCols.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <p className="text-[10px] text-muted/70 mt-1">
            Grouped rows get a subtotal row; per-column subtotals are set under Column formats.
          </p>
        </div>
        <PinList label="Pinned left" columns={fmtCols} value={columnsToArray(props.pinnedLeft)}
          onChange={v => setProps('pinnedLeft', v.length ? v : undefined)} />
        <PinList label="Pinned right" columns={fmtCols} value={columnsToArray(props.pinnedRight)}
          onChange={v => setProps('pinnedRight', v.length ? v : undefined)} />
      </Section>

      <Section title="Table style" defaultOpen={false} icon={Palette}>
        <p className="text-[10px] text-muted/70">
          Leave these empty to inherit the board theme, which keeps the table
          readable in both light and dark.
        </p>
        <div>
          <FieldLabel>Header background</FieldLabel>
          <ColorField value={props.headerBackground} placeholder="theme default"
            onChange={v => setProps('headerBackground', v || undefined)} />
        </div>
        <div>
          <FieldLabel>Header text</FieldLabel>
          <ColorField value={props.headerColor} placeholder="theme default"
            onChange={v => setProps('headerColor', v || undefined)} />
        </div>
        <div>
          <FieldLabel>Row colour (even)</FieldLabel>
          <ColorField value={props.rowColorEven} placeholder="theme default"
            onChange={v => setProps('rowColorEven', v || undefined)} />
        </div>
        <div>
          <FieldLabel>Row colour (odd)</FieldLabel>
          <ColorField value={props.rowColorOdd} placeholder="theme default"
            onChange={v => setProps('rowColorOdd', v || undefined)} />
        </div>
      </Section>

      <Section title="Excel export" defaultOpen={false} icon={FileSpreadsheet}>
        <p className="text-[10px] text-muted/70">
          Applies to the toolbar’s “Export Excel” only — the on-screen table and
          the CSV are unaffected.
        </p>
        <div>
          <FieldLabel>Sheet name</FieldLabel>
          <input type="text" className={inputCls} placeholder="Sheet1"
            value={xs.sheetName ?? ''} onChange={e => setExport('sheetName', e.target.value || undefined)} />
        </div>
        <ToggleRow label="Freeze header row" checked={!!xs.freezeHeader}
          onChange={v => setExport('freezeHeader', v || undefined)} />
        <ToggleRow label="Freeze first column" checked={!!xs.freezeFirstColumn}
          onChange={v => setExport('freezeFirstColumn', v || undefined)} />

        <SectionLabel>Header row</SectionLabel>
        <ExportStyleFields
          value={xs.headerStyling ?? {}}
          onChange={v => setExport('headerStyling', v)}
        />

        <SectionLabel>Body cells</SectionLabel>
        <ExportStyleFields
          value={xs.cellStyling ?? {}}
          onChange={v => setExport('cellStyling', v)}
        />

        <SectionLabel>Numbers</SectionLabel>
        <div className="flex gap-1.5">
          <div className="flex-1">
            <FieldLabel>Decimals</FieldLabel>
            <input type="number" min={0} max={10} className={inputCls} placeholder="auto"
              value={xs.numberFormat?.decimalPlaces ?? ''}
              onChange={e => setExportNumber('decimalPlaces', e.target.value === '' ? undefined : Math.max(0, parseInt(e.target.value, 10) || 0))} />
          </div>
          <div className="flex-1">
            <FieldLabel>Currency symbol</FieldLabel>
            <input type="text" className={inputCls} placeholder="none"
              value={xs.numberFormat?.currencySymbol ?? ''}
              onChange={e => setExportNumber('currencySymbol', e.target.value || undefined)} />
          </div>
        </div>
        <ToggleRow label="Thousands separator"
          checked={!!xs.numberFormat?.thousandSeparator}
          onChange={v => setExportNumber('thousandSeparator', v || undefined)} />

        <SectionLabel>Columns</SectionLabel>
        <PinList
          label="Exclude from the export"
          columns={fmtCols}
          value={columnsToArray(xs.columnExcluded)}
          onChange={v => setExport('columnExcluded', v.length ? v : undefined)}
        />
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
