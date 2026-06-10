/**
 * inputs/ — accessible dashboard filter input primitives (Track F).
 *
 * Public surface:
 *   <Popover>          portal dropdown that escapes grid overflow-hidden clipping
 *   <Combobox>         searchable single-select
 *   <MultiSelect>      searchable multi-select (select-all / clear / invert /
 *                      include+exclude semantics, windowed, chips)
 *   <DateRangePicker>  date range with relative presets
 *   <OptionList>       windowed option row renderer (plain / radio / checkbox)
 *   useFilterOptions() options loader hook (static one-shot + debounced server
 *                      search modes) — shared by all subtypes
 *
 * Value-shape + preset helpers (pure, unit-testable) re-exported from helpers.js.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'

export { default as Popover } from './Popover.jsx'
export { default as Combobox } from './Combobox.jsx'
export { default as MultiSelect } from './MultiSelect.jsx'
export { default as DateRangePicker } from './DateRangePicker.jsx'
export { default as OptionList, VirtualList, ROW_HEIGHT } from './OptionList.jsx'

export {
  normMulti,
  isExclude,
  valuesOf,
  modeOf,
  makeMulti,
  toISODate,
  resolvePreset,
  resolveDateRange,
  DEFAULT_PRESETS,
  PRESET_LABELS,
} from './helpers.js'

// ---------------------------------------------------------------------------
// Option helpers
// ---------------------------------------------------------------------------

/** Normalise a raw option (string | {value,label,icon}) to { v, l, icon }. */
export function normOption(opt) {
  if (opt != null && typeof opt === 'object') {
    return {
      v: String(opt.value),
      l: opt.label != null ? String(opt.label) : String(opt.value),
      icon: opt.icon,
    }
  }
  const s = String(opt)
  return { v: s, l: s }
}

/** Map an Arrow table's first two columns to [{value, label}]. */
function tableToOptions(table) {
  if (!table || table.numRows === 0) return []
  const fields = table.schema.fields.map(f => f.name)
  const valueField = fields[0]
  const labelField = fields[1] ?? fields[0]
  const valueCol = table.getChild(valueField)
  const labelCol = table.getChild(labelField)
  const out = []
  for (let i = 0; i < table.numRows; i++) {
    const v = valueCol ? valueCol.get(i) : null
    const l = labelCol ? labelCol.get(i) : v
    if (v != null) out.push({ value: String(v), label: l != null ? String(l) : String(v) })
  }
  return out
}

// ---------------------------------------------------------------------------
// useFilterOptions — F3 query-backed options loader
// ---------------------------------------------------------------------------

/**
 * Loads filter options from a registered query.
 *
 *  - static mode (default): one-shot fetch of `options_query_id` on mount,
 *    matching the legacy FilterWidgetLoader behaviour.
 *  - search mode: re-runs the query (debounced) as the user types, binding the
 *    live search text via `options_params` markers `{ search: { input: true } }`.
 *    Selected values whose labels aren't on the current result page keep their
 *    labels via a small per-widget {value → label} cache (seeded deep links).
 *
 * @param {object} widget  spec Widget (reads props.options_query_id / search_query_id /
 *                         options_mode / searchable / options_params / debounce_ms)
 * @returns {{
 *   options: Array<{value,label}>,
 *   loading: boolean,
 *   mode: 'static' | 'search',
 *   onSearch: (q: string) => void,
 *   labelFor: (value: string) => string,
 *   capped: boolean,
 * }}
 */
export function useFilterOptions(widget) {
  const p = widget?.props ?? {}
  const optionsQueryId = widget?.options_query_id ?? p.options_query_id
  const searchQueryId = widget?.search_query_id ?? p.search_query_id
  // search mode is implied by options_mode:'search', a searchable flag + a search query.
  const mode = (p.options_mode === 'search' || (p.searchable && searchQueryId)) ? 'search' : 'static'
  const queryId = mode === 'search' ? (searchQueryId ?? optionsQueryId) : optionsQueryId
  const debounceMs = Number(p.debounce_ms) > 0 ? Number(p.debounce_ms) : 250
  const optionsParams = p.options_params

  const [options, setOptions] = useState([])
  const [loading, setLoading] = useState(false)
  const [capped, setCapped] = useState(false)
  const labelCache = useRef(new Map())   // value → label, survives result pages
  const timer = useRef(null)
  const reqSeq = useRef(0)

  // Build named params, binding the live search text into any { input: true } slots.
  const buildParams = useCallback((searchText) => {
    if (!optionsParams || typeof optionsParams !== 'object') {
      return searchText ? { search: searchText } : undefined
    }
    const out = {}
    for (const [k, v] of Object.entries(optionsParams)) {
      if (v && typeof v === 'object' && v.input === true) out[k] = searchText
      else out[k] = v
    }
    return out
  }, [optionsParams])

  const fetchOptions = useCallback(async (searchText) => {
    if (!queryId) return
    const seq = ++reqSeq.current
    setLoading(true)
    try {
      const namedParams = buildParams(searchText)
      const { table } = await runArrowQueryById(queryId, namedParams ? { namedParams } : undefined)
      if (seq !== reqSeq.current) return  // a newer request superseded this one
      const opts = tableToOptions(table)
      for (const o of opts) labelCache.current.set(o.value, o.label)
      setOptions(opts)
      // Heuristic "showing first N" footer when the result fills a typical cap.
      setCapped(opts.length >= 500)
    } catch (err) {
      if (seq === reqSeq.current) {
        console.warn('[inputs] options fetch failed:', err?.message)
      }
    } finally {
      if (seq === reqSeq.current) setLoading(false)
    }
  }, [queryId, buildParams])

  // Initial load (both modes do an unfiltered first fetch).
  useEffect(() => {
    if (!queryId) return
    fetchOptions('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryId])

  const onSearch = useCallback((q) => {
    if (mode !== 'search') return   // static mode filters client-side
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => fetchOptions(q), debounceMs)
  }, [mode, debounceMs, fetchOptions])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const labelFor = useCallback(
    (value) => labelCache.current.get(String(value)) ?? String(value),
    [],
  )

  return useMemo(
    () => ({ options, loading, mode, onSearch, labelFor, capped }),
    [options, loading, mode, onSearch, labelFor, capped],
  )
}
