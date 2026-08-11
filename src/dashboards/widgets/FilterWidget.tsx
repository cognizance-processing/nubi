/**
 * FilterWidget.jsx — Spec-driven interactive filter widget.
 *
 * This is a thin dispatch layer over the accessible input PRIMITIVES in
 * `src/dashboards/inputs/` (Combobox, MultiSelect, DateRangePicker, OptionList,
 * Popover). The primitives render their dropdowns in a React portal so they
 * escape the grid cell's `overflow-hidden` clipping (the known bug).
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'filter'.
 *   {
 *     id,
 *     type: 'filter',
 *     drawer?: boolean,           // rendered in the filters drawer vs on-grid
 *     options_query_id?: string,  // static options source
 *     search_query_id?: string,   // server-search source (F3)
 *     props: {
 *       subtype:    'select' | 'multiselect' | 'daterange' | 'text',
 *       target_var: string,       // variable name to write on change
 *       label?:     string,
 *       placeholder?: string,
 *       all_label?: string,
 *       // behaviour ----------------------------------------------------------
 *       searchable?: boolean, clearable?: boolean, select_all?: boolean,
 *       exclude_toggle?: boolean, max_selected?: number,
 *       options_mode?: 'static' | 'search', debounce_ms?: number,
 *       options_params?: object,   // { search: { input: true } } marker
 *       presets?: string[],
 *       // appearance ----------------------------------------------------------
 *       size?: 'sm' | 'md' | 'lg', display?: 'chips' | 'count' | 'summary',
 *     }
 *   }
 *
 * options  {Array<{value, label}>}
 *   Static options (from options_query_id, fetched by SpecRenderer's loader).
 *   Backward compatible: defaults to []. In `search` mode the widget loads its
 *   own options via useFilterOptions and the prop is used only as a seed.
 *
 * Value shapes (backward compatible)
 * ----------------------------------
 *   multiselect:  ["a","b"]  (legacy, = include)  |  {mode:"exclude",values:[…]}
 *   daterange:    {from,to}  (legacy)             |  {preset:"last_30d"}
 *   select/text:  string
 *
 * Behaviour: on every interaction the widget calls
 * useSetVariable()(target_var, newValue); the VariableStore propagates it to any
 * data widget that refs target_var.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useFilterRefire, useSetVariable, useVariable } from '../VariableStore.jsx'
import {
  Combobox,
  MultiSelect,
  DateRangePicker,
  normOption,
  useFilterOptions,
} from '../inputs/index.js'

// ---------------------------------------------------------------------------
// Plain text filter (no dropdown — kept inline)
// ---------------------------------------------------------------------------

function TextFilter({ label, placeholder, value, onChange, size = 'md' }) {
  const uid = useId()
  const sizeCls = size === 'sm' ? 'text-xs px-2.5 py-1.5' : size === 'lg' ? 'text-base px-3.5 py-2.5' : 'text-sm px-3 py-2'
  const hasValue = !!value
  return (
    <div className="flex flex-col gap-1.5 h-full justify-center px-3 py-2 min-w-0">
      {label && (
        <label htmlFor={uid} className="text-[10.5px] font-semibold text-muted/80 uppercase tracking-[0.06em] leading-none">
          {label}
        </label>
      )}
      <input
        id={uid}
        type="text"
        value={value ?? ''}
        placeholder={placeholder ?? 'Filter…'}
        onChange={e => onChange(e.target.value)}
        className={[
          'w-full rounded-lg border bg-surface text-fg transition-all duration-150',
          sizeCls,
          'focus:outline-none focus:ring-2 focus:ring-ring/50',
          hasValue ? 'border-primary/60 font-medium' : 'border-border hover:border-ring/50',
        ].join(' ')}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// List slicer (vertical nav-style single-select — no dropdown, options inline)
// ---------------------------------------------------------------------------

function _rgba(hex, a) {
  if (typeof hex !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(hex)) return undefined
  const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${a})`
}

/**
 * ListSlicer — a vertical list of options rendered inline (Power-BI "list
 * slicer" / nav-rail look). Single-select; writes the chosen value (or '' for
 * the "All" row) to the target variable. Active row is highlighted with the
 * accent (props.accentColor, else the theme primary). No portal/dropdown — the
 * options are always visible, so it doubles as a sidebar nav.
 */
function ListSlicer({ label, options, value, onChange, allLabel = 'All', accent, showAll = true }) {
  const current = value == null ? '' : String(value)
  const rows = showAll ? [{ v: '', l: allLabel }, ...options] : options
  return (
    <div className="flex flex-col h-full px-2.5 py-2.5 gap-0.5 overflow-auto min-w-0">
      {label && (
        <div className="px-2 pb-1.5 text-[10.5px] font-semibold text-muted/80 uppercase tracking-[0.06em] leading-none">
          {label}
        </div>
      )}
      <div role="listbox" aria-label={label || 'Filter'} className="flex flex-col gap-0.5">
        {rows.map((o) => {
          const active = current === o.v
          return (
            <button
              key={o.v}
              type="button"
              role="option"
              aria-selected={active}
              onClick={() => onChange(o.v)}
              style={active && accent ? { backgroundColor: _rgba(accent, 0.16), color: accent } : undefined}
              className={[
                'flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-left transition-colors duration-150',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
                active
                  ? (accent ? 'font-semibold' : 'font-semibold bg-primary/15 text-primary')
                  : 'text-muted hover:text-fg hover:bg-surface-2/70',
              ].join(' ')}
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{
                  background: active ? (accent || 'var(--primary)') : 'transparent',
                  boxShadow: active && accent ? `0 0 8px ${accent}` : undefined,
                  border: active ? 'none' : '1px solid var(--border)',
                }}
                aria-hidden="true"
              />
              <span className="truncate">{o.l}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// FilterWidget
// ---------------------------------------------------------------------------

/**
 * @param {{ widget: object, options?: Array }} props
 */
export default function FilterWidget({ widget, options = [] }) {
  const { props: wProps = {} } = widget
  const {
    subtype     = 'select',
    target_var  = '',
    label,
    placeholder,
    all_label   = 'All',
    searchable  = true,
    clearable   = false,
    select_all  = true,
    exclude_toggle = true,
    max_selected,
    display     = 'chips',
    size        = 'md',
    presets,
    accentColor,
    show_all    = true,
  } = wProps

  const setVariable = useSetVariable()
  const storeValue = useVariable(target_var)

  // F3 — query-backed options. In `static` mode this stays empty and we use the
  // `options` prop supplied by SpecRenderer's loader. In `search` mode the hook
  // fetches (debounced) as the user types.
  const {
    options: queryOptions,
    loading: optionsLoading,
    mode: optionsMode,
    onSearch,
    labelFor,
  } = useFilterOptions(widget)

  // Cascading-filter refire (Finding 1 / §W4-G). When an UPSTREAM variable this
  // widget's options depend on changes (e.g. country → city), VariableStore bumps
  // this widget's stale epoch. We key on `widget.id`, the exact id the filter
  // graph / scheduleCascade marks stale (staleOptionWidgetIds → widget.id).
  const refireEpoch = useFilterRefire(widget.id)
  // Refetch the options when the epoch increments — but NOT on initial mount
  // (epoch 0), so widgets without upstream deps behave exactly as before. We
  // refetch via onSearch('') (the hook's debounced refetch entrypoint), which
  // re-runs the options query with the now-current upstream variable values.
  const onSearchRef = useRef(onSearch)
  useEffect(() => { onSearchRef.current = onSearch }, [onSearch])
  useEffect(() => {
    if (refireEpoch > 0) onSearchRef.current?.('')
    // Intentionally depend ONLY on refireEpoch so this fires once per cascade bump.
  }, [refireEpoch])

  // Choose the option source: search-mode results, else the static prop.
  const rawOptions = optionsMode === 'search' && queryOptions.length > 0 ? queryOptions : options
  const normed = useMemo(() => (rawOptions ?? []).map(normOption), [rawOptions])

  // In search mode, ensure already-selected values still have a row+label even
  // when they're not on the current result page (deep-link seeded).
  const optionsForList = useMemo(() => {
    if (optionsMode !== 'search') return normed
    const present = new Set(normed.map(o => o.v))
    const selectedValues = Array.isArray(storeValue)
      ? storeValue.map(String)
      : (storeValue && typeof storeValue === 'object' && Array.isArray(storeValue.values))
        ? storeValue.values.map(String)
        : (typeof storeValue === 'string' && storeValue ? [storeValue] : [])
    const extras = selectedValues
      .filter(v => !present.has(v))
      .map(v => ({ v, l: labelFor(v) }))
    return extras.length ? [...extras, ...normed] : normed
  }, [normed, optionsMode, storeValue, labelFor])

  function emptyForSubtype(st) {
    if (st === 'multiselect') return []
    if (st === 'daterange')   return { from: '', to: '' }
    return ''
  }

  const [localValue, setLocalValue] = useState(() => {
    if (storeValue !== undefined && storeValue !== null) return storeValue
    return emptyForSubtype(subtype)
  })

  useEffect(() => {
    if (storeValue !== undefined && storeValue !== null) {
      setLocalValue(storeValue)
    }
  }, [storeValue])

  const handleChange = useCallback((newValue) => {
    setLocalValue(newValue)
    if (target_var) setVariable(target_var, newValue)
  }, [target_var, setVariable])

  // CSS variables bridge per-widget style across the portal boundary (F1 caveat:
  // the portal escapes the widget subtree, so DOM inheritance won't reach it).
  // We forward any `--*` custom properties declared on widget.style.
  const styleVars = useMemo(() => {
    const s = widget.style && typeof widget.style === 'object' ? widget.style : null
    if (!s) return undefined
    const out = {}
    for (const [k, v] of Object.entries(s)) {
      if (k.startsWith('--')) out[k] = v
    }
    // A dark per-widget background makes the theme's muted label color
    // unreadable — derive a light label color so the label stays visible.
    const bg = s.background
    if (!out['--filter-label-color'] && typeof bg === 'string' && /^#[0-9a-fA-F]{6}$/.test(bg)) {
      const r = parseInt(bg.slice(1, 3), 16), g = parseInt(bg.slice(3, 5), 16), b = parseInt(bg.slice(5, 7), 16)
      const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
      if (lum < 0.45) out['--filter-label-color'] = 'rgba(255,255,255,0.78)'
    }
    return Object.keys(out).length ? out : undefined
  }, [widget.style])

  // widget.drawer: honoured by SpecRenderer for placement (drawer vs on-grid).
  // No behaviour change is needed beyond keeping the same render; the portal
  // works identically in both contexts.

  // The grid cell (SpecRenderer.renderItem) already draws the card surface +
  // border, so the widget itself stays transparent — otherwise stacked filters
  // read as heavy double-bordered boxes. Drawer placement is likewise borderless.
  const containerCls = 'flex flex-col justify-center h-full bg-transparent'

  return (
    <div className={containerCls}>
      {subtype === 'select' && (
        <Combobox
          label={label}
          placeholder={placeholder}
          allLabel={all_label}
          options={optionsForList}
          value={localValue}
          onChange={handleChange}
          clearable={clearable}
          searchable={searchable}
          size={size}
          styleVars={styleVars}
          onSearch={optionsMode === 'search' ? onSearch : undefined}
          loading={optionsLoading}
          typeToSearch={optionsMode === 'search'}
        />
      )}

      {subtype === 'multiselect' && (
        <MultiSelect
          label={label}
          placeholder={placeholder}
          options={optionsForList}
          value={localValue}
          onChange={handleChange}
          searchable={searchable}
          excludeToggle={exclude_toggle !== false}
          selectAll={select_all !== false}
          maxSelected={max_selected}
          display={display}
          size={size}
          styleVars={styleVars}
          onSearch={optionsMode === 'search' ? onSearch : undefined}
          loading={optionsLoading}
          typeToSearch={optionsMode === 'search'}
        />
      )}

      {subtype === 'daterange' && (
        <DateRangePicker
          label={label}
          value={localValue}
          onChange={handleChange}
          presets={presets}
          size={size}
          styleVars={styleVars}
        />
      )}

      {subtype === 'list' && (
        <ListSlicer
          label={label}
          options={optionsForList}
          value={localValue}
          onChange={handleChange}
          allLabel={all_label}
          accent={accentColor}
          showAll={show_all !== false}
        />
      )}

      {subtype === 'text' && (
        <TextFilter
          label={label}
          placeholder={placeholder}
          value={localValue}
          onChange={handleChange}
          size={size}
        />
      )}

      {!['select', 'multiselect', 'daterange', 'text', 'list'].includes(subtype) && (
        <div className="flex items-center justify-center h-full px-5 py-4 text-sm text-muted">
          Unknown filter subtype: {subtype}
        </div>
      )}
    </div>
  )
}
