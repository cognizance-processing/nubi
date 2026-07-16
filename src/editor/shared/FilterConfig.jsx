/**
 * shared/FilterConfig.jsx — Filter, Text, and Placement configuration panels.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Exports:
 *   PlacementControl   React component
 *   FilterConfig       React component
 *   TextConfig         React component
 *   applyPlacement     helper function
 *   effectivePlacement helper function
 *
 * Props (FilterConfig):
 *   widget    object    — spec widget (type='filter')
 *   onChange  (w)=>void — full widget update callback
 *
 * Props (TextConfig):
 *   widget    object    — spec widget (type='text')
 *   onChange  (w)=>void
 *
 * Props (PlacementControl):
 *   widget    object    — any spec widget
 *   onChange  (w)=>void
 */

import { LayoutGrid, Link2 } from 'lucide-react'
import { inputCls, selectCls, FieldLabel, ToggleRow, Section, ColorField } from './inspectorPrimitives.jsx'
import { QueryPicker } from './QueryPicker.jsx'
import { FILTER_SUBTYPES } from './constants.js'
import { effectivePlacement, applyPlacement } from './placementHelpers.js'

const SUBTYPE_LABELS = {
  select: 'Dropdown (single)',
  multiselect: 'Dropdown (multi-select)',
  list: 'List / nav rail',
  daterange: 'Date range',
  text: 'Search box',
}

// Re-export helpers so consumers can import them via this file OR directly from
// placementHelpers.js. The eslint react-refresh rule fires on mixing components
// with non-component exports — acceptable here since this is not a hot-module
// boundary (both are used internally to the editor).
// eslint-disable-next-line react-refresh/only-export-components
export { effectivePlacement, applyPlacement }

const PLACEMENT_OPTIONS = [
  { id: 'grid',   label: 'In grid',          hint: 'A normal grid cell you drag & resize.' },
  { id: 'header', label: 'Above grid (bar)', hint: 'A compact control in the filter bar above the grid.' },
  { id: 'drawer', label: 'In drawer',        hint: 'Lives in the slide-over Filters drawer.' },
]

export function PlacementControl({ widget, onChange }) {
  const current = effectivePlacement(widget)
  const active = PLACEMENT_OPTIONS.find(o => o.id === current) ?? PLACEMENT_OPTIONS[0]
  return (
    <div>
      <FieldLabel className="flex items-center gap-1.5">
        <LayoutGrid size={12} /> Placement
      </FieldLabel>
      <div className="grid grid-cols-3 gap-1.5" data-testid="widget-placement-control">
        {PLACEMENT_OPTIONS.map(o => (
          <button key={o.id} type="button"
            onClick={() => onChange(applyPlacement(widget, o.id))}
            data-testid={`placement-${o.id}`}
            title={o.hint}
            className={`h-8 px-1.5 text-[11px] font-medium rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${
              current === o.id ? 'bg-primary text-primary-fg border-primary shadow-sm' : 'bg-surface text-muted border-border hover:border-primary hover:text-primary'
            }`}>
            {o.label}
          </button>
        ))}
      </div>
      <p className="text-[10px] text-muted/70 mt-1">{active.hint}</p>
    </div>
  )
}

export function FilterConfig({ widget, onChange, spec }) {
  const setField = (key, val) => onChange({ ...widget, [key]: val })
  const props = widget.props ?? {}
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })
  const subtype = widget.subtype ?? 'select'
  const isChoice = subtype === 'select' || subtype === 'multiselect' || subtype === 'list'
  const varNames = (spec?.variables ?? []).map(v => v.name).filter(Boolean)
  const varName = widget.target_var ?? ''
  const knownVar = !varName || varNames.includes(varName)

  return (
    <div className="space-y-3">
      <PlacementControl widget={widget} onChange={onChange} />
      <div>
        <FieldLabel>Label</FieldLabel>
        <input type="text" className={inputCls} value={props.label ?? ''} placeholder="e.g. Region"
          onChange={e => setProps('label', e.target.value)} />
      </div>
      <div>
        <FieldLabel>Type</FieldLabel>
        <select className={selectCls} value={subtype} onChange={e => setField('subtype', e.target.value)}>
          {FILTER_SUBTYPES.map(s => <option key={s} value={s}>{SUBTYPE_LABELS[s] ?? s}</option>)}
        </select>
      </div>

      {/* ── Connect to widgets ── */}
      <div>
        <FieldLabel className="flex items-center gap-1.5"><Link2 size={12} /> Target variable</FieldLabel>
        <input type="text" list="filter-var-list" placeholder="e.g. region" className={inputCls}
          value={varName} onChange={e => setField('target_var', e.target.value)} />
        {varNames.length > 0 && (
          <datalist id="filter-var-list">
            {varNames.map(v => <option key={v} value={v} />)}
          </datalist>
        )}
        <p className="text-[10px] text-muted/70 mt-1 leading-relaxed">
          This filter writes its value to <span className="font-mono text-muted">{varName || 'a variable'}</span>.
          To make a chart react: in that chart&apos;s <b>Param bindings</b> add a param bound to this variable, and use
          <span className="font-mono text-muted"> {'{{'}{varName || 'region'}{'}}'}</span> in its query SQL.
        </p>
        {varName && !knownVar && (
          <p className="text-[10px] text-warning mt-1">Tip: also add “{varName}” under Dashboard → Variables so it has a default.</p>
        )}
      </div>

      {isChoice && (
        <div>
          <FieldLabel>Options query</FieldLabel>
          <QueryPicker value={widget.options_query_id ?? ''} onChange={v => setField('options_query_id', v)} />
          <p className="text-[10px] text-muted/70 mt-1">A query returning the choices (first column = value, second = label).</p>
        </div>
      )}

      {/* ── Appearance & behaviour ── */}
      <Section title="Appearance & behaviour" defaultOpen={false}>
        <div>
          <FieldLabel>Size</FieldLabel>
          <div className="flex h-8 rounded-lg border border-border overflow-hidden">
            {['sm', 'md', 'lg'].map(s => (
              <button key={s} type="button" onClick={() => setProps('size', s === 'md' ? undefined : s)}
                className={`flex-1 text-[11px] font-medium uppercase transition-colors ${(props.size ?? 'md') === s ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary'}`}>
                {s}
              </button>
            ))}
          </div>
        </div>
        {isChoice && (
          <div>
            <FieldLabel>“All” label</FieldLabel>
            <input type="text" className={inputCls} placeholder="All" value={props.all_label ?? ''}
              onChange={e => setProps('all_label', e.target.value || undefined)} />
          </div>
        )}
        {subtype === 'list' && (
          <div>
            <FieldLabel>Active colour</FieldLabel>
            <ColorField
              value={props.accentColor}
              onChange={v => setProps('accentColor', v)}
              placeholder="theme primary"
              fallback="#2456a6"
            />
            <p className="text-[10px] text-muted/70 mt-1">Highlight colour for the selected row. Leave blank for the theme accent.</p>
          </div>
        )}
        {(subtype === 'select' || subtype === 'multiselect') && (
          <ToggleRow label="Searchable" hint="Show a search box in the dropdown"
            checked={props.searchable !== false} onChange={v => setProps('searchable', v ? undefined : false)} />
        )}
        {(subtype === 'select' || subtype === 'multiselect') && (
          <ToggleRow label="Clearable" hint="Show a ✕ to reset to “All”"
            checked={!!props.clearable} onChange={v => setProps('clearable', v || undefined)} />
        )}
        {subtype === 'multiselect' && (
          <ToggleRow label="“Select all” action" checked={props.select_all !== false}
            onChange={v => setProps('select_all', v ? undefined : false)} />
        )}
      </Section>
    </div>
  )
}

export function TextConfig({ widget, onChange }) {
  return (
    <div className="space-y-3">
      <PlacementControl widget={widget} onChange={onChange} />
      <div>
        <FieldLabel>Markdown content</FieldLabel>
        <textarea rows={8} className={`${inputCls} h-auto py-1.5 resize-y font-mono text-xs leading-relaxed`}
          value={widget.content ?? ''} onChange={e => onChange({ ...widget, content: e.target.value })}
          placeholder="# Heading&#10;&#10;Enter **markdown** here..." />
      </div>
      <p className="text-[10px] text-muted/70">Supports standard Markdown.</p>
    </div>
  )
}
