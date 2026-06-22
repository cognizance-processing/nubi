/**
 * shared/FilterConfig.jsx — Filter, Text, and Placement configuration panels.
 * Shared between DashboardEditor and CanvasEditor.
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

import { LayoutGrid } from 'lucide-react'
import { inputCls, selectCls, FieldLabel } from './inspectorPrimitives.jsx'
import { QueryPicker } from './QueryPicker.jsx'
import { FILTER_SUBTYPES } from './constants.js'
import { effectivePlacement, applyPlacement } from './placementHelpers.js'

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

export function FilterConfig({ widget, onChange }) {
  const setField = (key, val) => onChange({ ...widget, [key]: val })
  const props = widget.props ?? {}
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })
  return (
    <div className="space-y-3">
      <PlacementControl widget={widget} onChange={onChange} />
      <div>
        <FieldLabel>Label</FieldLabel>
        <input type="text" className={inputCls} value={props.label ?? ''} onChange={e => setProps('label', e.target.value)} />
      </div>
      <div>
        <FieldLabel>Subtype</FieldLabel>
        <select className={selectCls} value={widget.subtype ?? 'select'} onChange={e => setField('subtype', e.target.value)}>
          {FILTER_SUBTYPES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <div>
        <FieldLabel>Target variable</FieldLabel>
        <input type="text" placeholder="e.g. selected_region" className={inputCls}
          value={widget.target_var ?? ''} onChange={e => setField('target_var', e.target.value)} />
      </div>
      {(widget.subtype === 'select' || widget.subtype === 'multiselect') && (
        <div>
          <FieldLabel>Options query ID</FieldLabel>
          <QueryPicker value={widget.options_query_id ?? ''} onChange={v => setField('options_query_id', v)} />
        </div>
      )}
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
