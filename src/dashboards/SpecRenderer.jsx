/**
 * SpecRenderer.jsx — Read-only React Grid Layout renderer for a DashboardSpec.
 *
 * Props
 * -----
 * spec  {DashboardSpec}  The spec object to render (matches backend spec.py shape exactly).
 *
 * Behaviour
 * ---------
 * - Wraps the entire widget tree in <VariableProvider> seeded from spec.variables defaults.
 * - Uses the headless GridCanvas (CSS Grid + dnd-kit) in read-only mode, with a
 *   ResizeObserver on the container driving responsive breakpoint selection.
 * - draggable and resizable are both false — this is a read-only viewer.
 * - Dispatches each widget to the appropriate component:
 *     chart  → <ChartWidget>
 *     kpi    → <KpiWidget>
 *     table  → <TableWidget>
 *     filter → <FilterWidget>  (options fetched one-shot from options_query_id if present)
 *     text   → <TextWidget>
 * - On small screens (sm breakpoint) all widgets stack in a single column.
 * - Converts the backend 1-based pos (x,y,w,h) to the grid's 0-based x,y.
 *
 * Spec → Props normalization (M14-C)
 * ------------------------------------
 * The backend spec stores filter/text fields at the WIDGET TOP LEVEL:
 *   widget.subtype, widget.target_var, widget.options_query_id, widget.content
 * The M14-B components (FilterWidget, TextWidget) read from widget.props.*
 * SpecRenderer bridges this by building a normalized `props` object from the
 * top-level spec fields before passing the widget to each component. Canonical
 * location remains the top-level spec fields; the props shim is renderer-internal.
 */

import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import GridCanvas from './grid/GridCanvas.jsx'
import TabBar from './TabBar.jsx'
import { getBreakpointFromWidth } from './grid/breakpoints.js'
import ChartWidget from './widgets/ChartWidget.jsx'
import KpiWidget from './widgets/KpiWidget.jsx'
import TableWidget from './widgets/TableWidget.jsx'
import FilterWidget from './widgets/FilterWidget.jsx'
import TextWidget from './widgets/TextWidget.jsx'
import HtmlWidget from './widgets/HtmlWidget.jsx'
import MetricWidget from './widgets/MetricWidget.jsx'
import PivotWidget from './widgets/PivotWidget.jsx'
import SectionWidget from './widgets/SectionWidget.jsx'
import { VariableProvider } from './VariableStore.jsx'
import { runArrowQueryById } from '../lib/wasmRuntime.js'
import { backgroundToCss, styleToCss } from './widgetHtml.js'
import { buildResponsiveLayouts, isHiddenAt } from './responsiveLayout.js'

// ---------------------------------------------------------------------------
// Spec → props normalization
// ---------------------------------------------------------------------------

/**
 * Normalize a raw spec widget into the shape expected by each component.
 *
 * Backend spec stores filter/text widget-specific fields at the top level:
 *   widget.subtype, widget.target_var, widget.options_query_id, widget.content
 *
 * The M14-B components read from widget.props.* so SpecRenderer merges those
 * top-level fields into the props object before dispatch.  Other widget types
 * (chart, kpi, table) already have their spec fields at the right path; this
 * merge is additive/non-destructive for them.
 */
function normalizeWidget(raw) {
  const existing = raw.props ?? {}
  const merged = {
    // top-level filter/text fields → props (canonical source wins over any
    // duplicate in props, since the spec's top-level is authoritative per M14-A)
    subtype:    raw.subtype    ?? existing.subtype,
    target_var: raw.target_var ?? existing.target_var,
    content:    raw.content    ?? existing.content,
    label:      raw.label      ?? existing.label,
    placeholder: raw.placeholder ?? existing.placeholder,
    // Keep any other props the author set
    ...existing,
  }

  return { ...raw, props: merged }
}

// ---------------------------------------------------------------------------
// FilterWidget wrapper — fetches options from options_query_id on mount
// ---------------------------------------------------------------------------

/**
 * Loads options for a filter widget from options_query_id (if set) then
 * renders <FilterWidget>.  This is a one-shot fetch; it does NOT re-run when
 * variables change (the options list itself is not parameterised here).
 *
 * editMode — when true the live query fetch is skipped entirely (no wasm call
 * needed in the editor canvas).  The widget still renders with an empty options
 * list so the filter UI is fully visible for authoring (subtype, label, etc.).
 */
function FilterWidgetLoader({ widget, editMode = false }) {
  const optionsQueryId = widget.options_query_id ?? widget.props?.options_query_id
  const [options, setOptions] = useState([])

  useEffect(() => {
    // Skip the fetch in edit mode — the editor doesn't need live option data
    // and wasm may not be initialised in the editor canvas context.
    if (!optionsQueryId || editMode) return
    let cancelled = false

    async function fetchOptions() {
      try {
        const { table } = await runArrowQueryById(optionsQueryId)
        if (cancelled || !table || table.numRows === 0) return

        // Map the first two columns to {value, label}; if only one col, use it for both.
        const fields = table.schema.fields.map(f => f.name)
        const valueField = fields[0]
        const labelField = fields[1] ?? fields[0]

        const opts = []
        for (let i = 0; i < table.numRows; i++) {
          const valueCol = table.getChild(valueField)
          const labelCol = table.getChild(labelField)
          const v = valueCol ? valueCol.get(i) : null
          const l = labelCol ? labelCol.get(i) : v
          if (v != null) {
            opts.push({ value: String(v), label: l != null ? String(l) : String(v) })
          }
        }
        if (!cancelled) setOptions(opts)
      } catch (err) {
        // Non-fatal — widget renders with empty options list
        console.warn('[SpecRenderer] FilterWidget options fetch failed:', err.message)
      }
    }

    fetchOptions()
    return () => { cancelled = true }
  }, [optionsQueryId, editMode])

  return <FilterWidget widget={widget} options={options} />
}

// ---------------------------------------------------------------------------
// Widget dispatcher
// ---------------------------------------------------------------------------

/**
 * Map widget type to the right component.
 *
 * editMode — passed down from SpecRenderer when the editor (W3-A) renders the
 * spec for filter authoring.  Filter widgets render in BOTH modes so the
 * filters drawer is accessible during editing; this flag is forwarded to
 * FilterWidgetLoader so it can skip the live query fetch when appropriate
 * (avoids spurious network calls in the editor canvas).
 */
function WidgetComponent({ widget, onOpenDrawer, editMode = false }) {
  // Normalize top-level spec fields into widget.props before dispatch
  const w = useMemo(() => normalizeWidget(widget), [widget])

  // A custom HTML template overrides the default widget body (any type).
  if (w.html) return <HtmlWidget widget={w} />

  // A section widget that declares a drilldown_group is a drilldown TRIGGER:
  // clicking it opens the matching drawer (the legacy BasicWidgetGroupStepper).
  if (w.type === 'section' && w.props?.drilldown_group) {
    return (
      <button
        type="button"
        onClick={() => onOpenDrawer?.(w.props.drilldown_group)}
        className="flex items-center justify-center gap-2 w-full h-full px-3 text-sm font-medium text-fg bg-surface hover:bg-border/40 transition-colors"
      >
        <span className="i">⤢</span>
        {w.props.title || 'Drill down'}
        <span className="text-muted text-xs">▸</span>
      </button>
    )
  }

  switch (w.type) {
    case 'chart':   return <ChartWidget  widget={w} />
    case 'kpi':     return <KpiWidget    widget={w} />
    case 'metric':  return <MetricWidget widget={w} />
    case 'table':   return <TableWidget  widget={w} />
    case 'pivot':   return <PivotWidget  widget={w} />
    // Filter widgets render in both view mode AND edit mode so the filters
    // drawer is available for authoring.  editMode suppresses the live query
    // fetch (no wasm needed in the editor canvas).
    case 'filter':  return <FilterWidgetLoader widget={w} editMode={editMode} />
    case 'text':    return <TextWidget   widget={w} />
    case 'section': return <SectionWidget widget={w} />
    default:
      return (
        <div className="flex items-center justify-center h-full text-sm text-muted">
          Unknown widget type: {w.type}
        </div>
      )
  }
}

// ---------------------------------------------------------------------------
// Slide-over drawer (filters panel + drilldown panels)
// ---------------------------------------------------------------------------

/**
 * Right-side slide-over panel. Renders a list of drawer widgets stacked
 * vertically. Used for the shared "Filters" drawer and for per-trigger
 * drilldown drawers (legacy renderToDrawer / BasicWidgetGroup).
 */
function SlideOver({ open, title, widgets, onClose, wide }) {
  if (!open) return null
  const sorted = [...widgets].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div
        className="relative h-full bg-bg border-l border-border shadow-xl overflow-y-auto"
        style={{ width: wide ? 'min(880px, 92vw)' : 'min(420px, 92vw)' }}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between px-4 py-3 bg-surface border-b border-border">
          <h3 className="text-sm font-semibold text-fg">{title}</h3>
          <button type="button" onClick={onClose} className="text-muted hover:text-fg text-lg leading-none">×</button>
        </div>
        <div className="p-4 space-y-4">
          {sorted.length === 0 ? (
            <div className="text-sm text-muted py-8 text-center">Nothing to show.</div>
          ) : sorted.map(w => (
            <div
              key={w.id}
              // Filter widgets: overflow-visible so dropdown popovers inside the
              // drawer panel are not clipped by the card boundary.
              className={`rounded-lg border border-border bg-surface ${w.type === 'filter' ? 'overflow-visible' : 'overflow-hidden'}`}
              style={{ minHeight: w.type === 'filter' ? undefined : 280 }}
            >
              <WidgetComponent widget={w} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Layout builder
// ---------------------------------------------------------------------------

/**
 * Convert a spec widget list into 0-based grid layout arrays per breakpoint,
 * applying spec.responsive overrides for md/sm with a fallback to the layout
 * derived from widget.pos (lg is always the canonical desktop layout).
 *
 * Backend pos uses 1-based x and y (column / row start); GridCanvas uses 0-based.
 * `colsByBp` carries the per-breakpoint column counts read from spec.layout so md
 * overrides clamp to the tablet column count and sm stacks into a single column
 * (or whatever spec.layout declares). The viewer is read-only, so no per-widget
 * draggable/resizable extras are needed — GridCanvas controls interaction.
 */
function buildLayouts(spec, cols, colsByBp) {
  return buildResponsiveLayouts(spec, cols, undefined, colsByBp)
}

// ---------------------------------------------------------------------------
// Build initial variable values from spec.variables
// ---------------------------------------------------------------------------

/**
 * Extract the default values map from spec.variables.
 * spec.variables shape: [{ name, type, default? }, ...]
 *
 * Returns a flat { [varName]: defaultValue } object used to seed the store.
 * Variables without a default get undefined (the store skips them so
 * resolveParams returns undefined for unset refs, which is correct).
 */
function buildVariableDefaults(specVariables) {
  if (!Array.isArray(specVariables)) return {}
  const defaults = {}
  for (const v of specVariables) {
    if (v?.name) {
      defaults[v.name] = v.default ?? undefined
    }
  }
  return defaults
}

// ---------------------------------------------------------------------------
// SpecRenderer
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   spec: object,
 *   initialVariables?: Record<string, unknown>,
 *   onVariableChange?: (name: string, value: unknown) => void,
 * }} props
 *
 * initialVariables — externally supplied variable values (e.g. from URL params
 * or an embed token) that LAYER OVER the spec defaults.  See DashboardViewPage
 * for the precedence ordering.
 *
 * onVariableChange — optional callback fired when any filter widget changes a
 * variable.  Used by DashboardViewPage to write the new value back to URL search
 * params so the state survives a refresh and is shareable.
 */
// Inner component — ASSUMES `spec` is non-null (the null-guard lives in the
// thin wrapper below). Because the early return is gone, every hook here runs
// unconditionally on every render, satisfying the Rules of Hooks even as `spec`
// transitions undefined → loaded (async fetch / embed hydration).
function SpecRendererInner({ spec, initialVariables = {}, onVariableChange, forceBreakpoint, activeTabId, onTabChange, editMode = false }) {
  const cols = spec.layout?.cols ?? 12
  const rowHeight = spec.layout?.row_height ?? 60
  const allWidgets = spec.widgets ?? []

  // Breakpoint thresholds + per-breakpoint column counts. lg/md default to the
  // spec column count; sm stacks into a single column. New per-breakpoint cols
  // fields (cols_md / cols_sm) override the defaults when a spec sets them.
  const breakpoints = { lg: 1200, md: 768, sm: 480 }
  const colsByBp = {
    lg: cols,
    md: spec.layout?.cols_md ?? cols,
    sm: spec.layout?.cols_sm ?? 1,
  }

  // Partition out drawer widgets (drawer:true) — they render in slide-overs,
  // not on the main grid. Group them by drawer_group ('filters' or 'dg_*').
  const { widgets, drawerGroups } = useMemo(() => {
    const grid = []
    const groups = {}
    for (const w of allWidgets) {
      if (w.drawer) {
        const g = w.drawer_group || 'filters'
        ;(groups[g] ??= []).push(w)
      } else {
        grid.push(w)
      }
    }
    return { widgets: grid, drawerGroups: groups }
  }, [JSON.stringify(allWidgets)])

  const [openDrawer, setOpenDrawer] = useState(null)
  const hasFilters = (drawerGroups.filters?.length ?? 0) > 0

  // -------------------------------------------------------------------------
  // Tabs (SHARED CONTRACT)
  // -------------------------------------------------------------------------
  // The renderer is controlled when an onTabChange callback is supplied; the
  // activeTabId prop is then the source of truth. Without a callback it falls
  // back to uncontrolled internal state. The effective tab resolves as:
  //   activeTabId ?? internalState ?? spec.tabs[0]?.id
  // When spec.tabs is empty/absent there are no tabs and behavior is identical
  // to before (no TabBar, no widget filtering).
  const tabs = Array.isArray(spec.tabs) ? spec.tabs : []
  const firstTabId = tabs[0]?.id ?? null
  const [internalTabId, setInternalTabId] = useState(null)
  const effectiveTabId = activeTabId ?? internalTabId ?? firstTabId
  const setTab = onTabChange ?? setInternalTabId

  // Filter grid widgets down to the active tab. A widget belongs to the active
  // tab when its tab_id matches the effective tab, OR its tab_id is null/absent
  // and the effective tab is the first tab (null === first tab). With no tabs
  // every widget passes through unchanged.
  const tabbedWidgets = useMemo(() => {
    if (tabs.length === 0) return widgets
    return widgets.filter((w) => {
      const t = w.tab_id ?? null
      if (t === effectiveTabId) return true
      return t == null && effectiveTabId === firstTabId
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgets, effectiveTabId, firstTabId, tabs.length])
  const drawerTitle = openDrawer === 'filters'
    ? (spec.drawer?.title || 'Filters')
    : (widgets.find(w => w.props?.drilldown_group === openDrawer)?.props?.title || 'Drill down')

  // Build the initial values for the VariableProvider:
  //   spec.variables defaults  (lowest precedence)
  //   + initialVariables prop  (URL params / embed token — higher precedence)
  //
  // NOTE: embed-token-locked params should be passed in initialVariables with
  // the locked values. The DashboardViewPage is responsible for ensuring that
  // locked params from an embed token cannot be overridden by URL params.
  // A future embed integration should populate initialVariables from the token
  // and strip the same keys from the URL before passing the remainder here.
  const variableDefaults = useMemo(
    () => ({
      ...buildVariableDefaults(spec.variables),
      ...initialVariables,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(spec.variables), JSON.stringify(initialVariables)],
  )

  const layouts = useMemo(
    () => buildLayouts({ ...spec, widgets: tabbedWidgets }, cols, colsByBp),
    // Rebuild when grid widgets or the per-breakpoint overrides change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tabbedWidgets, cols, colsByBp.md, colsByBp.sm, JSON.stringify(spec.responsive)],
  )

  // Measure the container width via a ResizeObserver so breakpoint selection
  // tracks the live layout (replaces RGL's useContainerWidth hook).
  const containerRef = useRef(null)
  const [width, setWidth] = useState(1200)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    // Seed from the current measurement before the observer fires.
    setWidth(el.clientWidth || 1200)
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = entry.contentRect?.width
        if (w) setWidth(w)
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // The breakpoint actually being rendered: forced (editor preview frame) or
  // derived from the container width (public viewer). Used to (a) pick the grid's
  // active layout + column count so it matches the editor, and (b) filter out
  // widgets hidden at this breakpoint so the rendered children match the
  // (already-filtered) layout array.
  const renderBreakpoint = forceBreakpoint ?? getBreakpointFromWidth(breakpoints, width || 1200)
  const visibleWidgets = tabbedWidgets.filter(w => !isHiddenAt(w, renderBreakpoint))
  const visibleWidgetsById = useMemo(
    () => new Map(visibleWidgets.map(w => [w.id, w])),
    [visibleWidgets],
  )

  // The active breakpoint's layout + column count.
  const activeLayout = layouts[renderBreakpoint] ?? layouts.lg
  const activeCols = colsByBp[renderBreakpoint] ?? cols

  // Grid flexibility options (spec.layout.*) — compaction mode, gap & padding.
  // GridCanvas takes a single scalar `gap` for both axes (margin_x/margin_y are
  // symmetric in practice) and a {x,y} padding object.
  const compactionMode = spec.layout?.compaction ?? 'free'   // default: free-place (preserves authored positions)
  const gap = Array.isArray(spec.layout?.margin)
    ? (spec.layout.margin[0] ?? 12)
    : (spec.layout?.margin_x ?? 12)
  const padding = Array.isArray(spec.layout?.container_padding)
    ? { x: spec.layout.container_padding[0] ?? 0, y: spec.layout.container_padding[1] ?? 0 }
    : { x: spec.layout?.padding_x ?? 0, y: spec.layout?.padding_y ?? 0 }

  const bgStyle = useMemo(() => backgroundToCss(spec.background), [JSON.stringify(spec.background)])

  // Stable per-cell renderer. GridCanvas's renderGridItem useCallback depends on
  // renderItem, so an inline arrow here would rebuild it every render and remount
  // the entire widget subtree on any SpecRenderer state change (width/ResizeObserver,
  // tab switch, variable change). Memoize so it only rebuilds when the visible
  // widget set or editMode actually changes. (setOpenDrawer is a stable state
  // setter; included for lint correctness. styleToCss / WidgetComponent are
  // module-level and need no dep.)
  const renderItem = useCallback((item) => {
    const widget = visibleWidgetsById.get(item.i)
    if (!widget) return null
    // When a widget declares its own style (incl. transparent bg) don't
    // force the opaque default surface — let the style win.
    const customStyle = styleToCss(widget.style)
    const hasCustomBg = customStyle && (
      'background' in customStyle || 'backgroundColor' in customStyle || 'backgroundImage' in customStyle
    )
    // Filter widgets contain absolutely-positioned dropdown popovers.
    // overflow-hidden clips those dropdowns (even when portaled, a stacking
    // ancestor with overflow:hidden can suppress the portal's z-index in some
    // browsers). Use overflow-visible for filter cells so open dropdowns
    // are never clipped; the portal approach (W3-B) makes this fully safe.
    const isFilter = widget.type === 'filter'
    return (
      <div
        className={`w-full h-full rounded-xl ${isFilter ? 'overflow-visible' : 'overflow-hidden'} ${hasCustomBg ? '' : 'bg-surface border border-border shadow-sm'}`}
        style={customStyle}
      >
        <WidgetComponent widget={widget} onOpenDrawer={setOpenDrawer} editMode={editMode} />
      </div>
    )
  }, [visibleWidgetsById, editMode, setOpenDrawer])

  return (
    <VariableProvider initialValues={variableDefaults} onVariableChange={onVariableChange}>
      <div
        className="w-full"
        ref={containerRef}
        style={bgStyle ? { ...bgStyle, padding: 16, borderRadius: 12 } : undefined}
      >
        {(spec.title || hasFilters) && (
          <div className="flex items-center justify-between px-1 mb-4 gap-3">
            {spec.title && (
              <h2 className="text-xl font-bold font-display text-fg">{spec.title}</h2>
            )}
            {hasFilters && (
              <button
                type="button"
                onClick={() => setOpenDrawer('filters')}
                className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border border-border bg-surface text-fg hover:bg-border/40 transition-colors"
              >
                <span aria-hidden>⚲</span> {spec.drawer?.title || 'Filters'}
                <span className="text-xs text-muted">({drawerGroups.filters.length})</span>
              </button>
            )}
          </div>
        )}
        {tabs.length > 1 && (
          <div className="mb-4">
            <TabBar
              tabs={tabs}
              activeTabId={effectiveTabId}
              onChange={setTab}
              tabBar={spec.tabBar}
            />
          </div>
        )}
        {tabbedWidgets.length === 0 ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted border-2 border-dashed border-border rounded-xl bg-surface">
            No widgets in this dashboard.
          </div>
        ) : (
          <GridCanvas
            layout={activeLayout.filter(item => visibleWidgetsById.has(item.i))}
            cols={activeCols}
            rowHeight={rowHeight}
            gap={gap}
            padding={padding}
            width={width}
            draggable={false}
            resizable={false}
            compaction={compactionMode}
            renderItem={renderItem}
          />
        )}
      </div>
      <SlideOver
        open={openDrawer != null}
        title={drawerTitle}
        widgets={openDrawer != null ? (drawerGroups[openDrawer] ?? []) : []}
        wide={openDrawer != null && openDrawer !== 'filters'}
        onClose={() => setOpenDrawer(null)}
      />
    </VariableProvider>
  )
}

// ---------------------------------------------------------------------------
// SpecRenderer (public default export) — thin null-guard WRAPPER.
// ---------------------------------------------------------------------------
//
// Keeping the null-guard out here (and out of SpecRendererInner) means the inner
// component's hooks always run unconditionally. When `spec` transitions
// undefined → loaded (async fetch / embed hydration), React mounts a fresh
// SpecRendererInner with a consistent hook order instead of throwing
// "Rendered more hooks than during the previous render". The export name/shape
// is unchanged — callers still `import SpecRenderer from '.../SpecRenderer.jsx'`.
export default function SpecRenderer(props) {
  if (!props.spec) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-muted">
        No spec provided.
      </div>
    )
  }
  return <SpecRendererInner {...props} />
}
