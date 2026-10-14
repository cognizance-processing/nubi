/**
 * BreadcrumbWidget.jsx — a drill-path trail for boards that filter downwards.
 *
 * widget shape (type === 'breadcrumb'):
 *   {
 *     id, type: 'breadcrumb',
 *     props: {
 *       variables: string[],   // tracked variables, OUTERMOST DRILL FIRST
 *       labels?: Record<string, string>,  // per-variable display name
 *       homeLabel?: string,    // default 'All'
 *       maxSteps?: number,     // default 10
 *       separator?: 'arrow' | 'slash' | 'dot',   // default 'arrow'
 *       chipColor?: string,    // default var(--primary)
 *       chipTextColor?: string, // chip text, both variants (default: fg / chipColor)
 *       chipVariant?: 'filled' | 'outlined',     // default 'filled'
 *       size?: 'sm' | 'md',    // default 'sm'
 *       contextColumns?: string[],  // see "Context chips" below
 *     },
 *     query_id?, params?,      // backs the context chips
 *     pos
 *   }
 *
 * Behaviour
 * ---------
 * Every time one of the tracked variables changes to a new non-empty value, a
 * step is appended carrying a SNAPSHOT of all tracked variables at that moment.
 * Clicking a step restores its snapshot and truncates the trail after it;
 * clicking Home clears every tracked variable and empties the trail.
 *
 * The legacy widget this replaces tracked *widget ids* because filters wrote
 * their output into a per-widget slot. Nubi filters write named variables, so
 * the trail tracks variables — the same drill path, addressed the way this
 * platform addresses it, and it works for a variable set by a chart click or
 * a table click-to-filter just as well as one set by a dropdown.
 *
 * Context chips: a drill path usually starts from a scope the reader should
 * see before they drill at all ("We are currently in week 3 › South Africa").
 * When the widget has a query, each of props.contextColumns is read off its
 * first row and shown as a fixed chip between Home and the drill steps. They
 * re-query with the widget's params, so a scope that is itself a variable
 * stays current. A label of '' for a tracked variable shows the bare value.
 *
 * The trail is deliberately in-memory: a drill path describes a viewing
 * session, so a reload starts at Home rather than resuming a stale path whose
 * underlying data may have moved on.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Home } from 'lucide-react'
import { useResolvedParams, useSetVariable, useVariableValues } from '../VariableStore.jsx'
import { runArrowQueryById } from '../../lib/wasmRuntime.js'
import { chipLabel, isEmptyValue } from '../legacyWidgetText.js'
import { resolveTextColors } from '../textColors.js'
import EmptyState from '../../components/ui/EmptyState.jsx'

const SEPARATORS = { slash: '/', dot: '•' }

export default function BreadcrumbWidget({ widget, editMode = false }) {
  const props = widget.props ?? {}
  // Keyed on the serialised list, not the array identity: the spec object is
  // rebuilt on every editor keystroke, so an identity dep would hand the change
  // watcher a fresh `tracked` array each render and re-run it constantly.
  const trackedKey = JSON.stringify(props.variables ?? null)
  const tracked = useMemo(
    () => {
      const raw = JSON.parse(trackedKey)
      return Array.isArray(raw) ? raw.filter(Boolean) : []
    },
    [trackedKey],
  )
  const labels = props.labels ?? {}
  const homeLabel = props.homeLabel ?? 'All'
  const maxSteps = Number.isInteger(props.maxSteps) && props.maxSteps > 0 ? props.maxSteps : 10
  // The chips carry their own colour; the separators between them are the
  // `muted` text role, so they follow the card's Appearance settings.
  const ink = resolveTextColors(widget.style)
  const chipColor = props.chipColor || ink.title || 'var(--primary)'
  // An explicit chip text colour holds for both variants (a pale chip colour
  // needs dark text either way); unset, each variant keeps its own default.
  const chipTextColor = props.chipTextColor || null
  const filled = props.chipVariant !== 'outlined'
  const separator = SEPARATORS[props.separator] ?? null
  const chipSize = props.size === 'md'
    ? 'h-8 px-3.5 text-[13px] gap-1.5'
    : 'h-7 px-2.5 text-[11px] gap-1'

  const contextKey = JSON.stringify(props.contextColumns ?? null)
  const contextColumns = useMemo(() => {
    const raw = JSON.parse(contextKey)
    return Array.isArray(raw) ? raw.filter(Boolean) : []
  }, [contextKey])
  const resolvedParams = useResolvedParams(widget.params)
  const [contextRow, setContextRow] = useState(null)
  useEffect(() => {
    if (!widget.query_id || contextColumns.length === 0) {
      setContextRow(null)
      return
    }
    let cancelled = false
    const named = Object.keys(resolvedParams).length > 0 ? { namedParams: resolvedParams } : undefined
    runArrowQueryById(widget.query_id, named)
      .then(({ table }) => {
        if (cancelled || !table || table.numRows === 0) return
        const row = {}
        for (const col of contextColumns) row[col] = table.getChild(col)?.get(0) ?? null
        setContextRow(row)
      })
      .catch(() => { if (!cancelled) setContextRow(null) })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.query_id, contextKey, JSON.stringify(resolvedParams)])
  const contextChips = contextRow
    ? contextColumns.map(col => contextRow[col]).filter(v => !isEmptyValue(v)).map(v => String(v))
    : []

  const variables = useVariableValues()
  const setVariable = useSetVariable()

  const [steps, setSteps] = useState([])
  // The snapshot the trail last agreed with. Restoring a step writes several
  // variables at once, and each write re-runs the watcher — without this the
  // restore would immediately re-append the step it just removed.
  const lastSnapshot = useRef(null)
  const scrollRef = useRef(null)

  const snapshot = useMemo(() => {
    const out = {}
    for (const name of tracked) out[name] = variables[name]
    return out
  }, [tracked, variables])

  const snapshotKey = JSON.stringify(snapshot)

  // Watch the tracked variables and append a step when one of them takes a new
  // non-empty value. Only the *changed* variable names the step, so a drill
  // from Region → Store reads "Region: West › Store: 42".
  useEffect(() => {
    if (editMode || tracked.length === 0) return
    const prev = lastSnapshot.current
    lastSnapshot.current = snapshot
    if (prev === null) return // first render establishes the baseline, it isn't a drill

    for (const name of tracked) {
      const before = prev[name]
      const after = snapshot[name]
      if (JSON.stringify(before) === JSON.stringify(after)) continue
      if (isEmptyValue(after)) continue // clearing a filter drills OUT, it isn't a new step
      const value = chipLabel(after)
      const prefix = labels[name] ?? name
      setSteps(cur => {
        const next = [...cur, { name, label: prefix === '' ? value : `${prefix}: ${value}`, snapshot }]
        return next.length > maxSteps ? next.slice(-maxSteps) : next
      })
      break
    }
  // `labels` is authoring config, not a trigger — keying on the snapshot alone
  // keeps this from firing on every unrelated re-render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshotKey, tracked, maxSteps, editMode])

  // Keep the newest crumb in view on a trail wider than the tile.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollLeft = scrollRef.current.scrollWidth
  }, [steps.length])

  const goHome = () => {
    for (const name of tracked) setVariable(name, null)
    setSteps([])
    lastSnapshot.current = null
  }

  const goToStep = (index) => {
    const target = steps[index]
    if (!target) return
    for (const name of tracked) setVariable(name, target.snapshot[name] ?? null)
    setSteps(cur => cur.slice(0, index + 1))
    lastSnapshot.current = target.snapshot
  }

  if (tracked.length === 0) {
    return (
      <div className="flex items-center justify-center h-full px-4">
        <EmptyState
          title="No variables tracked"
          description="Pick the filter variables that make up the drill path."
          compact
        />
      </div>
    )
  }

  const chipStyle = (active) => (
    active === filled
      ? { background: chipColor, color: chipTextColor ?? 'var(--primary-fg)', borderColor: chipColor }
      : { background: 'transparent', color: chipTextColor ?? chipColor, borderColor: chipColor }
  )

  const sep = separator
    ? <span className="text-muted text-xs px-1" style={ink.muted ? { color: ink.muted } : undefined}>{separator}</span>
    : <ChevronRight size={props.size === 'md' ? 16 : 13} className="text-muted mx-1" style={ink.muted ? { color: ink.muted } : undefined} aria-hidden="true" />

  return (
    <div
      ref={scrollRef}
      className="flex items-center gap-1.5 h-full w-full px-2 overflow-x-auto overflow-y-hidden"
    >
      <button
        type="button"
        onClick={goHome}
        style={chipStyle(steps.length === 0 && contextChips.length === 0)}
        className={`shrink-0 inline-flex items-center rounded-full border font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50 ${chipSize}`}
      >
        <Home size={props.size === 'md' ? 16 : 12} />
        {homeLabel}
      </button>

      {contextChips.map((label, i) => (
        <div key={`ctx-${i}`} className="shrink-0 flex items-center gap-1.5">
          {sep}
          <span
            title={label}
            style={chipStyle(steps.length === 0 && i === contextChips.length - 1)}
            className={`inline-flex items-center max-w-[18rem] truncate rounded-full border font-medium ${chipSize}`}
          >
            {label}
          </span>
        </div>
      ))}

      {steps.map((step, i) => (
        <div key={`${step.name}-${i}`} className="shrink-0 flex items-center gap-1.5">
          {sep}
          <button
            type="button"
            onClick={() => goToStep(i)}
            title={step.label}
            style={chipStyle(i === steps.length - 1)}
            className={`inline-flex items-center max-w-[14rem] truncate rounded-full border font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50 ${chipSize}`}
          >
            {step.label}
          </button>
        </div>
      ))}
    </div>
  )
}
