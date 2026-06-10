/**
 * Combobox.jsx — accessible searchable single-select.
 *
 * Trigger button + portal popover (escapes grid clipping) containing a search
 * input and a windowed OptionList. Full keyboard support: Up/Down/Home/End to
 * move the cursor, Enter to select, Escape to close, type-ahead via the search
 * box. ARIA: role="combobox" on the trigger, listbox/option on the panel.
 *
 * Value: a single string (or '' / null for "all"). Backward compatible with the
 * legacy SelectFilter contract.
 */

import { useId, useMemo, useRef, useState } from 'react'
import Popover from './Popover.jsx'
import OptionList from './OptionList.jsx'

const SIZES = {
  sm: 'text-xs px-2.5 py-1.5',
  md: 'text-sm px-3 py-2',
  lg: 'text-base px-3.5 py-2.5',
}

/**
 * @param {{
 *   label?: string,
 *   placeholder?: string,
 *   allLabel?: string,
 *   options: Array<{v: string, l: string}>,    // pre-normalised
 *   value: string | number | null,
 *   onChange: (v: string) => void,
 *   clearable?: boolean,
 *   searchable?: boolean,
 *   size?: 'sm' | 'md' | 'lg',
 *   styleVars?: Record<string, string>,
 *   onSearch?: (q: string) => void,            // server-search hook (F3)
 *   loading?: boolean,
 *   typeToSearch?: boolean,
 * }} props
 */
export default function Combobox({
  label,
  placeholder,
  allLabel = 'All',
  options,
  value,
  onChange,
  clearable = false,
  searchable = true,
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

  const current = value == null ? '' : String(value)

  // Local filtering only when not server-driven.
  const filtered = useMemo(() => {
    if (onSearch) return options
    const q = search.trim().toLowerCase()
    if (!q) return options
    return options.filter(o => o.l.toLowerCase().includes(q) || o.v.toLowerCase().includes(q))
  }, [options, search, onSearch])

  // "All" sentinel row prepended so users can clear the selection from the list.
  const rows = useMemo(
    () => [{ v: '', l: allLabel }, ...filtered],
    [filtered, allLabel],
  )

  const selectedLabel = useMemo(() => {
    if (!current) return ''
    const sel = options.find(o => o.v === current)
    return sel?.l ?? current
  }, [options, current])

  const selectedSet = useMemo(() => new Set([current]), [current])

  function commit(v) {
    onChange(v)
    setOpen(false)
    setSearch('')
    setActive(-1)
    triggerRef.current?.focus()
  }

  function openMenu() {
    setOpen(true)
    setActive(Math.max(0, rows.findIndex(o => o.v === current)))
  }

  function onTriggerKey(e) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      openMenu()
    }
  }

  function onPanelKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(rows.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(0, i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(rows.length - 1) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (active >= 0 && active < rows.length) commit(rows[active].v)
    }
  }

  function handleSearch(q) {
    setSearch(q)
    setActive(rows.length > 0 ? 0 : -1)
    onSearch?.(q)
  }

  const listboxId = `${uid}-listbox`

  return (
    <div className="flex flex-col gap-1 h-full px-5 py-4">
      {label && (
        <label htmlFor={uid} className="text-xs font-semibold text-muted uppercase tracking-wider">
          {label}
        </label>
      )}
      <div className="relative">
        <button
          id={uid}
          ref={triggerRef}
          type="button"
          role="combobox"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listboxId : undefined}
          onClick={() => (open ? setOpen(false) : openMenu())}
          onKeyDown={onTriggerKey}
          className={[
            'w-full flex items-center justify-between gap-2 rounded-lg border border-border bg-surface text-fg',
            SIZES[size] ?? SIZES.md,
            'focus:outline-none focus:ring-2 focus:ring-brand-teal/40 cursor-pointer',
          ].join(' ')}
        >
          <span className={selectedLabel ? 'truncate' : 'truncate text-muted'}>
            {selectedLabel || (placeholder ?? allLabel)}
          </span>
          <span className="flex items-center gap-1 shrink-0">
            {clearable && current && (
              <span
                role="button"
                tabIndex={0}
                aria-label="Clear"
                onClick={e => { e.stopPropagation(); onChange('') }}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); onChange('') } }}
                className="text-[11px] text-muted hover:text-fg px-1 rounded"
              >
                ✕
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
          <div onKeyDown={onPanelKey}>
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
            <OptionList
              options={rows}
              selected={selectedSet}
              mode="plain"
              onSelect={commit}
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
