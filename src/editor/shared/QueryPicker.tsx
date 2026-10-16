/**
 * shared/QueryPicker.tsx — query binder for widget inspectors.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Used to be a `<select>` of hardcoded demo ids plus a free-text "Enter
 * query_id..." box — binding a widget to data meant copying a UUID out of
 * /queries and pasting it in. This is now a searchable browser (see
 * `QueryBrowser.jsx`) that surfaces the query's NAME, its connector, and (for
 * the row you're looking at) its output columns, with registry/built-in/
 * "not in env" state carried over from the /queries registry list. The raw-id
 * box still exists for power users, but it is demoted below the browser and
 * collapsed by default.
 *
 * Props (additive — this stays a drop-in for existing callers):
 *   value    string                                — current query_id
 *   onChange (id)=>void
 *   extraIds (string | {id, name})[]  — additional known queries to list
 *            (beyond the registry). A bare string is shown by its id; an
 *            {id, name} entry is shown by name (falling back to id).
 *   allowCreate  bool     — offer to BUILD a query here rather than only pick
 *            one. Off by default: a picker can only meaningfully generate a
 *            list-of-choices query, which is what a filter's options field
 *            wants and what a chart's data field does not.
 *   createSuggestion {table, column, schema}|null — seed for the builder.
 *   createDatastoreId string|null — connector to preselect in the builder.
 *   onCreated (row)=>void — the created query, for callers that want to say
 *            what happened. `onChange` is called with its id regardless.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { FileCode2, ChevronDown, AlertTriangle, Plus } from 'lucide-react'
import Badge from '../../components/ui/Badge.jsx'
import { inputCls } from './inspectorPrimitives.jsx'
import { QueryBrowser, useQueryRegistry } from './QueryBrowser.jsx'
import { OptionsQueryBuilder } from './OptionsQueryBuilder.jsx'

export function QueryPicker({
  value, onChange, extraIds = [],
  allowCreate = false, createSuggestion = null, createDatastoreId = null,
  onCreated = undefined,
}) {
  const [open, setOpen] = useState(false)
  const [rawOpen, setRawOpen] = useState(false)
  const [building, setBuilding] = useState(false)
  // Queries created from here during this session. The registry is read once on
  // mount, so without these a query built seconds ago is one the picker has
  // never heard of: it renders as a bare UUID and trips the "unresolved value"
  // branch that force-opens the raw-id box. Feeding them back through
  // `extraIds` names them immediately and needs no refetch.
  const [justCreated, setJustCreated] = useState([])
  const containerRef = useRef(null)
  const triggerRef = useRef(null)

  const knownIds = useMemo(
    () => [...extraIds, ...justCreated],
    [extraIds, justCreated],
  )
  const { entries, loading, strictEnv } = useQueryRegistry(knownIds, value)
  const currentRow = entries.find(e => e.id === value)
  // Unresolved: a value is set but nothing (registry, extraIds, or the demo
  // list) recognises it. Auto-reveal the raw-id box so the binding is still
  // visible/editable instead of looking silently blank.
  const unresolved = Boolean(value) && currentRow && !currentRow.known && !loading

  useEffect(() => { if (unresolved) setRawOpen(true) }, [unresolved])

  useEffect(() => {
    if (!open) return
    const onDocDown = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocDown)
    return () => document.removeEventListener('mousedown', onDocDown)
  }, [open])

  const handleSelect = (id) => {
    onChange(id)
    setOpen(false)
    triggerRef.current?.focus()
  }

  const startBuilding = () => {
    setOpen(false)
    setBuilding(true)
  }

  const handleCreated = (row) => {
    setBuilding(false)
    setJustCreated(prev => (
      prev.some(r => r.id === row.id) ? prev : [...prev, { id: row.id, name: row.name }]
    ))
    onChange(row.id)
    onCreated?.(row)
    triggerRef.current?.focus()
  }

  const handleTriggerKeyDown = (e) => {
    if (e.key === 'ArrowDown' && !open) {
      e.preventDefault()
      setOpen(true)
    }
  }

  const handlePopoverKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
  }

  return (
    <div className="space-y-1" ref={containerRef}>
      <div className="relative">
        <button
          ref={triggerRef}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-label="Query"
          onClick={() => setOpen(o => !o)}
          onKeyDown={handleTriggerKeyDown}
          className="w-full min-h-8 flex items-center justify-between gap-2 text-left border border-border rounded-lg px-2.5 py-1.5 bg-surface hover:border-border/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus:border-ring/40 transition-all duration-150"
        >
          <span className="min-w-0 flex-1">
            {value ? (
              <>
                <span className="flex flex-wrap items-center gap-1 min-w-0">
                  <FileCode2 size={12} className="shrink-0 text-muted" />
                  <span className="text-xs font-medium text-fg truncate max-w-full">
                    {currentRow?.name || value}
                  </span>
                  {currentRow?.builtin && <Badge size="sm" variant="info">Built-in</Badge>}
                  {currentRow?.notInActiveEnv && (
                    <Badge size="sm" variant="warning">
                      <AlertTriangle size={8} />
                      not in {strictEnv}
                    </Badge>
                  )}
                </span>
                {(currentRow?.name || currentRow?.connectorName) && (
                  <span className="flex items-center gap-1.5 mt-0.5 pl-[18px]">
                    <span className="text-[10px] font-mono text-muted/70 truncate">{value}</span>
                    {currentRow?.connectorName && (
                      <span className="text-[10px] text-muted/60 truncate shrink-0">· {currentRow.connectorName}</span>
                    )}
                  </span>
                )}
              </>
            ) : (
              <span className="text-xs text-muted/60">Select a query…</span>
            )}
          </span>
          <ChevronDown size={13} className={`shrink-0 text-muted transition-transform duration-150 ${open ? 'rotate-180' : ''}`} />
        </button>

        {open && (
          <div
            className="nubi-dropdown absolute left-0 right-0 top-[calc(100%+6px)] w-full max-w-none min-w-0 p-0 overflow-hidden"
            onKeyDown={handlePopoverKeyDown}
          >
            <QueryBrowser
              entries={entries}
              loading={loading}
              value={value}
              strictEnv={strictEnv}
              onSelect={handleSelect}
              listId="query-picker-list"
            />
          </div>
        )}
      </div>

      {/* Build one instead of hunting for one that may not exist. */}
      {allowCreate && !building && (
        <div>
          <button
            type="button"
            onClick={startBuilding}
            data-testid="query-picker-create"
            className="text-[10px] text-accent hover:text-fg transition-colors focus-visible:outline-none focus-visible:underline inline-flex items-center gap-1"
          >
            <Plus size={10} /> Build one from a column
          </button>
        </div>
      )}
      {allowCreate && building && (
        <OptionsQueryBuilder
          datastoreId={createDatastoreId}
          suggestion={createSuggestion}
          onCreated={handleCreated}
          onCancel={() => setBuilding(false)}
        />
      )}

      {/* Raw-id fallback — demoted: collapsed unless the caller opts in, or
          the current value doesn't resolve to anything the browser knows. */}
      {!rawOpen ? (
        <div>
          <button
            type="button"
            onClick={() => setRawOpen(true)}
            className="text-[10px] text-muted/60 hover:text-muted transition-colors focus-visible:outline-none focus-visible:underline"
          >
            Enter a raw query ID instead
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5">
          <input
            type="text"
            aria-label="Raw query ID"
            placeholder="Enter query_id…"
            className={inputCls}
            value={value}
            onChange={e => onChange(e.target.value)}
          />
          <button
            type="button"
            onClick={() => setRawOpen(false)}
            className="shrink-0 text-[10px] text-muted/60 hover:text-fg transition-colors focus-visible:outline-none focus-visible:underline"
          >
            Hide
          </button>
        </div>
      )}
    </div>
  )
}
