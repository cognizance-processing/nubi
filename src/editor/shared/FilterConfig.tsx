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

import { useState } from 'react'
import { LayoutGrid, Link2, BarChart3, Table2, Gauge, Grid3x3, Layers, Check, Wand2, Loader2 } from 'lucide-react'
import { inputCls, selectCls, FieldLabel, ToggleRow, Section, ColorField, SectionLabel } from './inspectorPrimitives.jsx'
import { QueryPicker } from './QueryPicker.jsx'
import { FILTER_SUBTYPES } from './constants.js'
import { effectivePlacement, applyPlacement } from './placementHelpers.js'
import { useQueryParamsIndex, refreshQueryParamsIndex } from './useInspectorData.js'
import { titleText } from './titleValue.js'
import { listFilterableColumns, parameterizeQuery } from '../../lib/api.js'
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
 * "Its query takes no parameters" used to be the end of the road: the author
 * had to leave the editor, open the SQL, and hand-write a `{{param}}`
 * placeholder in the right subquery — which is not something most dashboard
 * authors can or should do. This turns that dead end into one click: pick the
 * column, and the server rewrites the query (injecting at the innermost scope
 * that exposes the column, so it filters before any roll-up) and verifies the
 * rewrite is inert when the filter is unset before saving it.
 */
function MakeFilterable({ queryId, varName, subtype, onDone, label = 'Add this filter to its query' }) {
  const [open, setOpen] = useState(false)
  const [columns, setColumns] = useState(null)   // null = not loaded yet
  const [column, setColumn] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const load = async () => {
    setOpen(true)
    if (columns !== null) return
    setBusy(true)
    const cols = await listFilterableColumns(queryId)
    setColumns(cols)
    // Pre-select a column matching the filter's own name when one exists —
    // the overwhelmingly common case (a "Region" filter on a `region` column).
    const match = cols.find(c => c.name.toLowerCase() === String(varName ?? '').toLowerCase())
    setColumn(match ? match.name : (cols[0]?.name ?? ''))
    setBusy(false)
  }

  const apply = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await parameterizeQuery(queryId, {
        param: varName, column, subtype: subtype ?? 'multiselect', apply: true,
      })
      if (!res?.ok || !res?.applied) {
        setError(res?.reason ?? 'The query could not be made filterable.')
        return
      }
      // The registry cached this query's (now stale) param list on mount.
      await refreshQueryParamsIndex()
      setOpen(false)
      onDone?.(varName)
    } catch (err) {
      setError(err?.message ?? 'The query could not be made filterable.')
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={load}
        data-testid={`make-filterable-${queryId}`}
        className="mt-1 ml-6 inline-flex items-center gap-1 text-[10px] font-medium text-muted hover:text-primary underline underline-offset-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded"
      >
        <Wand2 size={10} /> {label}
      </button>
    )
  }

  return (
    <div className="mt-1.5 ml-6 space-y-1.5">
      {busy && columns === null ? (
        <p className="text-[10px] text-muted flex items-center gap-1">
          <Loader2 size={10} className="animate-spin" /> Reading this query’s columns…
        </p>
      ) : (columns?.length ?? 0) === 0 ? (
        <p className="text-[10px] text-muted/70">
          No filterable columns found in this query.
        </p>
      ) : (
        <>
          <label className="block text-[10px] text-muted">Filter on which column?</label>
          <select
            className={selectCls}
            value={column}
            onChange={e => setColumn(e.target.value)}
            aria-label="Column to filter on"
          >
            {columns.map(c => (
              <option key={c.name} value={c.name}>
                {c.name}{c.in_output ? '' : ' (inside the query)'}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={apply}
              disabled={busy || !column}
              className="h-6 px-2 text-[10px] font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity inline-flex items-center gap-1"
            >
              {busy && <Loader2 size={9} className="animate-spin" />}
              {busy ? 'Checking…' : 'Add & connect'}
            </button>
            <button
              type="button"
              onClick={() => { setOpen(false); setError(null) }}
              className="h-6 px-2 text-[10px] text-muted hover:text-fg border border-border rounded-lg bg-surface hover:bg-surface-2 transition-colors"
            >
              Cancel
            </button>
          </div>
          <p className="text-[10px] text-muted/60 leading-relaxed">
            The query is re-run with the filter unset and must return exactly its
            current results before the change is saved.
          </p>
        </>
      )}
      {error && <p className="text-[10px] text-danger leading-relaxed">{error}</p>}
    </div>
  )
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
                <div className="pl-6 mt-1 space-y-1">
                  <p className="text-[10px] text-muted/60 leading-relaxed">
                    None of its parameters is called
                    <span className="font-mono text-muted"> {varName}</span>.
                  </p>
                  <select
                    className={selectCls}
                    defaultValue=""
                    aria-label={`Choose which parameter of ${row.label} this filter fills`}
                    onChange={e => connect(row, e.target.value)}>
                    {/* Naming the action rather than asking "Which parameter?"
                        matters: this list is every param the query happens to
                        declare, and on a real board those are usually
                        unrelated things (Period1, country_description). The
                        old wording invited binding a Region filter to a date
                        param, which type-checks and is nonsense. */}
                    <option value="" disabled>Reuse an existing parameter…</option>
                    {row.options.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              )}
              {blocked && (
                <p className="text-[10px] text-muted/60 mt-0.5 pl-6">
                  {loaded || row.state === 'no-param' ? blocked : 'reading parameters…'}
                </p>
              )}
              {/* Not having the RIGHT parameter is the same dead end as having
                  none at all — both used to mean "go hand-edit the SQL". Offer
                  to add it in either case. */}
              {(row.state === 'no-param' || row.state === 'choose') && row.queryId && (
                <MakeFilterable
                  queryId={row.queryId}
                  varName={varName}
                  subtype={widget.subtype ?? widget.props?.subtype}
                  label={row.state === 'choose'
                    ? `…or add a new “${varName}” filter to its query`
                    : 'Add this filter to its query'}
                  onDone={() => connect(row, varName)}
                />
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
