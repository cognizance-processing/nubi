/**
 * FilterWidget.jsx — Spec-driven interactive filter widget.
 *
 * Props
 * -----
 * widget  {object}  A spec Widget object with type === 'filter'.
 *                   Shape:
 *                   {
 *                     id,
 *                     type: 'filter',
 *                     props: {
 *                       subtype:    'select' | 'multiselect' | 'daterange' | 'text',
 *                       target_var: string,           // variable name to write on change
 *                       label?:     string,
 *                       placeholder?: string,
 *                     }
 *                   }
 * options  {Array<{value: string|number, label?: string}>}
 *   Static options for select/multiselect.  In production this list will be
 *   populated from an options_query_id query result; for M14-B we accept it as
 *   a prop directly, defaulting to [].
 *
 * Behaviour
 * ---------
 * On every user interaction the widget calls useSetVariable()(target_var, newValue).
 * The VariableStore then propagates the new value to any data widget that refs
 * target_var in its params.
 *
 * Styling: matches the minimal Tailwind classes used by KpiWidget / SpecRenderer.
 */

import { useCallback, useId, useEffect, useMemo, useRef, useState } from 'react'
import { useSetVariable, useVariable } from '../VariableStore.jsx'

// ---------------------------------------------------------------------------
// Option helpers
// ---------------------------------------------------------------------------

/** Normalise a raw option (string | {value,label}) to { v, l }. */
function normOption(opt) {
  const v = typeof opt === 'object' ? String(opt.value) : String(opt)
  const l = typeof opt === 'object' ? (opt.label ?? v) : v
  return { v, l }
}

// ---------------------------------------------------------------------------
// VirtualList — windowed option list (no deps). Renders only visible rows so
// large option sets (10k+) stay smooth.
// ---------------------------------------------------------------------------

const ROW_HEIGHT = 32       // px per option row
const LIST_MAX_HEIGHT = 240 // px viewport for the dropdown list
const OVERSCAN = 4          // extra rows above/below the viewport

function VirtualList({ items, renderRow }) {
  const [scrollTop, setScrollTop] = useState(0)
  const total = items.length
  const viewportH = Math.min(LIST_MAX_HEIGHT, total * ROW_HEIGHT)

  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
  const visibleCount = Math.ceil(viewportH / ROW_HEIGHT) + OVERSCAN * 2
  const endIndex = Math.min(total, startIndex + visibleCount)
  const padTop = startIndex * ROW_HEIGHT
  const padBottom = (total - endIndex) * ROW_HEIGHT

  return (
    <div
      className="overflow-y-auto"
      style={{ height: viewportH }}
      onScroll={e => setScrollTop(e.currentTarget.scrollTop)}
      role="listbox"
    >
      <div style={{ height: padTop }} />
      {items.slice(startIndex, endIndex).map((item, i) => renderRow(item, startIndex + i))}
      <div style={{ height: padBottom }} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Searchable single-select combobox
// ---------------------------------------------------------------------------

function SelectFilter({ label, placeholder, options, value, onChange }) {
  const uid = useId()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef(null)

  const normed = useMemo(() => options.map(normOption), [options])
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return normed
    return normed.filter(o => o.l.toLowerCase().includes(q) || o.v.toLowerCase().includes(q))
  }, [normed, search])

  const selectedLabel = useMemo(() => {
    const sel = normed.find(o => o.v === String(value ?? ''))
    return sel?.l ?? ''
  }, [normed, value])

  useEffect(() => {
    if (!open) return
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div className="flex flex-col gap-1 h-full px-5 py-4">
      {label && (
        <label htmlFor={uid} className="text-xs font-semibold text-muted uppercase tracking-wider">
          {label}
        </label>
      )}
      <div className="relative" ref={ref}>
        <button
          id={uid}
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-haspopup="listbox"
          aria-expanded={open}
          className="w-full flex items-center justify-between gap-2 rounded-lg border border-border bg-surface text-fg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal/40 cursor-pointer"
        >
          <span className={selectedLabel ? 'truncate' : 'truncate text-muted'}>
            {selectedLabel || (placeholder ?? 'All')}
          </span>
          <span className="text-muted shrink-0">▾</span>
        </button>

        {open && (
          <div className="absolute left-0 right-0 top-full mt-1 z-30 rounded-lg border border-border bg-surface shadow-lg overflow-hidden">
            <div className="p-2 border-b border-border">
              <input
                type="text"
                autoFocus
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search…"
                className="w-full rounded-md border border-border bg-surface text-fg text-sm px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
              />
            </div>
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-xs text-muted italic">No matches</div>
            ) : (
              <VirtualList
                items={[{ v: '', l: placeholder ?? 'All' }, ...filtered]}
                renderRow={(o) => {
                  const active = String(value ?? '') === o.v
                  return (
                    <button
                      key={o.v || '__all__'}
                      type="button"
                      role="option"
                      aria-selected={active}
                      onClick={() => { onChange(o.v); setOpen(false); setSearch('') }}
                      style={{ height: ROW_HEIGHT }}
                      className={[
                        'w-full text-left px-3 text-sm truncate transition-colors',
                        active ? 'bg-brand-teal/10 text-brand-teal font-medium' : 'text-fg hover:bg-surface-2/70',
                      ].join(' ')}
                    >
                      {o.l}
                    </button>
                  )
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Searchable multi-select combobox
// ---------------------------------------------------------------------------

function MultiSelectFilter({ label, placeholder, options, value, onChange }) {
  const uid = useId()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef(null)
  const selected = Array.isArray(value) ? value.map(String) : []

  const normed = useMemo(() => options.map(normOption), [options])
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return normed
    return normed.filter(o => o.l.toLowerCase().includes(q) || o.v.toLowerCase().includes(q))
  }, [normed, search])

  function toggle(v) {
    if (selected.includes(v)) onChange(selected.filter(s => s !== v))
    else onChange([...selected, v])
  }

  useEffect(() => {
    if (!open) return
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const summary = selected.length === 0
    ? (placeholder ?? 'Select…')
    : `${selected.length} selected`

  return (
    <div className="flex flex-col gap-1 h-full px-5 py-4">
      {label && (
        <span className="text-xs font-semibold text-muted uppercase tracking-wider">{label}</span>
      )}
      <div className="relative" ref={ref}>
        <button
          id={uid}
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-haspopup="listbox"
          aria-expanded={open}
          className="w-full flex items-center justify-between gap-2 rounded-lg border border-border bg-surface text-fg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal/40 cursor-pointer"
        >
          <span className={selected.length ? 'truncate' : 'truncate text-muted'}>{summary}</span>
          <span className="flex items-center gap-1 shrink-0">
            {selected.length > 0 && (
              <span
                role="button"
                tabIndex={0}
                onClick={e => { e.stopPropagation(); onChange([]) }}
                className="text-[11px] text-muted hover:text-fg px-1"
                title="Clear selection"
              >
                clear
              </span>
            )}
            <span className="text-muted">▾</span>
          </span>
        </button>

        {open && (
          <div className="absolute left-0 right-0 top-full mt-1 z-30 rounded-lg border border-border bg-surface shadow-lg overflow-hidden">
            <div className="p-2 border-b border-border">
              <input
                type="text"
                autoFocus
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search…"
                className="w-full rounded-md border border-border bg-surface text-fg text-sm px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
              />
            </div>
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-xs text-muted italic">No matches</div>
            ) : (
              <VirtualList
                items={filtered}
                renderRow={(o) => {
                  const active = selected.includes(o.v)
                  return (
                    <button
                      key={o.v}
                      type="button"
                      role="option"
                      aria-selected={active}
                      onClick={() => toggle(o.v)}
                      style={{ height: ROW_HEIGHT }}
                      className="w-full flex items-center gap-2 text-left px-3 text-sm text-fg hover:bg-surface-2/70 transition-colors"
                    >
                      <span
                        className={[
                          'inline-flex items-center justify-center w-4 h-4 rounded border shrink-0 text-[10px]',
                          active ? 'bg-brand-teal border-brand-teal text-white' : 'border-border',
                        ].join(' ')}
                      >
                        {active ? '✓' : ''}
                      </span>
                      <span className="truncate">{o.l}</span>
                    </button>
                  )
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function DateRangeFilter({ label, value, onChange }) {
  // value: { from: string, to: string } | null
  const from = value?.from ?? ''
  const to   = value?.to   ?? ''

  function handleFrom(e) {
    onChange({ from: e.target.value, to })
  }
  function handleTo(e) {
    onChange({ from, to: e.target.value })
  }

  return (
    <div className="flex flex-col gap-1 h-full px-5 py-4">
      {label && (
        <span className="text-xs font-semibold text-muted uppercase tracking-wider">{label}</span>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="date"
          value={from}
          onChange={handleFrom}
          aria-label={label ? `${label} from` : 'From date'}
          className="flex-1 min-w-0 rounded-lg border border-border bg-surface text-fg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
        />
        <span className="text-muted text-xs">to</span>
        <input
          type="date"
          value={to}
          min={from || undefined}
          onChange={handleTo}
          aria-label={label ? `${label} to` : 'To date'}
          className="flex-1 min-w-0 rounded-lg border border-border bg-surface text-fg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
        />
      </div>
    </div>
  )
}

function TextFilter({ label, placeholder, value, onChange }) {
  const uid = useId()
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
        className="w-full rounded-lg border border-border bg-surface text-fg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
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
    subtype    = 'select',
    target_var = '',
    label,
    placeholder,
  } = wProps

  const setVariable = useSetVariable()

  // Read the current variable value from the store so URL-seeded values (and
  // any external initialValues) are reflected in the filter input on first render.
  // Called unconditionally (hooks rules); returns undefined when target_var is
  // empty or the variable has no value, which is handled below.
  const storeValue = useVariable(target_var)

  // Derive a sensible empty value for each subtype.
  function emptyForSubtype(st) {
    if (st === 'multiselect') return []
    if (st === 'daterange')   return { from: '', to: '' }
    return ''
  }

  // Local controlled state — seeded from the store value (URL param / default)
  // on first render.  The store is written on every user interaction so data
  // widgets re-query accordingly.
  const [localValue, setLocalValue] = useState(() => {
    if (storeValue !== undefined && storeValue !== null) return storeValue
    return emptyForSubtype(subtype)
  })

  // Keep localValue in sync when the store value changes externally (e.g. the
  // URL param changes programmatically or the board is navigated with a new URL).
  // This runs only when the store value changes, not on every render.
  useEffect(() => {
    if (storeValue !== undefined && storeValue !== null) {
      setLocalValue(storeValue)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeValue])

  const handleChange = useCallback((newValue) => {
    setLocalValue(newValue)
    if (target_var) {
      setVariable(target_var, newValue)
    }
  }, [target_var, setVariable])

  const sharedProps = {
    label,
    placeholder,
    options,
    value: localValue,
    onChange: handleChange,
  }

  return (
    <div className="flex flex-col justify-center h-full bg-surface rounded-xl border border-border">
      {subtype === 'select'      && <SelectFilter      {...sharedProps} />}
      {subtype === 'multiselect' && <MultiSelectFilter {...sharedProps} />}
      {subtype === 'daterange'   && <DateRangeFilter   {...sharedProps} />}
      {subtype === 'text'        && <TextFilter        {...sharedProps} />}
      {!['select','multiselect','daterange','text'].includes(subtype) && (
        <div className="flex items-center justify-center h-full px-5 py-4 text-sm text-muted">
          Unknown filter subtype: {subtype}
        </div>
      )}
    </div>
  )
}
