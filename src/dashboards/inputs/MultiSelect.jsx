/**
 * MultiSelect.jsx — accessible searchable multi-select with power actions.
 *
 * Features (Track F2):
 *   - Searchable, windowed option list (portal popover → escapes grid clipping).
 *   - Header actions: Select all (acts on the *filtered* list), Clear, Invert.
 *   - Include / Exclude segmented toggle ("is any of" / "is not any of").
 *     Exclude is surfaced to data widgets as { mode:'exclude', values:[...] }.
 *   - Selected options pinned to the top of the list.
 *   - Trigger shows removable chips with "+N more" overflow (display: chips |
 *     count | summary).
 *
 * Value shape: see helpers.js (normMulti / makeMulti). A plain array still works
 * and means "include".
 */

import { useId, useMemo, useRef, useState } from 'react'
import Popover from './Popover.jsx'
import OptionList from './OptionList.jsx'
import { modeOf, valuesOf, makeMulti } from './helpers.js'

const SIZES = {
  sm: 'text-xs px-2.5 py-1.5',
  md: 'text-sm px-3 py-2',
  lg: 'text-base px-3.5 py-2.5',
}

const MAX_CHIPS = 3

/**
 * @param {{
 *   label?: string,
 *   placeholder?: string,
 *   options: Array<{v: string, l: string}>,
 *   value: unknown,                            // array | {mode,values}
 *   onChange: (next: unknown) => void,
 *   searchable?: boolean,
 *   excludeToggle?: boolean,
 *   selectAll?: boolean,
 *   maxSelected?: number,
 *   display?: 'chips' | 'count' | 'summary',
 *   size?: 'sm' | 'md' | 'lg',
 *   styleVars?: Record<string, string>,
 *   onSearch?: (q: string) => void,
 *   loading?: boolean,
 *   typeToSearch?: boolean,
 * }} props
 */
export default function MultiSelect({
  label,
  placeholder,
  options,
  value,
  onChange,
  searchable = true,
  excludeToggle = true,
  selectAll = true,
  maxSelected,
  display = 'chips',
  size = 'md',
  styleVars,
  onSearch,
  loading = false,
  typeToSearch = false,
}) {
  const uid = useId()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [active, setActive] = useState(-1)
  const triggerRef = useRef(null)

  const mode = modeOf(value)                 // 'all' | 'include' | 'exclude'
  const selected = useMemo(() => valuesOf(value), [value])
  const selectedSet = useMemo(() => new Set(selected), [selected])

  const filtered = useMemo(() => {
    if (onSearch) return options
    const q = search.trim().toLowerCase()
    if (!q) return options
    return options.filter(o => o.l.toLowerCase().includes(q) || o.v.toLowerCase().includes(q))
  }, [options, search, onSearch])

  // Pin selected options to the top of the (filtered) list.
  const rows = useMemo(() => {
    const sel = filtered.filter(o => selectedSet.has(o.v))
    const rest = filtered.filter(o => !selectedSet.has(o.v))
    return [...sel, ...rest]
  }, [filtered, selectedSet])

  // Effective mode for emitting values: 'all' collapses to 'include' once the
  // user starts picking. Exclude is preserved.
  const emitMode = mode === 'exclude' ? 'exclude' : 'include'

  function emit(values) {
    onChange(makeMulti(emitMode, values))
  }

  function toggle(v) {
    if (selectedSet.has(v)) {
      emit(selected.filter(s => s !== v))
    } else {
      if (maxSelected && selected.length >= maxSelected) return
      emit([...selected, v])
    }
  }

  function selectAllFiltered() {
    const next = new Set(selected)
    for (const o of filtered) next.add(o.v)
    emit([...next])
  }

  function clearAll() {
    emit([])
  }

  function invertFiltered() {
    // Invert membership across the *filtered* set, leaving others untouched.
    const next = new Set(selected)
    for (const o of filtered) {
      if (next.has(o.v)) next.delete(o.v)
      else next.add(o.v)
    }
    emit([...next])
  }

  function setMode(nextMode) {
    onChange(makeMulti(nextMode, selected))
  }

  function handleSearch(q) {
    setSearch(q)
    setActive(rows.length > 0 ? 0 : -1)
    onSearch?.(q)
  }

  function onTriggerKey(e) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      setOpen(true)
      setActive(0)
    }
  }

  function onPanelKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(rows.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(0, i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(rows.length - 1) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (active >= 0 && active < rows.length) toggle(rows[active].v)
    }
  }

  // ---- Trigger summary -----------------------------------------------------

  const selectedLabels = useMemo(
    () => selected.map(v => options.find(o => o.v === v)?.l ?? v),
    [selected, options],
  )

  function removeChip(e, idx) {
    e.stopPropagation()
    emit(selected.filter((_, i) => i !== idx))
  }

  const prefix = mode === 'exclude' ? 'Not ' : ''

  let triggerContent
  if (selected.length === 0) {
    triggerContent = <span className="truncate text-muted">{placeholder ?? 'Select…'}</span>
  } else if (display === 'count') {
    triggerContent = <span className="truncate">{prefix}{selected.length} selected</span>
  } else if (display === 'summary') {
    triggerContent = <span className="truncate">{prefix}{selectedLabels.join(', ')}</span>
  } else {
    // chips
    const shown = selectedLabels.slice(0, MAX_CHIPS)
    const extra = selectedLabels.length - shown.length
    triggerContent = (
      <span className="flex items-center gap-1 flex-wrap min-w-0">
        {mode === 'exclude' && (
          <span className="text-[10px] uppercase tracking-wide text-muted shrink-0">not</span>
        )}
        {shown.map((l, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 max-w-[10rem] rounded-md bg-surface-2 border border-border px-1.5 py-0.5 text-xs"
          >
            <span className="truncate">{l}</span>
            <span
              role="button"
              tabIndex={0}
              aria-label={`Remove ${l}`}
              onClick={e => removeChip(e, i)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') removeChip(e, i) }}
              className="text-muted hover:text-fg leading-none"
            >
              ✕
            </span>
          </span>
        ))}
        {extra > 0 && <span className="text-xs text-muted shrink-0">+{extra} more</span>}
      </span>
    )
  }

  const listboxId = `${uid}-listbox`

  return (
    <div className="flex flex-col gap-1 h-full px-5 py-4">
      {label && (
        <span className="text-xs font-semibold text-muted uppercase tracking-wider">{label}</span>
      )}
      <div className="relative">
        <button
          id={uid}
          ref={triggerRef}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listboxId : undefined}
          onClick={() => setOpen(o => !o)}
          onKeyDown={onTriggerKey}
          className={[
            'w-full flex items-center justify-between gap-2 rounded-lg border border-border bg-surface text-fg',
            SIZES[size] ?? SIZES.md,
            'focus:outline-none focus:ring-2 focus:ring-brand-teal/40 cursor-pointer',
          ].join(' ')}
        >
          <span className="min-w-0 flex-1 text-left">{triggerContent}</span>
          <span className="flex items-center gap-1 shrink-0">
            {selected.length > 0 && (
              <span
                role="button"
                tabIndex={0}
                aria-label="Clear selection"
                onClick={e => { e.stopPropagation(); clearAll() }}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); clearAll() } }}
                className="text-[11px] text-muted hover:text-fg px-1 rounded"
                title="Clear selection"
              >
                clear
              </span>
            )}
            <span aria-hidden="true" className="text-muted">▾</span>
          </span>
        </button>

        <Popover
          anchorRef={triggerRef}
          open={open}
          onClose={() => { setOpen(false); setSearch(''); setActive(-1) }}
          styleVars={styleVars}
          role="presentation"
        >
          <div onKeyDown={onPanelKey} className="flex flex-col min-h-0">
            {searchable && (
              <div className="p-2 border-b border-border">
                <input
                  type="text"
                  autoFocus
                  value={search}
                  role="searchbox"
                  aria-label="Search options"
                  aria-controls={listboxId}
                  onChange={e => handleSearch(e.target.value)}
                  placeholder="Search…"
                  className="w-full rounded-md border border-border bg-surface text-fg text-sm px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-teal/40"
                />
              </div>
            )}

            {excludeToggle && (
              <div className="flex items-center gap-1 px-2 pt-2" role="group" aria-label="Match mode">
                <button
                  type="button"
                  aria-pressed={mode !== 'exclude'}
                  onClick={() => setMode('include')}
                  className={[
                    'flex-1 rounded-md px-2 py-1 text-xs font-medium transition-colors',
                    mode !== 'exclude' ? 'bg-brand-teal/10 text-brand-teal' : 'text-muted hover:bg-surface-2/70',
                  ].join(' ')}
                >
                  is any of
                </button>
                <button
                  type="button"
                  aria-pressed={mode === 'exclude'}
                  onClick={() => setMode('exclude')}
                  className={[
                    'flex-1 rounded-md px-2 py-1 text-xs font-medium transition-colors',
                    mode === 'exclude' ? 'bg-brand-teal/10 text-brand-teal' : 'text-muted hover:bg-surface-2/70',
                  ].join(' ')}
                >
                  is not any of
                </button>
              </div>
            )}

            {selectAll && (
              <div className="flex items-center gap-3 px-3 py-1.5 border-b border-border text-xs">
                <button type="button" onClick={selectAllFiltered} className="text-brand-teal hover:underline">
                  Select all
                </button>
                <button type="button" onClick={clearAll} className="text-muted hover:text-fg">
                  Clear
                </button>
                <button type="button" onClick={invertFiltered} className="text-muted hover:text-fg">
                  Invert
                </button>
                <span className="ml-auto text-muted tabular-nums">{selected.length} selected</span>
              </div>
            )}

            <OptionList
              options={rows}
              selected={selectedSet}
              mode="checkbox"
              onSelect={toggle}
              activeIndex={active}
              onActiveChange={setActive}
              query={search}
              loading={loading}
              typeToSearch={typeToSearch && !!onSearch}
              listboxId={listboxId}
            />
          </div>
        </Popover>
      </div>
    </div>
  )
}
