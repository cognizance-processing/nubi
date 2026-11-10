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

import { Table2, Hash, Palette, Rows3, FileSpreadsheet, ChevronUp, ChevronDown, Copy, Trash2 } from 'lucide-react'
import {
  inputCls, selectCls, FieldLabel, SectionLabel, Section, ColorSwatch, ColorField, ToggleRow,
} from './inspectorPrimitives.jsx'
import { useColumnIntrospection } from './useInspectorData.js'
import { parseNumberInput } from './numberInput.js'
import { FORMAT_OPS, formatOpLabel, COLUMN_FORMAT_TYPES, NUMBER_DISPLAY_FORMATS, PIVOT_AGGS } from './constants.js'

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
                className={`px-2 h-7 text-[11px] font-mono rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${value.includes(col)
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
            onChange={e => set('fontSize', parseNumberInput(e.target.value, { integer: true, min: 1 }))} />
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

const checkboxLabelCls = 'flex items-center gap-1.5 text-[11px] text-muted cursor-pointer'

/** Drop undefined keys; undefined when nothing is left. */
function compact(obj) {
  const next = { ...obj }
  for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k]
  return Object.keys(next).length ? next : undefined
}

export function ColumnFormatsEditor({ columns, value, onChange }) {
  // A column keeps its entry while ANY setting is present — prefix/suffix, a
  // chip, a link or a subtotal are all meaningful on a raw (untyped) column.
  const setFmt = (col, patch) => {
    const next = { ...value }
    const merged = compact({ ...(next[col] ?? {}), ...patch })
    if (!merged) delete next[col]
    else next[col] = merged
    onChange(next)
  }
  const setLink = (col, fmt, patch) => setFmt(col, { link: { ...(fmt.link ?? {}), ...patch } })
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
        const numeric = fmt.type === 'number' || fmt.type === 'percent'
        const link = fmt.link
        return (
          <div key={col} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface">
            <div className="flex items-center gap-1.5">
              <span className="flex-1 text-xs font-mono text-fg truncate">{col}</span>
              <select className={`${selectCls} w-28`} value={fmt.type ?? ''} onChange={e => setFmt(col, { type: e.target.value || undefined })}>
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
                {fmt.type === 'number' && (
                  <select className={`${selectCls} flex-1`} title="Number display" value={fmt.numberFormat ?? ''}
                    onChange={e => setFmt(col, { numberFormat: e.target.value || undefined })}>
                    {NUMBER_DISPLAY_FORMATS.map(([v, lbl]) => <option key={v} value={v}>{lbl}</option>)}
                  </select>
                )}
              </div>
            )}
            {fmt.type === 'date' && (
              <select className={selectCls} value={fmt.dateStyle ?? 'short'} onChange={e => setFmt(col, { dateStyle: e.target.value })}>
                {['short', 'medium', 'long', 'full'].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            )}
            <div className="flex gap-1.5">
              <input type="text" placeholder="prefix" className={`${inputCls} flex-1`}
                value={fmt.prefix ?? ''} onChange={e => setFmt(col, { prefix: e.target.value || undefined })} />
              <input type="text" placeholder="suffix" className={`${inputCls} flex-1`}
                value={fmt.suffix ?? ''} onChange={e => setFmt(col, { suffix: e.target.value || undefined })} />
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              {numeric && (
                <>
                  <label className={checkboxLabelCls}>
                    <input type="checkbox" checked={!!fmt.delta}
                      onChange={e => setFmt(col, { delta: e.target.checked || undefined })} />
                    Color by sign
                  </label>
                  <label className={checkboxLabelCls}>
                    <input type="checkbox" checked={!!fmt.bar}
                      onChange={e => setFmt(col, { bar: e.target.checked || undefined })} />
                    Show bar
                  </label>
                </>
              )}
              <label className={checkboxLabelCls}>
                <input type="checkbox" checked={!!fmt.chip}
                  onChange={e => setFmt(col, { chip: e.target.checked || undefined })} />
                Chip
              </label>
              <label className={checkboxLabelCls}>
                <input type="checkbox" checked={!!link}
                  onChange={e => setFmt(col, { link: e.target.checked ? { template: '' } : undefined })} />
                Link
              </label>
            </div>
            {numeric && fmt.bar && (
              <div className="flex items-center gap-1.5">
                <select className={`${selectCls} w-24`} title="Bar style" value={fmt.barStyle ?? 'inline'}
                  onChange={e => setFmt(col, { barStyle: e.target.value === 'inline' ? undefined : e.target.value })}>
                  <option value="inline">inline</option>
                  <option value="fill">fill</option>
                  <option value="progress">progress</option>
                </select>
                <select className={`${selectCls} flex-1`} title="Bar maximum" value={fmt.barMaxColumn ?? ''}
                  onChange={e => setFmt(col, { barMaxColumn: e.target.value || undefined })}>
                  <option value="">max: column max</option>
                  {columns.filter(c => c !== col).map(c => <option key={c} value={c}>max: {c}</option>)}
                </select>
                <ColorSwatch size="sm" title="Bar colour" value={fmt.barColor} fallback="#1f4fd1"
                  onChange={v => setFmt(col, { barColor: v || undefined })} />
              </div>
            )}
            {fmt.chip && (
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">chip
                  <ColorSwatch size="sm" title="Chip colour" value={fmt.chipColor} fallback="#dbe4fb"
                    onChange={v => setFmt(col, { chipColor: v || undefined })} />
                </label>
                <label className="flex items-center gap-1 text-[10px] text-muted cursor-pointer">text
                  <ColorSwatch size="sm" title="Chip text colour" value={fmt.chipTextColor} fallback="#1f2937"
                    onChange={v => setFmt(col, { chipTextColor: v || undefined })} />
                </label>
              </div>
            )}
            {link && (
              <div className="space-y-1.5">
                <input type="text" placeholder="https://example.com/item/{{value}}" className={`${inputCls} font-mono`}
                  value={link.template ?? ''} onChange={e => setLink(col, fmt, { template: e.target.value })} />
                <p className="text-[10px] text-muted/70">
                  {'{{value}}'} is this cell; {'{{column}}'} reads another column. Only http(s), mailto and /paths open.
                </p>
                <div className="flex items-center gap-1.5">
                  <select className={`${selectCls} flex-1`} value={link.labelMode ?? 'cellValue'}
                    onChange={e => setLink(col, fmt, { labelMode: e.target.value === 'cellValue' ? undefined : e.target.value })}>
                    <option value="cellValue">Label: cell value</option>
                    <option value="customText">Label: custom text</option>
                    <option value="button">Button</option>
                  </select>
                  <label className={checkboxLabelCls}>
                    <input type="checkbox" checked={link.openInNewTab !== false}
                      onChange={e => setLink(col, fmt, { openInNewTab: e.target.checked ? undefined : false })} />
                    New tab
                  </label>
                </div>
                {(link.labelMode === 'customText' || link.labelMode === 'button') && (
                  <input type="text" placeholder="Open" className={inputCls}
                    value={link.customText ?? ''} onChange={e => setLink(col, fmt, { customText: e.target.value || undefined })} />
                )}
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

const ruleIconBtnCls = 'w-7 h-7 shrink-0 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-30 disabled:pointer-events-none focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50'

/** Full-width two-way toggle (Value | Column, Cell | Row). */
function Segmented({ options, value, onChange }) {
  return (
    <div className="flex h-8 rounded-lg border border-border overflow-hidden">
      {options.map(([key, label, title]) => (
        <button key={key} type="button" title={title} aria-pressed={value === key} onClick={() => onChange(key)}
          className={`flex-1 text-[12px] font-medium transition-colors ${value === key ? 'bg-[var(--ink)] text-[var(--ink-fg)]' : 'bg-surface text-muted hover:text-fg'}`}>
          {label}
        </button>
      ))}
    </div>
  )
}

/** Swatch + colour text in one box, narrow enough for a three-up row. */
function RuleColorField({ value, fallback, placeholder, onChange }) {
  return (
    <div className="flex items-center gap-1.5 h-8 px-1.5 rounded-lg border border-border bg-surface min-w-0">
      <ColorSwatch size="sm" value={value} fallback={fallback} title="Pick a colour" onChange={onChange} />
      <input type="text" className="flex-1 min-w-0 bg-transparent text-[12px] font-mono text-fg placeholder:text-muted/60 focus:outline-none"
        placeholder={placeholder} value={value ?? ''} onChange={e => onChange(e.target.value || undefined)} />
    </div>
  )
}

/** A sample of what a matching cell will look like. */
function RulePreview({ rule }) {
  const s = rule.style ?? {}
  const text = rule.compareColumn != null
    ? `${formatOpLabel(rule.op ?? 'gt')} ${rule.compareColumn || '…'}`
    : (String(rule.value ?? '').trim() || 'Aa')
  const border = s.border ?? (s.borderWidth || s.borderColor
    ? `${s.borderWidth || 1}px solid ${s.borderColor || 'currentColor'}` : undefined)
  return (
    <span className="inline-flex items-center h-6 px-2 rounded-md text-[11px] max-w-[9rem] truncate border border-border"
      style={{ backgroundColor: s.backgroundColor, color: s.color, fontWeight: s.fontWeight, ...(border ? { border } : {}) }}
      title="Preview">
      {text}
    </span>
  )
}

export function ConditionalRulesEditor({ columns, rules, onChange }) {
  const addRule = () => onChange([...rules, {
    column: columns[0] ?? '', op: 'gt', value: '', scope: 'cell', enabled: true,
    style: { backgroundColor: '#dcfce7', color: '#166534' },
  }])
  const setRule = (idx, patch) => onChange(rules.map((r, i) => i === idx ? compact({ ...r, ...patch }) : r))
  const setStyle = (idx, patch) => onChange(rules.map((r, i) => i === idx ? { ...r, style: compact({ ...r.style, ...patch }) ?? {} } : r))
  const removeRule = (idx) => onChange(rules.filter((_, i) => i !== idx))
  // Order matters: later rules win, so moving a rule changes which style shows.
  const moveRule = (idx, dir) => {
    const to = idx + dir
    if (to < 0 || to >= rules.length) return
    const next = [...rules]
      ;[next[idx], next[to]] = [next[to], next[idx]]
    onChange(next)
  }
  const duplicateRule = (idx) => onChange([...rules.slice(0, idx + 1), structuredClone(rules[idx]), ...rules.slice(idx + 1)])
  const setCompareMode = (idx, r, mode) => setRule(idx, mode === 'column'
    ? { compareColumn: r.compareColumn || columns.find(c => c !== r.column) || '' }
    : { compareColumn: undefined, compareColumn2: undefined })

  return (
    <div className="space-y-3">
      <p className="text-[12px] text-muted leading-snug">
        Rules run top to bottom. When rules overlap, the later one wins.
      </p>
      {rules.map((r, idx) => {
        const enabled = r.enabled !== false && r.isActive !== false
        const byColumn = r.compareColumn != null
        const others = columns.filter(c => c !== r.column)
        const s = r.style ?? {}
        return (
          <div key={idx} className="rounded-xl border border-border bg-surface p-3 space-y-3">
            <div className="flex items-center gap-2">
              <input type="checkbox" className="h-4 w-4 shrink-0 cursor-pointer accent-[var(--success,#16a34a)]"
                title={enabled ? 'Rule on — click to disable' : 'Rule off — click to enable'}
                checked={enabled}
                onChange={e => setRule(idx, { enabled: e.target.checked, isActive: undefined })} />
              <span className="text-[13px] font-semibold text-fg shrink-0">Rule {idx + 1}</span>
              <span className="min-w-0 flex-1"><RulePreview rule={r} /></span>
              <div className="flex items-center shrink-0">
                <button onClick={() => moveRule(idx, -1)} disabled={idx === 0} title="Move up" className={ruleIconBtnCls}><ChevronUp size={14} /></button>
                <button onClick={() => moveRule(idx, 1)} disabled={idx === rules.length - 1} title="Move down" className={ruleIconBtnCls}><ChevronDown size={14} /></button>
                <button onClick={() => duplicateRule(idx)} title="Duplicate rule" className={ruleIconBtnCls}><Copy size={13} /></button>
                <button onClick={() => removeRule(idx)} title="Delete rule" className={`${ruleIconBtnCls} !text-[var(--danger,#ef4444)]`}><Trash2 size={14} /></button>
              </div>
            </div>

            <div className={`space-y-3${enabled ? '' : ' opacity-50'}`}>
              <div className="space-y-2">
                <SectionLabel>When</SectionLabel>
                <div className="grid grid-cols-2 gap-2">
                  <select className={`${selectCls} min-w-0`} value={r.column ?? ''} onChange={e => setRule(idx, { column: e.target.value })}>
                    {!r.column && <option value="">Column…</option>}
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <select className={`${selectCls} min-w-0`} title={formatOpLabel(r.op ?? 'gt')} value={r.op ?? 'gt'} onChange={e => setRule(idx, { op: e.target.value })}>
                    {FORMAT_OPS.map(o => <option key={o} value={o}>{formatOpLabel(o)}</option>)}
                  </select>
                </div>
                {byColumn ? (
                  <div className={r.op === 'between' ? 'grid grid-cols-2 gap-2' : ''}>
                    <select className={`${selectCls} min-w-0`} value={r.compareColumn ?? ''}
                      onChange={e => setRule(idx, { compareColumn: e.target.value })}>
                      {!r.compareColumn && <option value="">Column…</option>}
                      {others.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                    {r.op === 'between' && (
                      <select className={`${selectCls} min-w-0`} value={r.compareColumn2 ?? ''}
                        onChange={e => setRule(idx, { compareColumn2: e.target.value || undefined })}>
                        <option value="">and…</option>
                        {others.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    )}
                  </div>
                ) : (
                  <div className={r.op === 'between' ? 'grid grid-cols-2 gap-2' : ''}>
                    <input type="text" placeholder={r.op === 'between' ? 'From' : 'Value'} className={`${inputCls} min-w-0`}
                      value={r.value ?? ''} onChange={e => setRule(idx, { value: e.target.value })} />
                    {r.op === 'between' && (
                      <input type="text" placeholder="To" className={`${inputCls} min-w-0`}
                        value={r.value2 ?? ''} onChange={e => setRule(idx, { value2: e.target.value })} />
                    )}
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <FieldLabel>Compare to</FieldLabel>
                    <Segmented value={byColumn ? 'column' : 'static'} onChange={mode => setCompareMode(idx, r, mode)}
                      options={[['static', 'Value', 'Compare against a fixed value'], ['column', 'Column', 'Compare against another column of the same row']]} />
                  </div>
                  <div>
                    <FieldLabel>Apply to</FieldLabel>
                    <Segmented value={r.scope ?? 'cell'} onChange={sc => setRule(idx, { scope: sc })}
                      options={[['cell', 'Cell', 'Style only the matching cell'], ['row', 'Row', 'Style the whole row']]} />
                  </div>
                </div>
              </div>

              <div className="space-y-2 border-t border-border pt-3">
                <SectionLabel>Then style</SectionLabel>
                <div className="grid grid-cols-3 gap-2">
                  <div className="min-w-0">
                    <FieldLabel>Background</FieldLabel>
                    <RuleColorField value={s.backgroundColor} fallback="#dcfce7" placeholder="none"
                      onChange={v => setStyle(idx, { backgroundColor: v })} />
                  </div>
                  <div className="min-w-0">
                    <FieldLabel>Text</FieldLabel>
                    <RuleColorField value={s.color} fallback="#166534" placeholder="auto"
                      onChange={v => setStyle(idx, { color: v })} />
                  </div>
                  <div className="min-w-0">
                    {/* Border: width + colour, folded into a CSS `border` by evalRules(). */}
                    <FieldLabel>Border</FieldLabel>
                    <RuleColorField value={s.borderColor} fallback="#166534" placeholder="none"
                      onChange={v => setStyle(idx, v
                        ? { borderColor: v, borderWidth: s.borderWidth || 1 }
                        : { borderColor: undefined, borderWidth: undefined })} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <FieldLabel>Border width</FieldLabel>
                    <div className="relative">
                      <input type="number" min={0} max={8} placeholder="0" title="Border width in px; 0 or empty = no border"
                        className={`${inputCls} pr-8`}
                        value={s.borderWidth ?? ''}
                        onChange={e => {
                          const w = parseNumberInput(e.target.value, { integer: true, min: 0, max: 8 })
                          setStyle(idx, w ? { borderWidth: w, borderColor: s.borderColor ?? '#166534' } : { borderWidth: undefined, borderColor: undefined })
                        }} />
                      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[11px] text-muted">px</span>
                    </div>
                  </div>
                  <div>
                    <FieldLabel>Weight</FieldLabel>
                    <button type="button" aria-pressed={s.fontWeight === 'bold'}
                      onClick={() => setStyle(idx, { fontWeight: s.fontWeight === 'bold' ? undefined : 'bold' })}
                      className={`w-full h-8 rounded-lg border text-[12px] transition-colors ${s.fontWeight === 'bold' ? 'font-bold border-[var(--ink)] bg-surface-2 text-fg' : 'font-medium border-border text-muted hover:text-fg'}`}>
                      Bold
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )
      })}
      <button type="button" onClick={(e) => { addRule(); e.currentTarget.blur() }}
        className="w-full h-10 text-[12px] font-medium rounded-xl border border-dashed border-border hover:border-fg text-muted hover:text-fg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50">
        + Add rule
      </button>
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
                  className={`px-2 h-7 text-[11px] font-mono rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${on && selected.includes(col) ? 'bg-[var(--ink)] text-[var(--ink-fg)] border-[var(--ink)]'
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
          <FieldLabel>Appearance</FieldLabel>
          <select className={selectCls} value={props.appearance ?? 'default'}
            onChange={e => setProps('appearance', e.target.value === 'default' ? undefined : e.target.value)}>
            <option value="default">Compact</option>
            <option value="report">Report (roomy rows, larger type)</option>
          </select>
        </div>
        <ToggleRow label="Summary row" hint="Totals under the last row, using each column's aggregation"
          checked={props.summaryRow === true}
          onChange={v => setProps('summaryRow', v ? true : undefined)} />
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
