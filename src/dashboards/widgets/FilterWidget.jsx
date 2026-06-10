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
  return (
    <div className="flex flex-col gap-1 h-full px-5 py-4">
      {label && (
        <label htmlFor={uid} className="text-xs font-semibold text-muted uppercase tracking-wider">
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
          'w-full rounded-lg border border-border bg-surface text-fg',
          sizeCls,
          'focus:outline-none focus:ring-2 focus:ring-brand-teal/40',
        ].join(' ')}
      />
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    return Object.keys(out).length ? out : undefined
  }, [widget.style])

  // widget.drawer: honoured by SpecRenderer for placement (drawer vs on-grid).
  // No behaviour change is needed beyond keeping the same render; the portal
  // works identically in both contexts.

  const containerCls = widget.drawer
    ? 'flex flex-col justify-start h-full bg-transparent'
    : 'flex flex-col justify-center h-full bg-surface rounded-xl border border-border'

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

      {subtype === 'text' && (
        <TextFilter
          label={label}
          placeholder={placeholder}
          value={localValue}
          onChange={handleChange}
          size={size}
        />
      )}

      {!['select', 'multiselect', 'daterange', 'text'].includes(subtype) && (
        <div className="flex items-center justify-center h-full px-5 py-4 text-sm text-muted">
          Unknown filter subtype: {subtype}
        </div>
      )}
    </div>
  )
}
