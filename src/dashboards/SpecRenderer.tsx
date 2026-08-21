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
import ImageWidget from './widgets/ImageWidget.jsx'
import StepperWidget from './widgets/StepperWidget.jsx'
import { VariableProvider, useSetVariable, useResolvedParams } from './VariableStore.jsx'
import { CrossFilterProvider } from './CrossFilterContext.jsx'
import { RefreshContext } from './RefreshContext.jsx'
import { useAutoRefresh } from './useAutoRefresh.js'
import { runArrowQueryById, prefetchDemoData } from '../lib/wasmRuntime.js'
import { backgroundToCss, styleToCss } from './widgetHtml.js'
import { useTheme } from '../contexts/ThemeContext.jsx'
import { findWidgetStylePreset } from './stylePresets.js'
import { buildResponsiveLayouts, isHiddenAt } from './responsiveLayout.js'
import { useProviderData } from './useProviderData.js'
import Button from '../components/ui/Button.jsx'
import EmptyState from '../components/ui/EmptyState.jsx'

// ---------------------------------------------------------------------------
// Placement (SHARED CONTRACT)
// ---------------------------------------------------------------------------

/**
 * Effective placement for a widget: 'grid' | 'header' | 'drawer'.
 *
 * Contract (shared with backend + editor):
 *   - if widget.placement is set, use it;
 *   - else if widget.drawer === true, treat as 'drawer' (legacy flag);
 *   - else 'grid' (default).
 *
 * Header widgets render in the horizontal filter bar above the grid, ordered by
 * widget.order (pos ignored). Grid widgets render in the GridCanvas. Drawer
 * widgets render in the slide-over panel.
 */
function effectivePlacement(w) {
  if (w?.placement) return w.placement
  if (w?.drawer === true) return 'drawer'
  return 'grid'
}

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
 *
 * providerTable — when a widget is bound to a DataProvider (BET-3), the
 * resolved Arrow table slice for this widget is passed here so the widget
 * skips its own query_id / metric fetch.  null = legacy path unchanged.
 */
function WidgetComponent({ widget, onOpenDrawer = undefined, editMode = false, providerTable = null }) {
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
        className="flex items-center justify-center gap-2 w-full h-full px-3 text-sm font-medium text-fg bg-surface hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-xl group"
      >
        <span className="opacity-60 group-hover:opacity-100 transition-opacity" aria-hidden="true">⤢</span>
        {w.props.title || 'Drill down'}
        <span className="text-muted text-xs group-hover:translate-x-0.5 transition-transform" aria-hidden="true">▸</span>
      </button>
    )
  }

  switch (w.type) {
    case 'chart':   return <ChartWidget  widget={w} providerTable={providerTable} />
    case 'kpi':     return <KpiWidget    widget={w} providerTable={providerTable} />
    case 'metric':  return <MetricWidget widget={w} />
    case 'table':   return <TableWidget  widget={w} providerTable={providerTable} />
    case 'pivot':   return <PivotWidget  widget={w} />
    // Filter widgets render in both view mode AND edit mode so the filters
    // drawer is available for authoring.  editMode suppresses the live query
    // fetch (no wasm needed in the editor canvas).
    case 'filter':  return <FilterWidgetLoader widget={w} editMode={editMode} />
    case 'text':    return <TextWidget   widget={w} />
    case 'image':   return <ImageWidget  widget={w} />
    case 'section': return <SectionWidget widget={w} />
    // A stepper shows one child widget at a time in a single tile (the legacy
    // in-tile drill-down). It renders its children back through this same
    // dispatch, so nesting and every widget type keep working inside it.
    case 'stepper': return (
      <StepperWidget
        widget={w}
        renderChild={(child) => (
          <WidgetComponent
            widget={child}
            onOpenDrawer={onOpenDrawer}
            editMode={editMode}
          />
        )}
      />
    )
    default:
      return (
        <div className="flex items-center justify-center h-full px-4">
          <EmptyState
            title={`Unknown widget type`}
            description={`"${w.type}" is not recognised.`}
            compact
          />
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
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={title}>
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Panel */}
      <div
        className="relative h-full bg-surface border-l border-border shadow-2xl overflow-y-auto flex flex-col"
        style={{ width: wide ? 'min(880px, 92vw)' : 'min(420px, 92vw)' }}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between px-5 py-3.5 bg-surface/90 backdrop-blur border-b border-border shrink-0">
          <h3 className="text-sm font-semibold text-fg">{title}</h3>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Close panel"
          >
            <span aria-hidden="true" className="text-base leading-none">×</span>
          </button>
        </div>
        {/* Content */}
        <div className="flex-1 p-4 space-y-3">
          {sorted.length === 0 ? (
            <EmptyState title="Nothing to show." compact />
          ) : sorted.map(w => (
            <div
              key={w.id}
              // Filter widgets: overflow-visible so dropdown popovers inside the
              // drawer panel are not clipped by the card boundary.
              className={`rounded-xl border border-border bg-bg ${w.type === 'filter' ? 'overflow-visible' : 'overflow-hidden'}`}
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

// ---------------------------------------------------------------------------
// CrossFilterProviderWrapper — inner wrapper that binds setVariable from the
// VariableStore context (which is only available once VariableProvider mounts).
// ---------------------------------------------------------------------------

/**
 * Thin adapter: reads setVariable from VariableStore then passes it to
 * CrossFilterProvider so widgets can emit cross-filter events that write
 * board variables.
 *
 * @param {{ onCrossFilter?, onTabChange?, children }} props
 */
function CrossFilterProviderWrapper({ onCrossFilter, onTabChange, children }) {
  const setVariable = useSetVariable()
  return (
    <CrossFilterProvider
      setVariable={setVariable}
      onTabChange={onTabChange}
      onCrossFilter={onCrossFilter}
    >
      {children}
    </CrossFilterProvider>
  )
}

// ---------------------------------------------------------------------------
// SpecRendererInner
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   spec: object,
 *   initialVariables?: Record<string, unknown>,
 *   onVariableChange?: (name: string, value: unknown) => void,
 *   onCrossFilter?: (event: { type: string, var: string|null, value: unknown, tabId: string|null }) => void,
 * }} props
 *
 * initialVariables — externally supplied variable values (e.g. from URL params
 * or an embed token) that LAYER OVER the spec defaults.  See DashboardViewPage
 * for the precedence ordering.
 *
 * onVariableChange — optional callback fired when any filter widget changes a
 * variable.  Used by DashboardViewPage to write the new value back to URL search
 * params so the state survives a refresh and is shareable.
 *
 * onCrossFilter — optional callback fired when a widget emits a cross-filter or
 * navigate event (data-point click). Receives { type, var, value, tabId }.
 * Useful for parent frames / embed integrations.
 */
// ---------------------------------------------------------------------------
// SpecRendererBody — runs INSIDE <VariableProvider>
// ---------------------------------------------------------------------------
//
// All hooks that require VariableValuesContext (useResolvedParams, useProviderData
// via useResolvedParams internally) live here. The 8×useResolvedParams +
// 8×useProviderData calls are unconditional and in fixed order (Rules of Hooks).
//
// Props are forwarded from SpecRendererInner after it has computed the
// VariableProvider seed values and mounted the provider.

function SpecRendererBody({
  spec,
  boardId,
  allWidgets,
  cols,
  colsByBp,
  rowHeight,
  refreshEpoch,
  forceBreakpoint,
  activeTabId,
  onTabChange,
  onCrossFilter,
  editMode,
}) {
  // Theme-adaptation ctx for styleToCss/backgroundToCss (see widgetHtml.js /
  // lib/themeColor.js). `spec.themeAdapt: 'off'` is a per-board escape hatch;
  // omitting it (or 'auto') keeps the default on — the no-ctx / adapted
  // outputs are proven identical whenever the viewer's theme already matches
  // an authored light chrome, so this is safe to default on.
  const { theme } = useTheme()
  const themeAdaptOn = spec.themeAdapt !== 'off'
  const adaptCtx = useMemo(() => (themeAdaptOn ? { theme } : undefined), [themeAdaptOn, theme])

  // Partition widgets by effective placement (SHARED CONTRACT):
  //   'drawer' → slide-over panel, grouped by drawer_group ('filters' or 'dg_*')
  //   'header' → horizontal filter bar above the grid (ordered by widget.order)
  //   'grid'   → the main GridCanvas (default)
  const { widgets, headerWidgets, drawerGroups } = useMemo(() => {
    const grid: Record<string, any>[] = []
    const header: Record<string, any>[] = []
    const groups: Record<string, Record<string, any>[]> = {}
    for (const w of allWidgets) {
      const placement = effectivePlacement(w)
      if (placement === 'drawer') {
        const g = w.drawer_group || 'filters'
        ;(groups[g] ??= []).push(w)
      } else if (placement === 'header') {
        header.push(w)
      } else {
        grid.push(w)
      }
    }
    return { widgets: grid, headerWidgets: header, drawerGroups: groups }
  }, [JSON.stringify(allWidgets)])

  const [openDrawer, setOpenDrawer] = useState(null)
  const hasFilters = (drawerGroups.filters?.length ?? 0) > 0

  // -------------------------------------------------------------------------
  // Tabs (SHARED CONTRACT)
  // -------------------------------------------------------------------------
  const tabs = Array.isArray(spec.tabs) ? spec.tabs : []
  const firstTabId = tabs[0]?.id ?? null
  const [internalTabId, setInternalTabId] = useState(null)
  const effectiveTabId = activeTabId ?? internalTabId ?? firstTabId
  const setTab = onTabChange ?? setInternalTabId

  // Filter grid widgets down to the active tab.
  const tabbedWidgets = useMemo(() => {
    if (tabs.length === 0) return widgets
    return widgets.filter((w) => {
      const t = w.tab_id ?? null
      if (t === effectiveTabId) return true
      return t == null && effectiveTabId === firstTabId
    })
  }, [widgets, effectiveTabId, firstTabId, tabs.length])

  // Header (filter-bar) widgets, tab-scoped + sorted by widget.order ascending.
  const tabbedHeaderWidgets = useMemo(() => {
    const scoped = tabs.length === 0
      ? headerWidgets
      : headerWidgets.filter((w) => {
          const t = w.tab_id ?? null
          if (t === effectiveTabId) return true
          return t == null && effectiveTabId === firstTabId
        })
    return [...scoped].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  }, [headerWidgets, effectiveTabId, firstTabId, tabs.length])

  const drawerTitle = openDrawer === 'filters'
    ? (spec.drawer?.title || 'Filters')
    : (widgets.find(w => w.props?.drilldown_group === openDrawer)?.props?.title || 'Drill down')

  const layouts = useMemo(
    () => buildLayouts({ ...spec, widgets: tabbedWidgets }, cols, colsByBp),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tabbedWidgets, cols, colsByBp.md, colsByBp.sm, JSON.stringify(spec.responsive)],
  )

  // Measure the container width via a ResizeObserver.
  const containerRef = useRef(null)
  const [width, setWidth] = useState(1200)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
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

  const breakpoints = { lg: 1200, md: 768, sm: 480 }
  const renderBreakpoint = forceBreakpoint ?? getBreakpointFromWidth(breakpoints, width || 1200)
  const visibleWidgets = tabbedWidgets.filter(w => !isHiddenAt(w, renderBreakpoint))
  const visibleWidgetsById = useMemo(
    () => new Map(visibleWidgets.map(w => [w.id, w])),
    [visibleWidgets],
  )

  const activeLayout = layouts[renderBreakpoint] ?? layouts.lg
  const activeCols = colsByBp[renderBreakpoint] ?? cols

  const compactionMode = spec.layout?.compaction ?? 'free'
  const gap = Array.isArray(spec.layout?.margin)
    ? (spec.layout.margin[0] ?? 12)
    : (spec.layout?.margin_x ?? 12)
  const padding = Array.isArray(spec.layout?.container_padding)
    ? { x: spec.layout.container_padding[0] ?? 0, y: spec.layout.container_padding[1] ?? 0 }
    : { x: spec.layout?.padding_x ?? 0, y: spec.layout?.padding_y ?? 0 }

  const bgStyle = useMemo(
    () => backgroundToCss(spec.background, adaptCtx),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(spec.background), adaptCtx],
  )

  // ── BET-3 DataProvider wiring ──────────────────────────────────────────────
  // useResolvedParams reads VariableValuesContext — this component is rendered
  // INSIDE <VariableProvider> so the context is available.
  // Fixed 8 slots, unconditional, in fixed order — satisfies Rules of Hooks.

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const boardProviders = useMemo(() => Array.isArray(spec.data) ? spec.data : [], [JSON.stringify(spec.data)])

  const MAX_PROVIDERS = 8
  const providerSlots = useMemo(() => {
    const slots = []
    for (let i = 0; i < MAX_PROVIDERS; i++) {
      slots.push(boardProviders[i] ?? null)
    }
    return slots
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(boardProviders)])

  // 8× useResolvedParams (unconditional, fixed order)
  const resolvedP0 = useResolvedParams(providerSlots[0]?.params ?? {})
  const resolvedP1 = useResolvedParams(providerSlots[1]?.params ?? {})
  const resolvedP2 = useResolvedParams(providerSlots[2]?.params ?? {})
  const resolvedP3 = useResolvedParams(providerSlots[3]?.params ?? {})
  const resolvedP4 = useResolvedParams(providerSlots[4]?.params ?? {})
  const resolvedP5 = useResolvedParams(providerSlots[5]?.params ?? {})
  const resolvedP6 = useResolvedParams(providerSlots[6]?.params ?? {})
  const resolvedP7 = useResolvedParams(providerSlots[7]?.params ?? {})

  // 8× useProviderData (unconditional, fixed order)
  const slot0 = useProviderData(boardId, providerSlots[0]?.id ?? null, resolvedP0, refreshEpoch)
  const slot1 = useProviderData(boardId, providerSlots[1]?.id ?? null, resolvedP1, refreshEpoch)
  const slot2 = useProviderData(boardId, providerSlots[2]?.id ?? null, resolvedP2, refreshEpoch)
  const slot3 = useProviderData(boardId, providerSlots[3]?.id ?? null, resolvedP3, refreshEpoch)
  const slot4 = useProviderData(boardId, providerSlots[4]?.id ?? null, resolvedP4, refreshEpoch)
  const slot5 = useProviderData(boardId, providerSlots[5]?.id ?? null, resolvedP5, refreshEpoch)
  const slot6 = useProviderData(boardId, providerSlots[6]?.id ?? null, resolvedP6, refreshEpoch)
  const slot7 = useProviderData(boardId, providerSlots[7]?.id ?? null, resolvedP7, refreshEpoch)

  const providerResults = useMemo(() => {
    const results = {}
    const slots = [slot0, slot1, slot2, slot3, slot4, slot5, slot6, slot7]
    boardProviders.forEach((provider, i) => {
      if (i < MAX_PROVIDERS) {
        results[provider.id] = slots[i]
      }
    })
    return results
  }, [boardProviders, slot0, slot1, slot2, slot3, slot4, slot5, slot6, slot7])

  const widgetProviderTableMap = useMemo(() => {
    const map = {}
    for (const w of allWidgets) {
      if (w.source?.provider && w.source?.result) {
        const pResult = providerResults[w.source.provider]
        if (pResult) {
          map[w.id] = pResult.tables[w.source.result] ?? null
        }
      }
    }
    return map
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(allWidgets), providerResults])
  // ── End BET-3 DataProvider wiring ─────────────────────────────────────────

  // Dashboard-level style preset (e.g. "glass") — only used as a fallback for
  // widgets that don't declare their own `widget.style`, so per-widget
  // overrides always win and dashboards with no `stylePreset` are unaffected.
  const dashboardPresetStyle = useMemo(
    () => (spec.stylePreset ? findWidgetStylePreset(spec.stylePreset)?.style ?? null : null),
    [spec.stylePreset],
  )

  // Stable per-cell renderer.
  const renderItem = useCallback((item) => {
    const widget = visibleWidgetsById.get(item.i)
    if (!widget) return null
    // A widget can pin its own colors (widget.style.themeAdapt: false) — e.g.
    // a brand logo tile that must never repaint.
    const widgetCtx = widget.style?.themeAdapt === false ? undefined : adaptCtx
    const customStyle = styleToCss(widget.style, widgetCtx) ?? (dashboardPresetStyle ? styleToCss(dashboardPresetStyle, widgetCtx) : undefined)
    const hasCustomBg = customStyle && (
      'background' in customStyle || 'backgroundColor' in customStyle || 'backgroundImage' in customStyle
    )
    const isFilter = widget.type === 'filter'
    const providerTable = widgetProviderTableMap[widget.id] ?? null
    return (
      <div
        className={[
          'w-full h-full rounded-xl transition-shadow duration-200',
          isFilter ? 'overflow-visible' : 'overflow-hidden',
          !hasCustomBg && 'bg-surface border border-border shadow-sm hover:shadow-md',
        ].filter(Boolean).join(' ')}
        style={customStyle}
      >
        <WidgetComponent widget={widget} onOpenDrawer={setOpenDrawer} editMode={editMode} providerTable={providerTable} />
      </div>
    )
  }, [visibleWidgetsById, editMode, setOpenDrawer, widgetProviderTableMap, dashboardPresetStyle, adaptCtx])

  return (
    <RefreshContext.Provider value={refreshEpoch}>
    <CrossFilterProviderWrapper onCrossFilter={onCrossFilter} onTabChange={setTab}>
    <div
      className="w-full"
      data-dashboard-root
      ref={containerRef}
      style={bgStyle ? { ...bgStyle, padding: 16, borderRadius: 12 } : undefined}
    >
      {(spec.title || spec.description || hasFilters) && (
        <div className="flex items-start justify-between px-1 mb-5 gap-3">
          <div className="min-w-0">
            {spec.title && (
              <h2 className="text-[22px] font-bold font-display text-fg leading-tight tracking-tight">{spec.title}</h2>
            )}
            {spec.description && (
              <p className="mt-1 text-[13px] text-muted leading-relaxed max-w-3xl">{spec.description}</p>
            )}
          </div>
          {hasFilters && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setOpenDrawer('filters')}
              className="shrink-0"
              aria-label={`Open ${spec.drawer?.title || 'Filters'} panel`}
            >
              <span aria-hidden="true" className="text-xs">⚲</span>
              {spec.drawer?.title || 'Filters'}
              <span className="text-xs opacity-60">({drawerGroups.filters.length})</span>
            </Button>
          )}
        </div>
      )}
      {tabs.length > 1 && (
        <div className="mb-5">
          <TabBar
            tabs={tabs}
            activeTabId={effectiveTabId}
            onChange={setTab}
            tabBar={spec.tabBar}
          />
        </div>
      )}
      {tabbedHeaderWidgets.length > 0 && (
        <div className="nubi-filter-bar flex flex-wrap items-end gap-3 px-1 pb-4 mb-4 border-b border-border">
          {tabbedHeaderWidgets.map((w) => (
            <div
              key={w.id}
              className="min-w-[10rem] max-w-xs overflow-visible"
            >
              <WidgetComponent widget={w} onOpenDrawer={setOpenDrawer} editMode={editMode} />
            </div>
          ))}
        </div>
      )}
      {tabbedWidgets.length === 0 ? (
        <div className="border-2 border-dashed border-border rounded-2xl bg-surface/50">
          <EmptyState
            title="No widgets"
            description="This dashboard has no widgets to display."
            compact
          />
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
    </CrossFilterProviderWrapper>
    </RefreshContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// SpecRendererInner
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   spec: object,
 *   initialVariables?: Record<string, unknown>,
 *   onVariableChange?: (name: string, value: unknown) => void,
 *   onCrossFilter?: (event: { type: string, var: string|null, value: unknown, tabId: string|null }) => void,
 * }} props
 *
 * Computes the VariableProvider seed values, then mounts:
 *   <VariableProvider>
 *     <SpecRendererBody />   ← provider-resolution hooks live here (inside the provider)
 *   </VariableProvider>
 *
 * This fixes the crash where useResolvedParams was called outside VariableProvider.
 */
// Inner component — ASSUMES `spec` is non-null (the null-guard lives in the
// thin wrapper below). Because the early return is gone, every hook here runs
// unconditionally on every render, satisfying the Rules of Hooks even as `spec`
// transitions undefined → loaded (async fetch / embed hydration).
function SpecRendererInner({ spec, boardId: boardIdProp, initialVariables = {}, onVariableChange, onCrossFilter, forceBreakpoint, activeTabId, onTabChange, editMode = false }) {
  const cols = spec.layout?.cols ?? 12
  const rowHeight = spec.layout?.row_height ?? 60
  const allWidgets = spec.widgets ?? []

  // Warm the demo query-map + parquet manifest before widgets query, so the
  // demo-detection race (in runArrowQueryById) reliably wins on a cold remote
  // load instead of falling back to the server path and showing sample data.
  useEffect(() => { prefetchDemoData() }, [])

  // ── Auto-refresh / polling ──────────────────────────────────────────────
  // spec.refresh.interval_ms (or spec.refresh_interval_ms) enables board-level
  // polling.  The epoch counter is exposed via RefreshContext so widgets can
  // include it in their fetch deps and re-query on each tick.
  const refreshIntervalMs = spec.refresh?.interval_ms ?? spec.refresh_interval_ms ?? null
  const [refreshEpoch, setRefreshEpoch] = useState(0)
  useAutoRefresh({
    intervalMs: refreshIntervalMs,
    onRefresh:  () => setRefreshEpoch(e => e + 1),
    enabled:    !editMode,   // never auto-refresh in the editor canvas
  })

  const colsByBp = {
    lg: cols,
    md: spec.layout?.cols_md ?? cols,
    sm: spec.layout?.cols_sm ?? 1,
  }

  // boardId: prefer the explicit prop (from DashboardViewPage) and fall back to
  // spec._boardId (for embed contexts that inject it into the spec object).
  const boardId = boardIdProp ?? spec._boardId ?? null

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

  // SpecRendererBody (rendered as a child of VariableProvider) handles all hooks
  // that require VariableValuesContext: useResolvedParams, useProviderData, plus
  // the full render/return tree.
  return (
    <VariableProvider initialValues={variableDefaults} onVariableChange={onVariableChange}>
      <SpecRendererBody
        spec={spec}
        boardId={boardId}
        allWidgets={allWidgets}
        cols={cols}
        colsByBp={colsByBp}
        rowHeight={rowHeight}
        refreshEpoch={refreshEpoch}
        forceBreakpoint={forceBreakpoint}
        activeTabId={activeTabId}
        onTabChange={onTabChange}
        onCrossFilter={onCrossFilter}
        editMode={editMode}
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
      <div className="py-16 border-2 border-dashed border-border rounded-2xl">
        <EmptyState title="No spec provided." description="A dashboard spec is required to render this view." compact />
      </div>
    )
  }
  return <SpecRendererInner {...props} />
}
