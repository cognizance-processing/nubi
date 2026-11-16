/**
 * ConnectorCombobox — styled, searchable, keyboard-accessible picker for an
 * existing connector (datastore) instance.
 *
 * Replaces the plain `<select>` used to bind a query / blend source to a
 * connector (QueryWorkspace's primary-cell toolbar, BlendBuilder's per-source
 * connector field). Each row shows the connector's real brand logo, its name,
 * and a small dialect badge (e.g. `postgres`, `tsql`, `duckdb`) derived from
 * the shared catalog in src/data/connectors.js — itself a frontend mirror of
 * backend/app/connectors/dialects.py. Rows are grouped by connector category
 * (Relational / Cloud-managed SQL / Warehouses / Query engines / Lake & files
 * / APIs) and searchable by connector name or type.
 *
 * Contract is unchanged from the `<select>` it replaces: `value` is the
 * connector's `id` (or '' for "none selected"), `onChange(id)` fires with
 * the chosen connector's `id`. This component never invents or rewrites that
 * value — it only picks among `datastores` as given by the caller, so the
 * string ultimately sent to the backend (`datastore_id`) is unchanged.
 */

import { useId, useMemo, useRef, useState } from 'react'
import { ChevronDown, Search, Check } from 'lucide-react'
import Popover from '../../dashboards/inputs/Popover.jsx'
import ConnectorLogo from './ConnectorLogo.jsx'
import DialectBadge from './DialectBadge.jsx'
import { getTypeInfo, dialectFor, CONNECTOR_CATEGORIES } from '../../data/connectors.js'

function connectorType(ds) {
  return (ds?.config?.connector_type ?? ds?.config?.type ?? ds?.type ?? '').toString()
}

/**
 * @param {{
 *   datastores: Array<{id:string, name?:string, config?:{connector_type?:string}}>,
 *   value: string,
 *   onChange: (id: string) => void,
 *   placeholder?: string,
 *   allowEmpty?: boolean,
 *   emptyLabel?: string,
 *   id?: string,
 *   className?: string,
 *   triggerClassName?: string,
 * }} props
 */
export default function ConnectorCombobox({
  datastores = [],
  value,
  onChange,
  placeholder = 'Select a connector…',
  allowEmpty = false,
  emptyLabel = '— none —',
  id = undefined,
  className = '',
  triggerClassName = 'max-w-[220px]',
}) {
  const uid = useId()
  const domId = id ?? uid
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const triggerRef = useRef(null)

  const current = value ?? ''
  const selected = useMemo(() => datastores.find(d => d.id === current) ?? null, [datastores, current])
  const selectedInfo = selected ? getTypeInfo(connectorType(selected)) : null

  // ── Group + filter ─────────────────────────────────────────────────────
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const byCategory = new Map()
    for (const ds of datastores) {
      const info = getTypeInfo(connectorType(ds))
      const name = ds.name ?? ds.id
      if (q && !name.toLowerCase().includes(q) && !info.label.toLowerCase().includes(q)) continue
      const cat = info.category ?? 'other'
      if (!byCategory.has(cat)) byCategory.set(cat, [])
      byCategory.get(cat).push({ ds, info })
    }
    return CONNECTOR_CATEGORIES
      .map(cat => ({ ...cat, rows: byCategory.get(cat.id) ?? [] }))
      .filter(cat => cat.rows.length > 0)
  }, [datastores, query])

  // Flat index across groups, for keyboard nav.
  const flat = useMemo(() => groups.flatMap(g => g.rows), [groups])

  function commit(id) {
    onChange(id)
    setOpen(false)
    setQuery('')
    setActive(0)
    triggerRef.current?.focus()
  }

  function openMenu() {
    setOpen(true)
    const idx = flat.findIndex(r => r.ds.id === current)
    setActive(Math.max(0, idx))
  }

  function onTriggerKey(e) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      openMenu()
    }
  }

  function onPanelKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(flat.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(0, i - 1)) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(flat.length - 1) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (active >= 0 && active < flat.length) commit(flat[active].ds.id)
    }
  }

  const listboxId = `${domId}-listbox`
  const showSearch = datastores.length > 6

  return (
    <div className={`relative inline-block ${className}`}>
      <button
        id={domId}
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onTriggerKey}
        className={`
          flex items-center gap-1.5 h-7 rounded-md border border-border bg-surface
          pl-1.5 pr-2 text-xs text-fg cursor-pointer transition-colors
          hover:bg-surface-2 focus:outline-none focus:ring-1 focus:ring-ring
          ${triggerClassName}
        `}
        title="Run / bind this query against a connector."
      >
        {selected ? (
          <>
            <ConnectorLogo info={selectedInfo} size={13} />
            <span className="truncate">{selected.name ?? selected.id}</span>
            <DialectBadge dialect={dialectFor(connectorType(selected))} />
          </>
        ) : (
          <span className="truncate text-muted">{placeholder}</span>
        )}
        <ChevronDown size={12} className={`ml-auto shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      <Popover
        anchorRef={triggerRef}
        open={open}
        onClose={() => { setOpen(false); setQuery(''); setActive(0) }}
        matchWidth={false}
        maxHeight={360}
        ariaLabel="Select a connector"
      >
        <div className="w-72" onKeyDown={onPanelKey}>
          {showSearch && (
            <div className="p-2 border-b border-border">
              <div className="relative">
                <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
                <input
                  type="text"
                  autoFocus
                  value={query}
                  role="searchbox"
                  aria-label="Search connectors"
                  aria-controls={listboxId}
                  onChange={e => { setQuery(e.target.value); setActive(0) }}
                  placeholder="Search connectors…"
                  className="w-full rounded-md border border-border bg-surface text-fg text-xs pl-6 pr-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>
          )}

          <div role="listbox" id={listboxId} className="max-h-72 overflow-y-auto py-1">
            {allowEmpty && (
              <button
                type="button"
                role="option"
                aria-selected={current === ''}
                onClick={() => commit('')}
                onMouseEnter={() => setActive(-1)}
                className={`
                  w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs
                  transition-colors
                  ${current === '' ? 'text-primary font-medium' : 'text-muted hover:bg-surface-2'}
                `}
              >
                <span className="flex-1 truncate">{emptyLabel}</span>
                {current === '' && <Check size={12} className="shrink-0" />}
              </button>
            )}

            {flat.length === 0 && (
              <p className="px-3 py-3 text-xs text-muted italic">No connectors match “{query}”.</p>
            )}

            {groups.map(cat => (
              <div key={cat.id}>
                <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted/80">
                  {cat.label}
                </div>
                {cat.rows.map(({ ds, info }) => {
                  const idx = flat.findIndex(r => r.ds.id === ds.id)
                  const isSelected = ds.id === current
                  const isActive = idx === active
                  const dialect = dialectFor(connectorType(ds))
                  return (
                    <button
                      key={ds.id}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onMouseEnter={() => setActive(idx)}
                      onClick={() => commit(ds.id)}
                      className={`
                        w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs
                        transition-colors
                        ${isActive ? 'bg-surface-2' : 'hover:bg-surface-2/70'}
                        ${isSelected ? 'text-primary font-medium' : 'text-fg'}
                      `}
                    >
                      <ConnectorLogo info={info} size={14} />
                      <span className="flex-1 min-w-0 truncate">{ds.name ?? ds.id}</span>
                      <DialectBadge dialect={dialect} />
                      {isSelected && <Check size={12} className="shrink-0 text-primary" />}
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      </Popover>
    </div>
  )
}
