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

import { LayoutGrid, Link2, BarChart3, Table2, Gauge, Grid3x3, Layers, Check } from 'lucide-react'
import { inputCls, selectCls, FieldLabel, ToggleRow, Section, ColorField, SectionLabel } from './inspectorPrimitives.jsx'
import { QueryPicker } from './QueryPicker.jsx'
import { FILTER_SUBTYPES } from './constants.js'
import { effectivePlacement, applyPlacement } from './placementHelpers.js'
import { useQueryParamsIndex } from './useInspectorData.js'
import { titleText } from './titleValue.js'
import { wiringRows, connectedCount, bindParam, unbindVar, varNameFromLabel } from '../../dashboards/paramWiring.js'

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

// ---------------------------------------------------------------------------
// ConnectedWidgets — "what does this filter actually control?"
// ---------------------------------------------------------------------------

const TYPE_ICON = {
  chart: BarChart3, table: Table2, kpi: Gauge, metric: Gauge,
  pivot: Grid3x3, stepper: Layers,
}

/** Why a widget can't be connected, in words the author can act on. */
const BLOCKED_REASON = {
  'no-param': 'its query takes no parameters',
  unknown: 'no query bound yet',
}

/**
 * The list of widgets this filter drives, with a switch on each one.
 *
 * This replaces the instructions that used to live here ("go to that chart's
 * param bindings and add…"). Everything it needs is already known: the board
 * spec lists the widgets, the registry lists each query's declared params, and
 * a name match tells us which param a variable belongs in. Ticking a row writes
 * that binding onto the other widget; unticking removes it.
 */
function ConnectedWidgets({ widget, spec, varName, onPatchWidget, resolveWidget }) {
  const { paramsById, loaded } = useQueryParamsIndex()

  if (!varName) {
    return (
      <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 leading-relaxed">
        Name this filter’s variable above and the widgets it can control appear here.
      </p>
    )
  }

  const rows = wiringRows({
    widgets: (spec?.widgets ?? []).filter(w => w.id !== widget.id),
    varName,
    paramsByQueryId: paramsById,
    resolve: resolveWidget,
    // Name each row the way the widget names itself on the canvas — the same
    // fallback chain the library-save dialog uses. A list of widget ids is not
    // something anyone can pick their chart out of.
    labelFor: w => titleText(w.config?.title) || titleText(w.props?.label)
      || titleText(w.title) || `Untitled ${w.type}`,
  })

  if (rows.length === 0) {
    return (
      <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center">
        No charts, tables or KPIs on this board yet.
      </p>
    )
  }

  const connected = connectedCount(rows)
  const connectable = rows.filter(r => r.state === 'available')

  const connect = (row, paramName) => {
    if (!paramName) return
    onPatchWidget(row.id, w => ({ ...w, params: bindParam(w.params, paramName, varName) }))
  }
  const disconnect = (row) => {
    onPatchWidget(row.id, w => ({ ...w, params: unbindVar(w.params, varName) }))
  }

  return (
    <div className="space-y-2" data-testid="filter-connections">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted">
          <span className="font-medium text-fg">{connected}</span> of {rows.length} connected
        </span>
        {connectable.length > 0 && (
          <button
            data-testid="filter-connect-all"
            onClick={() => connectable.forEach(r => connect(r, r.paramName))}
            className="text-[11px] font-medium px-2 h-6 rounded-lg border border-dashed border-border hover:border-primary text-muted hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
            Connect {connectable.length === 1 ? 'the other one' : `all ${connectable.length}`}
          </button>
        )}
      </div>

      <ul className="space-y-1">
        {rows.map(row => {
          const Icon = TYPE_ICON[row.type] ?? BarChart3
          const isOn = row.state === 'connected'
          const canToggle = isOn || row.state === 'available'
          const blocked = BLOCKED_REASON[row.state]
          return (
            <li key={row.id}
              data-testid={`filter-connection-${row.id}`}
              className={`rounded-lg border px-2 py-1.5 transition-colors ${
                isOn ? 'border-primary/40 bg-primary/5' : 'border-border bg-surface'
              }`}>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  role="switch"
                  aria-checked={isOn}
                  aria-label={`${isOn ? 'Disconnect' : 'Connect'} ${row.label}`}
                  disabled={!canToggle}
                  onClick={() => (isOn ? disconnect(row) : connect(row, row.paramName))}
                  className={`w-4 h-4 flex-none rounded border flex items-center justify-center transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50 ${
                    isOn ? 'bg-primary border-primary text-primary-fg'
                    : canToggle ? 'border-border hover:border-primary bg-surface'
                    : 'border-border/60 bg-surface-2 cursor-not-allowed'
                  }`}>
                  {isOn && <Check size={11} strokeWidth={3} />}
                </button>
                <Icon size={12} className={`flex-none ${isOn ? 'text-primary' : 'text-muted/70'}`} />
                <span className={`flex-1 text-xs truncate ${canToggle ? 'text-fg' : 'text-muted/70'}`} title={row.label}>
                  {row.label}
                </span>
              </div>

              {isOn && (
                <p className="text-[10px] text-muted/80 mt-0.5 pl-6 font-mono truncate">→ {row.paramName}</p>
              )}
              {row.state === 'available' && (
                <p className="text-[10px] text-muted/60 mt-0.5 pl-6 font-mono truncate">{row.paramName}</p>
              )}
              {row.state === 'choose' && (
                <div className="pl-6 mt-1">
                  <select
                    className={selectCls}
                    defaultValue=""
                    aria-label={`Choose which parameter of ${row.label} this filter fills`}
                    onChange={e => connect(row, e.target.value)}>
                    <option value="" disabled>Which parameter?</option>
                    {row.options.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              )}
              {blocked && (
                <p className="text-[10px] text-muted/60 mt-0.5 pl-6">
                  {loaded || row.state === 'no-param' ? blocked : 'reading parameters…'}
                </p>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function FilterConfig({ widget, onChange, spec, onPatchWidget = undefined, onCreateVariable = undefined, resolveWidget = undefined }) {
  const setField = (key, val) => onChange({ ...widget, [key]: val })
  const props = widget.props ?? {}
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })
  const subtype = widget.subtype ?? 'select'
  const isChoice = subtype === 'select' || subtype === 'multiselect' || subtype === 'list'
  const varNames = (spec?.variables ?? []).map(v => v.name).filter(Boolean)
  const varName = widget.target_var ?? ''
  const knownVar = !varName || varNames.includes(varName)

  // The variable follows the label — "Store group" writes `store_group` — until
  // someone edits the name directly, at which point it stops following. That is
  // detected by comparing against what the PREVIOUS label would have produced,
  // so a hand-picked name is never overwritten by a later label edit.
  const setLabel = (label) => {
    const next: Record<string, any> = { ...widget, props: { ...props, label } }
    const followed = !varName || varName === varNameFromLabel(props.label ?? '')
    if (followed) {
      const derived = varNameFromLabel(label)
      if (derived) next.target_var = derived
    }
    onChange(next)
  }

  return (
    <div className="space-y-3">
      <PlacementControl widget={widget} onChange={onChange} />
      <div>
        <FieldLabel>Label</FieldLabel>
        <input type="text" className={inputCls} value={props.label ?? ''} placeholder="e.g. Region"
          data-testid="filter-label"
          onChange={e => setLabel(e.target.value)}
          onBlur={() => { const v = (widget.target_var ?? '').trim(); if (v && !varNames.includes(v)) onCreateVariable?.(v) }} />
      </div>
      <div>
        <FieldLabel>Type</FieldLabel>
        <select className={selectCls} value={subtype} onChange={e => setField('subtype', e.target.value)}>
          {FILTER_SUBTYPES.map(s => <option key={s} value={s}>{SUBTYPE_LABELS[s] ?? s}</option>)}
        </select>
      </div>

      {/* ── What this filter controls ────────────────────────────────────── */}
      <div className="space-y-1.5">
        <FieldLabel className="flex items-center gap-1.5"><Link2 size={12} /> Controls these widgets</FieldLabel>
        <ConnectedWidgets
          widget={widget}
          spec={spec}
          varName={varName}
          onPatchWidget={onPatchWidget ?? (() => {})}
          resolveWidget={resolveWidget}
        />
        <p className="text-[10px] text-muted/70 leading-relaxed">
          A widget can be connected once its query declares a matching
          <span className="font-mono text-muted"> {'{{'}{varName || 'name'}{'}}'} </span>
          parameter.
        </p>
      </div>

      {/* ── Variable name (advanced — derived from the label by default) ──── */}
      <Section title="Variable name" defaultOpen={!varName || !knownVar}>
        <input type="text" list="filter-var-list" placeholder="e.g. region" className={inputCls}
          data-testid="filter-target-var"
          value={varName}
          onChange={e => setField('target_var', e.target.value)}
          onBlur={e => { const v = e.target.value.trim(); if (v && !varNames.includes(v)) onCreateVariable?.(v) }} />
        {varNames.length > 0 && (
          <datalist id="filter-var-list">
            {varNames.map(v => <option key={v} value={v} />)}
          </datalist>
        )}
        <p className="text-[10px] text-muted/70 mt-1 leading-relaxed">
          The name this filter’s value travels under — written into queries as
          <span className="font-mono text-muted"> {'{{'}{varName || 'region'}{'}}'}</span>, and into a
          shareable link as <span className="font-mono text-muted">?{varName || 'region'}=…</span>.
          It follows the label unless you change it here.
        </p>
      </Section>

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
