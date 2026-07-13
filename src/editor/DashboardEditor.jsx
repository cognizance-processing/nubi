/**
 * DashboardEditor.jsx — Drag-and-drop DashboardSpec editor (Wave EDITOR-3B).
 *
 * GRID ENGINE: dnd-kit + native CSS Grid via the headless <GridCanvas> (see
 * src/dashboards/grid/). This replaces the old react-grid-layout <GridLayout> +
 * createScaledStrategy. GridCanvas owns all drag/resize math and zoom scaling; the
 * editor only supplies the layout + commit callbacks and owns history.
 *
 * What's new vs EDITOR-3A
 * ------------------------
 * 1. CSS-Grid engine (GridCanvas) — desktop/tablet free-drag + 8-handle resize
 *    (mode="grid"), mobile drag-to-reorder stack (mode="reorder") with a height
 *    stepper instead of tiny corner handles.
 * 2. Zoom controls + Reset moved into the app top bar; below md the whole toolbar
 *    cluster collapses behind a hamburger that opens a slide-out sidebar.
 * 3. Configure-dashboard panel expanded: per-device columns, row height, gap, and
 *    an Advanced group (compaction + dense, padding, breakpoint thresholds, max
 *    content width).
 *
 * PRESERVED — THE COMMIT CONTRACT (DO NOT TOUCH):
 *   - GridCanvas.onLayoutCommit(finalLayout) → commitLayout(finalLayout), which
 *     routes via deviceRef/activeBreakpoint through applyLayoutCommit
 *     (lg → widget.pos, md/sm → spec.responsive[bp]), with the no-op JSON-equality
 *     skip so mount/device-switch re-renders don't pollute history or bake
 *     fallback layouts into overrides.
 *   - onInteractionStart/End → handleInteractionStart/End (isDraggingRef +
 *     frozenLayoutsRef freeze while dragging).
 *   - The arrow-key nudge handler + ConfigPanel numeric x/y/w/h fields still call
 *     commitLayout([item]) directly (not via GridCanvas) — unchanged.
 */

import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  memo,
} from 'react'
import { createPortal } from 'react-dom'
import { useUi } from '../contexts/UiContext.jsx'
import {
  PanelRightClose, PanelRightOpen, Plus, Trash2, X, GripVertical,
  Database, SlidersHorizontal, Palette, Code2, TrendingUp, Settings2,
  BarChart3, LineChart, AreaChart, ScatterChart, PieChart, Gauge, Grid3x3,
  BarChartHorizontal, Table2, Hash, Filter as FilterIcon, Type, Heading,
  Monitor, Tablet, Smartphone, ChevronDown, Settings, LayoutGrid, MessageSquare,
  ZoomIn, ZoomOut, Maximize2, Menu, ChevronUp, Sigma, FileCode2, LayoutDashboard,
  Undo2, Redo2, Eye, Pencil, Save, Loader2, CheckCircle2,
} from 'lucide-react'
import Button from '../components/ui/Button.jsx'
import Spinner from '../components/ui/Spinner.jsx'
import { toast } from '../components/ui/Toast.jsx'

// Device viewport presets for the editor's responsive preview/edit switcher.
const DEVICES = [
  { id: 'desktop', label: 'Desktop', Icon: Monitor, width: null },
  { id: 'tablet', label: 'Tablet', Icon: Tablet, width: 834 },
  { id: 'mobile', label: 'Mobile', Icon: Smartphone, width: 390 },
]
const DEVICE_WIDTHS = { desktop: null, tablet: 834, mobile: 390 }
// Canvas zoom bounds + the design-width floor for the desktop frame (so the full
// desktop grid stays visible/editable — zoomed out — on small screens).
const MIN_ZOOM = 0.25
const MAX_ZOOM = 2
const DESKTOP_MIN_WIDTH = 1024
// Common device widths offered as quick chips when editing tablet/mobile.
const WIDTH_PRESETS = [390, 412, 768, 834, 1024]

// Icon maps shared across the palette, chart-type grid, and config header.
const WIDGET_ICONS = {
  kpi: Hash, metric: TrendingUp, chart: BarChart3, table: Table2,
  pivot: Grid3x3, filter: FilterIcon, text: Type, section: Heading,
}
const CHART_ICONS = {
  line: LineChart, bar: BarChart3, hbar: BarChartHorizontal, scatter: ScatterChart,
  area: AreaChart, pie: PieChart, donut: PieChart, heatmap: Grid3x3, gauge: Gauge,
}
import { GridCanvas } from '../dashboards/grid/index.js'
import { get, post, put, listRegisteredQueries } from '../lib/api.js'
import ChartWidget from '../dashboards/widgets/ChartWidget.jsx'
import KpiWidget from '../dashboards/widgets/KpiWidget.jsx'
import TableWidget from '../dashboards/widgets/TableWidget.jsx'
import FilterWidget from '../dashboards/widgets/FilterWidget.jsx'
import TextWidget from '../dashboards/widgets/TextWidget.jsx'
import HtmlWidget from '../dashboards/widgets/HtmlWidget.jsx'
import MetricWidget from '../dashboards/widgets/MetricWidget.jsx'
import MetricPicker from '../components/app/MetricPicker.jsx'
import PivotWidget from '../dashboards/widgets/PivotWidget.jsx'
import SectionWidget from '../dashboards/widgets/SectionWidget.jsx'
import ExportShareMenu from '../components/ExportShareMenu.jsx'
import DashboardCodeView from './DashboardCodeView.jsx'
import { VariableProvider } from '../dashboards/VariableStore.jsx'
import SpecRenderer from '../dashboards/SpecRenderer.jsx'
import { backgroundToCss, styleToCss } from '../dashboards/widgetHtml.js'
import TabBar from '../dashboards/TabBar.jsx'
import {
  DEVICE_TO_BREAKPOINT,
  buildResponsiveLayouts,
  applyLayoutCommit,
  clearBreakpointOverrides,
  hasOverrides,
  effectivePos,
  isHiddenAt,
} from '../dashboards/responsiveLayout.js'
import ChatPanel from './ChatPanel.jsx'
import {
  createHistory,
  push as historyPush,
  undo as historyUndo,
  redo as historyRedo,
  canUndo,
  canRedo,
} from './history.js'
// ── Shared inspector panels ──────────────────────────────────────────────────
import {
  inputCls, selectCls, FieldLabel, SectionLabel, Section, ToggleRow, ChipRow, ColumnSelect,
  useColumnIntrospection, useMetricsList,
  QueryPicker, BackgroundEditor, ParamBindingSection,
  ChartConfig, KpiConfig,
  TableConfig, ColumnFormatsEditor, ConditionalRulesEditor,
  FilterConfig, TextConfig, PlacementControl, effectivePlacement,
  DEMO_QUERY_IDS, CHART_TYPES, SERIES_TYPES, FILTER_SUBTYPES, VARIABLE_TYPES,
  FORMAT_OPS, COLUMN_FORMAT_TYPES, BACKGROUND_TYPES, PIVOT_AGGS,
} from './shared/index.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// NOTE: DEMO_QUERY_IDS, CHART_TYPES, SERIES_TYPES, FILTER_SUBTYPES, VARIABLE_TYPES,
// FORMAT_OPS, COLUMN_FORMAT_TYPES, BACKGROUND_TYPES, PIVOT_AGGS are now imported
// from ./shared/constants.js (via ./shared/index.js above).
// react-grid-layout compaction modes exposed in the Dashboard panel.
const COMPACTION_MODES = [
  { id: 'free',       label: 'Free place', hint: 'Keep widgets exactly where placed' },
  { id: 'vertical',   label: 'Vertical',   hint: 'Pack upward' },
  { id: 'horizontal', label: 'Horizontal', hint: 'Pack leftward' },
  { id: 'none',       label: 'None',       hint: 'No compaction (collisions allowed)' },
]

const DEFAULT_SPEC = {
  version: 1,
  title: 'New Dashboard',
  layout: { cols: 12, row_height: 60 },
  variables: [],
  widgets: [],
}

// Minimum sizes per type (in grid cells)
const WIDGET_MIN_SIZES = {
  kpi:     { minW: 2, minH: 2 },
  metric:  { minW: 2, minH: 2 },
  table:   { minW: 3, minH: 3 },
  pivot:   { minW: 3, minH: 3 },
  chart:   { minW: 3, minH: 3 },
  filter:  { minW: 2, minH: 2 },
  text:    { minW: 2, minH: 2 },
  section: { minW: 2, minH: 1 },
}

const COLUMN_OPTIONS = [6, 8, 12, 16, 24]
// Mobile/tablet column presets — mobile is no longer hard-locked to a single
// column (the historical default of 1 stays available + is the fallback).
const COLUMN_OPTIONS_SM = [1, 2, 3, 4, 6]
const COLUMN_OPTIONS_MD = [4, 6, 8, 12]
const ROW_HEIGHT_OPTIONS = [40, 60, 80, 100]
const GAP_OPTIONS = [0, 8, 12, 16, 24]

// Default breakpoint width thresholds (mirror GridCanvas DEFAULT_BREAKPOINTS /
// SpecRenderer's breakpoints). Authors can override per-spec in the Advanced group.
const DEFAULT_BREAKPOINTS_PX = { lg: 1200, md: 768, sm: 480 }

// ---------------------------------------------------------------------------
// Helpers: spec <-> RGL layout conversions
// ---------------------------------------------------------------------------

// NOTE: spec ↔ RGL conversion now lives in ../dashboards/responsiveLayout.js
// (shared with SpecRenderer) so per-breakpoint overrides are handled in one place.

let _idCounter = 0
function genId(type) {
  _idCounter += 1
  return `${type}_${_idCounter}`
}

// ---------------------------------------------------------------------------
// Tab helpers (Track T — T5 editor)
// ---------------------------------------------------------------------------

// Tab-bar style tokens exposed in the inspector. ALL user-supplied colors / CSS
// flow through the sanitized styleToCss/parseCssString path (never raw-injected).
const TAB_VARIANTS = ['underline', 'pills', 'segmented']
const TAB_ALIGNS = ['start', 'center', 'end', 'stretch']
const TAB_SIZES = ['sm', 'md', 'lg']

// effectivePlacement is now imported from ./shared/index.js

/** Filter widgets eligible for the above-grid filter bar (placement → 'header'),
 *  scoped to a tab like widgetsForTab, then ordered by widget.order ascending. */
function headerWidgetsForTab(spec, activeTabId) {
  const scoped = widgetsForTab(spec, activeTabId).filter(w => effectivePlacement(w) === 'header')
  return [...scoped].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
}

/** Generate a stable, collision-free tab id within the current tab list. */
function genTabId(existing = []) {
  const used = new Set(existing.map(t => t.id))
  let n = existing.length + 1
  let id = `t${n}`
  while (used.has(id)) { n += 1; id = `t${n}` }
  return id
}

/** The effective active tab id given the spec + a requested id (falls back to first). */
function resolveActiveTab(spec, requested) {
  const tabs = Array.isArray(spec.tabs) ? spec.tabs : []
  if (tabs.length === 0) return null
  if (requested && tabs.some(t => t.id === requested)) return requested
  return tabs[0].id
}

/**
 * Filter widgets down to a single tab for the canvas (mirrors SpecRenderer):
 *   - no tabs → every widget (today's behavior)
 *   - widget.tab_id === activeTabId → in
 *   - widget.tab_id null/absent → belongs to the FIRST tab
 */
function widgetsForTab(spec, activeTabId) {
  const tabs = Array.isArray(spec.tabs) ? spec.tabs : []
  if (tabs.length === 0) return spec.widgets
  const firstTabId = tabs[0]?.id ?? null
  return spec.widgets.filter(w => {
    const t = w.tab_id ?? null
    if (t === activeTabId) return true
    return t == null && activeTabId === firstTabId
  })
}

// ---------------------------------------------------------------------------
// useElementWidth — local ResizeObserver width hook
// ---------------------------------------------------------------------------

// Replaces RGL's useContainerWidth (which lived in react-grid-layout). Tracks the
// measured pixel width of a ref'd element; GridCanvas itself does NOT need this for
// placement (columns are 1fr) — we use it only to compute the desktop design width
// and the auto-fit zoom.
function useElementWidth(initialWidth = 900) {
  const containerRef = useRef(null)
  const [width, setWidth] = useState(initialWidth)
  useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect?.width
      if (w && w > 0) setWidth(w)
    })
    ro.observe(el)
    // Seed immediately so the first paint isn't stuck on the initial value.
    const w0 = el.getBoundingClientRect().width
    if (w0 > 0) setWidth(w0)
    return () => ro.disconnect()
  }, [])
  return { width, containerRef }
}

// ---------------------------------------------------------------------------
// findFreeSpot
// ---------------------------------------------------------------------------

function findFreeSpot(widgets, newW, newH, cols = 12) {
  const MAX_SCAN_ROWS = 200
  const occupied = {}
  for (const w of widgets) {
    const rx = Math.max(0, (w.pos?.x ?? 1) - 1)
    const ry = Math.max(0, (w.pos?.y ?? 1) - 1)
    const rw = w.pos?.w ?? 4
    const rh = w.pos?.h ?? 4
    for (let row = ry; row < ry + rh; row++) {
      for (let col = rx; col < rx + rw; col++) {
        if (!occupied[row]) occupied[row] = {}
        occupied[row][col] = true
      }
    }
  }

  const fits = (rx, ry, rw, rh) => {
    if (rx + rw > cols) return false
    for (let row = ry; row < ry + rh; row++) {
      for (let col = rx; col < rx + rw; col++) {
        if (occupied[row]?.[col]) return false
      }
    }
    return true
  }

  for (let row = 0; row < MAX_SCAN_ROWS; row++) {
    for (let col = 0; col <= cols - newW; col++) {
      if (fits(col, row, newW, newH)) return { x: col + 1, y: row + 1 }
    }
  }

  const maxBottom = widgets.reduce((m, w) => {
    const bottom = (w.pos?.y ?? 1) - 1 + (w.pos?.h ?? 4)
    return Math.max(m, bottom)
  }, 0)
  return { x: 1, y: maxBottom + 1 }
}

// ---------------------------------------------------------------------------
// Default widget factories
// ---------------------------------------------------------------------------

const WIDGET_SIZES = {
  kpi:     { w: 3, h: 3 },
  metric:  { w: 3, h: 3 },
  table:   { w: 6, h: 5 },
  pivot:   { w: 6, h: 5 },
  chart:   { w: 6, h: 6 },
  filter:  { w: 3, h: 2 },
  text:    { w: 6, h: 3 },
  section: { w: 12, h: 1 },
}

function makeKpiWidget(pos) {
  return {
    id: genId('kpi'), type: 'kpi', query_id: 'demo_all',
    chart_type: null, encoding: { value: '' }, props: { label: 'KPI', format: 'number' },
    pos: { ...WIDGET_SIZES.kpi, ...pos },
  }
}
function makeTableWidget(pos) {
  return {
    id: genId('table'), type: 'table', query_id: 'demo_all',
    chart_type: null, encoding: {}, props: { limit: 50, columns: '' },
    pos: { ...WIDGET_SIZES.table, ...pos },
  }
}
function makeChartWidget(pos) {
  return {
    id: genId('chart'), type: 'chart', query_id: 'demo_all',
    chart_type: 'bar', encoding: { x: '', y: '', color: '' }, props: {}, params: {},
    pos: { ...WIDGET_SIZES.chart, ...pos },
  }
}
function makeFilterWidget(pos) {
  return {
    id: genId('filter'), type: 'filter', subtype: 'select', target_var: '',
    options_query_id: '', query_id: null, props: { label: 'Filter' },
    pos: { ...WIDGET_SIZES.filter, ...pos },
  }
}
function makeTextWidget(pos) {
  return {
    id: genId('text'), type: 'text',
    content: '## Heading\n\nAdd your markdown content here.',
    query_id: null, pos: { ...WIDGET_SIZES.text, ...pos },
  }
}
function makeMetricWidget(pos) {
  return {
    id: genId('metric'), type: 'metric', query_id: 'demo_all',
    chart_type: null, encoding: { value: '', compare: '', spark: '' },
    props: { label: 'Metric', format: 'number', deltaFormat: 'percent' }, params: {},
    pos: { ...WIDGET_SIZES.metric, ...pos },
  }
}
function makePivotWidget(pos) {
  return {
    id: genId('pivot'), type: 'pivot', query_id: 'demo_all',
    chart_type: null, encoding: { rows: '', cols: '', value: '' },
    props: { agg: 'sum' }, params: {},
    pos: { ...WIDGET_SIZES.pivot, ...pos },
  }
}
function makeSectionWidget(pos) {
  return {
    id: genId('section'), type: 'section', query_id: null,
    props: { title: 'Section', subtitle: '', align: 'left', divider: true },
    pos: { ...WIDGET_SIZES.section, ...pos },
  }
}

function makeWidget(type, pos) {
  if (type === 'kpi') return makeKpiWidget(pos)
  if (type === 'metric') return makeMetricWidget(pos)
  if (type === 'table') return makeTableWidget(pos)
  if (type === 'pivot') return makePivotWidget(pos)
  if (type === 'filter') return makeFilterWidget(pos)
  if (type === 'text') return makeTextWidget(pos)
  if (type === 'section') return makeSectionWidget(pos)
  return makeChartWidget(pos)
}

// ---------------------------------------------------------------------------
// Shared input classes
// ---------------------------------------------------------------------------
// NOTE: inputCls, selectCls, FieldLabel are now imported from ./shared/index.js

// ---------------------------------------------------------------------------
// QueryPicker
// ---------------------------------------------------------------------------
// NOTE: QueryPicker is now imported from ./shared/index.js

// ---------------------------------------------------------------------------
// useMetricsList — now imported from ./shared/index.js
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// MetricBindingSection — bind a data widget to a GOVERNED metric (alternative
// to query_id). Sets/clears `widget.metric` = { metric_id, dimensions,
// time_grain, filters }. query_id stays intact; metric binding is additive and
// takes precedence in the runtime (runMetricQuery) when present.
// ---------------------------------------------------------------------------

function MetricBindingSection({ widget, onChange }) {
  const metrics = useMetricsList()
  const binding = widget.metric ?? null

  const setBinding = (next) => {
    if (next && next.metric_id) {
      onChange({ ...widget, metric: next })
    } else {
      // Clearing the metric → drop the binding so query_id takes over again.
      const { metric, ...rest } = widget // eslint-disable-line no-unused-vars
      onChange(rest)
    }
  }

  return (
    <div className="space-y-2">
      <MetricPicker metrics={metrics} value={binding} onChange={setBinding} />
      {binding?.metric_id && (
        <p className="text-[10px] text-muted/70 leading-snug">
          Bound to a governed metric — this takes precedence over the query above.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// useColumnIntrospection — now imported from ./shared/index.js
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ColumnSelect — now imported from ./shared/index.js
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// ChartConfig / KpiConfig / TableConfig / FilterConfig / TextConfig
// ---------------------------------------------------------------------------
// NOTE: SectionLabel, Section, ToggleRow are now imported from ./shared/index.js
// NOTE: ChartConfig, KpiConfig, TableConfig, FilterConfig, TextConfig are now imported from ./shared/index.js

// NOTE: ChartConfig, KpiConfig, TableConfig, ColumnFormatsEditor, ConditionalRulesEditor,
// FilterConfig, TextConfig, PlacementControl, applyPlacement, effectivePlacement are all
// imported from ./shared/index.js

function PivotConfig({ widget, onChange }) {
  const { columns, introspecting } = useColumnIntrospection(widget.query_id)
  const enc = widget.encoding ?? {}
  const props = widget.props ?? {}
  const setEncoding = (key, val) => onChange({ ...widget, encoding: { ...enc, [key]: val } })
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })
  return (
    <div className="space-y-3">
      <Section title="Pivot dimensions" icon={Grid3x3}>
        {introspecting && <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>}
        <ColumnSelect label="Rows (dimension)" value={enc.rows} onChange={v => setEncoding('rows', v)} columns={columns} />
        <ColumnSelect label="Columns (dimension)" value={enc.cols} onChange={v => setEncoding('cols', v)} columns={columns} />
        <ColumnSelect label="Value (measure)" value={enc.value} onChange={v => setEncoding('value', v)} columns={columns} optional />
        <div>
          <FieldLabel>Aggregation</FieldLabel>
          <select className={selectCls} value={props.agg ?? 'sum'} onChange={e => setProps('agg', e.target.value)}>
            {PIVOT_AGGS.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
          <p className="text-[10px] text-muted/70 mt-1">With no value column, cells show the row count.</p>
        </div>
      </Section>
    </div>
  )
}

function SectionConfig({ widget, onChange }) {
  const props = widget.props ?? {}
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })
  const align = props.align ?? 'left'
  return (
    <div className="space-y-3">
      <div>
        <FieldLabel>Title</FieldLabel>
        <input type="text" className={inputCls} value={props.title ?? ''} placeholder="Section title" onChange={e => setProps('title', e.target.value)} />
      </div>
      <div>
        <FieldLabel>Subtitle</FieldLabel>
        <input type="text" className={inputCls} value={props.subtitle ?? ''} placeholder="Optional subtitle" onChange={e => setProps('subtitle', e.target.value)} />
      </div>
      <div>
        <FieldLabel>Alignment</FieldLabel>
        <div className="flex h-8 rounded-lg border border-border overflow-hidden">
          {['left', 'center', 'right'].map(a => (
            <button key={a} onClick={() => setProps('align', a)}
              className={`flex-1 text-[11px] font-medium capitalize transition-colors ${align === a ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary'}`}>{a}</button>
          ))}
        </div>
      </div>
      <ToggleRow label="Show divider line" checked={props.divider !== false} onChange={v => setProps('divider', v)} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// DrilldownSection — chart click sets a target variable (cross-widget filter)
// ---------------------------------------------------------------------------

function DrilldownSection({ widget, onChange }) {
  const dd = widget.drilldown ?? {}
  const enabled = !!dd.target_var
  const enc = widget.encoding ?? {}
  // Candidate value fields = the chart's encoding columns (x is the usual one).
  const fieldOptions = [enc.x, enc.color, enc.value, typeof enc.y === 'string' ? enc.y : null].filter(Boolean)
  const setDD = (patch) => {
    const next = { ...dd, ...patch }
    onChange({ ...widget, drilldown: next.target_var ? next : undefined })
  }
  return (
    <Section title="Drilldown / cross-filter" defaultOpen={false}
      right={enabled ? <span className="text-[10px] text-primary font-medium">on</span> : null}>
      <p className="text-[10px] text-muted/70 leading-relaxed">
        Make this chart a filter source. Clicking a data point sets a dashboard
        variable; other widgets bound to it (via Parameters) re-query.
      </p>
      <ToggleRow label="Enable click-to-filter" checked={enabled}
        onChange={v => setDD({ target_var: v ? (dd.target_var || '') : '' })} />
      {enabled && (
        <>
          <div>
            <FieldLabel>Target variable</FieldLabel>
            <input type="text" placeholder="e.g. region" className={inputCls}
              value={dd.target_var ?? ''} onChange={e => setDD({ target_var: e.target.value })} />
          </div>
          <div>
            <FieldLabel>Value field (optional)</FieldLabel>
            <select className={selectCls} value={dd.value_field ?? ''} onChange={e => setDD({ value_field: e.target.value || undefined })}>
              <option value="">— clicked category (x) —</option>
              {fieldOptions.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
            <p className="text-[10px] text-muted/70 mt-1">Defaults to the clicked point's category value.</p>
          </div>
        </>
      )}
    </Section>
  )
}

// ---------------------------------------------------------------------------
// WidgetLayoutSection — per-breakpoint position/size + visibility + constraints
// ---------------------------------------------------------------------------

const BP_LABEL = { lg: 'Desktop', md: 'Tablet', sm: 'Mobile' }
const ALL_BREAKPOINTS = ['lg', 'md', 'sm']

function WidgetLayoutSection({ widget, onChange, spec, activeBreakpoint = 'lg', onLayoutCommit }) {
  const pos = widget.pos ?? {}
  const setPos = (patch) => {
    const next = { ...pos, ...patch }
    Object.keys(next).forEach(k => { if (next[k] === '' || next[k] == null) delete next[k] })
    onChange({ ...widget, pos: next })
  }
  const numField = (key, label, placeholder) => (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <input type="number" min={1} className={inputCls} placeholder={placeholder}
        value={pos[key] ?? ''} onChange={e => setPos({ [key]: e.target.value === '' ? undefined : (parseInt(e.target.value, 10) || undefined) })} />
    </div>
  )

  // Position & size for the ACTIVE breakpoint — edits route through the same
  // single-breakpoint commit path as drag/resize (lg → widget.pos,
  // tablet/mobile → spec.responsive[bp]).
  const eff = effectivePos(widget, spec, activeBreakpoint) ?? {}
  const base = { x: eff.x ?? 1, y: eff.y ?? 1, w: eff.w ?? 4, h: eff.h ?? 4 }
  const setLayoutField = (key, raw) => {
    const v = Math.max(1, parseInt(raw, 10) || 1)
    const next = { ...base, [key]: v }
    onLayoutCommit?.({ i: widget.id, x: next.x - 1, y: next.y - 1, w: next.w, h: next.h })
  }
  const layoutField = (key, label) => (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <input type="number" min={1} className={inputCls}
        value={base[key]} onChange={e => setLayoutField(key, e.target.value)} />
    </div>
  )

  // Per-breakpoint visibility (widget.hidden = ['lg'|'md'|'sm', …]).
  const hidden = Array.isArray(widget.hidden) ? widget.hidden : []
  const toggleHidden = (bp, on) => {
    const next = on ? [...hidden.filter(b => b !== bp), bp] : hidden.filter(b => b !== bp)
    onChange({ ...widget, hidden: next.length ? next : undefined })
  }

  return (
    <Section title="Layout & size" defaultOpen={false}>
      <FieldLabel>Position &amp; size · {BP_LABEL[activeBreakpoint]}</FieldLabel>
      <div className="grid grid-cols-4 gap-2">
        {layoutField('x', 'X')}
        {layoutField('y', 'Y')}
        {layoutField('w', 'W')}
        {layoutField('h', 'H')}
      </div>
      {activeBreakpoint !== 'lg' && (
        <p className="text-[10px] text-muted/60 -mt-1">Edits apply to {BP_LABEL[activeBreakpoint]} only.</p>
      )}

      <div className="pt-1">
        <FieldLabel>Visibility</FieldLabel>
        {ALL_BREAKPOINTS.map(bp => (
          <ToggleRow key={bp} label={`Hide on ${BP_LABEL[bp]}`}
            checked={hidden.includes(bp)} onChange={v => toggleHidden(bp, v)} />
        ))}
      </div>

      <div className="pt-1">
        <ToggleRow label="Static (pin in place)" hint="Cannot be dragged or resized"
          checked={!!pos.static} onChange={v => setPos({ static: v || undefined })} />
        <div className="grid grid-cols-2 gap-2">
          {numField('minW', 'Min width', 'cells')}
          {numField('minH', 'Min height', 'cells')}
          {numField('maxW', 'Max width', 'cells')}
          {numField('maxH', 'Max height', 'cells')}
        </div>
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------
// ParamBindingSection — now imported from ./shared/index.js
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// VariablesEditor
// ---------------------------------------------------------------------------

function VariablesEditor({ variables, onChange }) {
  const vars = variables ?? []
  const addVar = () => onChange([...vars, { name: `var${vars.length + 1}`, type: 'text', default: '' }])
  const removeVar = idx => onChange(vars.filter((_, i) => i !== idx))
  const setVar = (idx, key, val) => onChange(vars.map((v, i) => i === idx ? { ...v, [key]: val } : v))

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <SectionLabel>Dashboard variables</SectionLabel>
        <button onClick={addVar}
          className="text-[11px] font-medium px-2 h-6 rounded-lg border border-dashed border-border hover:border-primary text-muted hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
          + Add
        </button>
      </div>
      {vars.length === 0 && <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center">No variables defined.</p>}
      {vars.map((v, idx) => (
        <div key={idx} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface-2">
          <div className="flex items-center gap-1.5">
            <input type="text" className={`${inputCls} flex-1 font-mono text-xs`} placeholder="name"
              value={v.name} onChange={e => setVar(idx, 'name', e.target.value)} />
            <button onClick={() => removeVar(idx)}
              className="text-xs px-1.5 py-0.5 rounded border border-transparent hover:border-border text-muted hover:text-fg transition-colors" title="Remove variable">✕</button>
          </div>
          <div className="flex gap-1.5">
            <select className={`${selectCls} flex-1`} value={v.type ?? 'text'} onChange={e => setVar(idx, 'type', e.target.value)}>
              {VARIABLE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <input type="text" className={`${inputCls} flex-1`} placeholder="default"
              value={v.default ?? ''} onChange={e => setVar(idx, 'default', e.target.value)} />
          </div>
          <ToggleRow label="Bind to URL" checked={!!v.url_bind} onChange={val => setVar(idx, 'url_bind', val || undefined)} />
        </div>
      ))}
      <p className="text-[10px] text-muted/70 leading-relaxed">
        URL-bound variables are seeded from <span className="font-mono text-muted">/d/:id?var=value</span> (URL wins
        over the default) and filter changes update the URL so the view is shareable &amp; refresh-safe.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConfigPanel
// ---------------------------------------------------------------------------

// BackgroundEditor is now imported from ./shared/index.js

// ---------------------------------------------------------------------------
// WidgetAppearanceSection — widget.style + widget.html (full HTML flexibility)
// ---------------------------------------------------------------------------

const HTML_CHEATSHEET = '{{value}} · {{col:NAME}} · {{row.0.NAME}} · {{prop:NAME}}'

function WidgetAppearanceSection({ widget, onChange }) {
  const style = widget.style ?? {}
  const setStyle = (patch) => {
    const next = { ...style, ...patch }
    Object.keys(next).forEach(k => { if (next[k] === '' || next[k] == null) delete next[k] })
    onChange({ ...widget, style: Object.keys(next).length ? next : undefined })
  }
  const setBg = (bgVal) => {
    const hasBg = bgVal && bgVal.type
    setStyle({ background: hasBg ? bgVal : '' })
  }
  return (
    <div className="space-y-3">
      <Section title="Appearance" defaultOpen={false} icon={Palette}>
        <div className="space-y-1">
          <SectionLabel>Card background</SectionLabel>
          <BackgroundEditor value={typeof style.background === 'object' ? style.background : undefined} onChange={setBg} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <FieldLabel>Border</FieldLabel>
            <input type="text" className={inputCls} placeholder="1px solid #333" value={typeof style.border === 'string' ? style.border : ''} onChange={e => setStyle({ border: e.target.value })} />
          </div>
          <div>
            <FieldLabel>Radius</FieldLabel>
            <input type="text" className={inputCls} placeholder="12px" value={style.borderRadius ?? ''} onChange={e => setStyle({ borderRadius: e.target.value })} />
          </div>
        </div>
        <div>
          <FieldLabel>Padding</FieldLabel>
          <input type="text" className={inputCls} placeholder="8px 12px" value={style.padding ?? ''} onChange={e => setStyle({ padding: e.target.value })} />
        </div>
      </Section>

      <Section title="Custom HTML" defaultOpen={false}
        right={widget.html ? <span className="text-[10px] text-primary font-medium">active</span> : null}>
        <p className="text-[10px] text-muted/70 leading-relaxed">
          Replaces the default widget body with your own sanitized HTML. Tokens pull live query data:
          <span className="block font-mono text-muted mt-0.5">{HTML_CHEATSHEET}</span>
        </p>
        <textarea rows={6} className={`${inputCls} h-auto py-1.5 font-mono text-xs resize-y`}
          placeholder={'<div class="p-4">\n  <h2>{{prop:label}}</h2>\n  <p class="text-2xl">{{value}}</p>\n</div>'}
          value={widget.html ?? ''} onChange={e => onChange({ ...widget, html: e.target.value || undefined })} />
        {widget.html && (
          <button onClick={() => onChange({ ...widget, html: undefined })}
            className="text-xs text-muted hover:text-red-500 transition-colors">Clear custom HTML → use default widget</button>
        )}
      </Section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DashboardPanel — dashboard-level settings: background, grid, variables
// ---------------------------------------------------------------------------

// ChipRow is now imported from ./shared/index.js

function DashboardPanel({ spec, onSpecChange }) {
  const layout = spec.layout ?? {}
  // Per-device column counts. Desktop = `cols` (canonical, default 12); tablet =
  // `cols_md` (falls back to desktop); mobile = `cols_sm` (falls back to the
  // historical single column). Mobile is no longer hard-locked to 1.
  const cols = layout.cols ?? 12
  const colsMd = layout.cols_md ?? cols
  const colsSm = layout.cols_sm ?? 1
  const rowHeight = layout.row_height ?? 60
  // Single scalar gap (CSS Grid `gap`, both axes) — GridCanvas takes one number.
  // Falls back to the legacy margin_x so old specs keep their gutters.
  const gap = layout.gap ?? layout.margin_x ?? 12
  const dense = !!layout.dense
  const bp = { ...DEFAULT_BREAKPOINTS_PX, ...(layout.breakpoints ?? {}) }

  const setLayout = (patch) => onSpecChange({ ...spec, layout: { ...spec.layout, ...patch } })
  const setBreakpoint = (key, raw) => {
    const v = parseInt(raw, 10)
    setLayout({ breakpoints: { ...bp, [key]: Number.isFinite(v) ? v : DEFAULT_BREAKPOINTS_PX[key] } })
  }
  return (
    <div className="p-4 space-y-3 overflow-y-auto h-full">
      <h3 className="text-sm font-semibold text-fg">Dashboard</h3>

      <Section title="Background" icon={Palette}>
        <BackgroundEditor value={spec.background} onChange={bg => onSpecChange({ ...spec, background: bg && bg.type ? bg : undefined })} />
      </Section>

      <Section title="Grid" icon={Grid3x3}>
        {/* Columns per device — independent counts (mobile no longer locked to 1). */}
        <div className="space-y-1">
          <SectionLabel>Columns · Desktop</SectionLabel>
          <ChipRow options={COLUMN_OPTIONS} value={cols} onChange={c => setLayout({ cols: c })} />
        </div>
        <div className="space-y-1">
          <SectionLabel>Columns · Tablet</SectionLabel>
          <ChipRow options={COLUMN_OPTIONS_MD} value={colsMd} onChange={c => setLayout({ cols_md: c })} />
        </div>
        <div className="space-y-1">
          <SectionLabel>Columns · Mobile</SectionLabel>
          <ChipRow options={COLUMN_OPTIONS_SM} value={colsSm} onChange={c => setLayout({ cols_sm: c })} />
        </div>
        <div className="space-y-1">
          <SectionLabel>Row height</SectionLabel>
          <ChipRow options={ROW_HEIGHT_OPTIONS} value={rowHeight} onChange={rh => setLayout({ row_height: rh })} />
        </div>
        <div className="space-y-1">
          <SectionLabel>Gap (px)</SectionLabel>
          <ChipRow options={GAP_OPTIONS} value={gap} onChange={g => setLayout({ gap: g })} />
        </div>
      </Section>

      <Section title="Advanced" defaultOpen={false} icon={SlidersHorizontal}>
        {/* Compaction + dense packing */}
        <div className="space-y-1">
          <SectionLabel>Compaction mode</SectionLabel>
          <div className="grid grid-cols-2 gap-1.5">
            {COMPACTION_MODES.map(m => {
              const active = (layout.compaction ?? 'free') === m.id
              return (
                <button key={m.id} onClick={() => setLayout({ compaction: m.id })} title={m.hint}
                  className={`px-2 py-1.5 text-xs font-medium rounded-lg border text-left transition-all ${active ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-fg border-border hover:border-primary hover:text-primary'}`}>
                  {m.label}
                </button>
              )
            })}
          </div>
          <p className="text-[10px] text-muted/70">{COMPACTION_MODES.find(m => m.id === (layout.compaction ?? 'free'))?.hint}</p>
          <ToggleRow label="Dense packing" hint="Back-fill gaps when packing (vertical / horizontal only)"
            checked={dense} onChange={v => setLayout({ dense: v || undefined })} />
        </div>

        {/* Container padding */}
        <div className="space-y-1">
          <SectionLabel>Container padding</SectionLabel>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <FieldLabel>Padding X (px)</FieldLabel>
              <input type="number" min={0} className={inputCls} value={layout.padding_x ?? 0}
                onChange={e => setLayout({ padding_x: parseInt(e.target.value, 10) || 0 })} />
            </div>
            <div>
              <FieldLabel>Padding Y (px)</FieldLabel>
              <input type="number" min={0} className={inputCls} value={layout.padding_y ?? 0}
                onChange={e => setLayout({ padding_y: parseInt(e.target.value, 10) || 0 })} />
            </div>
          </div>
        </div>

        {/* Breakpoint width thresholds (px) — used by the viewer to pick a layout. */}
        <div className="space-y-1">
          <SectionLabel>Breakpoint widths (px)</SectionLabel>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <FieldLabel>Desktop ≥</FieldLabel>
              <input type="number" min={0} className={inputCls} value={bp.lg} onChange={e => setBreakpoint('lg', e.target.value)} />
            </div>
            <div>
              <FieldLabel>Tablet ≥</FieldLabel>
              <input type="number" min={0} className={inputCls} value={bp.md} onChange={e => setBreakpoint('md', e.target.value)} />
            </div>
            <div>
              <FieldLabel>Mobile ≥</FieldLabel>
              <input type="number" min={0} className={inputCls} value={bp.sm} onChange={e => setBreakpoint('sm', e.target.value)} />
            </div>
          </div>
        </div>

        {/* Max content width — caps the rendered dashboard's width in the viewer. */}
        <div>
          <FieldLabel>Max content width (px)</FieldLabel>
          <input type="number" min={0} className={inputCls} placeholder="unbounded"
            value={layout.max_width ?? ''}
            onChange={e => setLayout({ max_width: e.target.value === '' ? undefined : (parseInt(e.target.value, 10) || undefined) })} />
        </div>
      </Section>

      <Section title="Variables" defaultOpen={false} icon={SlidersHorizontal}>
        <VariablesEditor variables={spec?.variables} onChange={vars => onSpecChange?.({ ...spec, variables: vars })} />
      </Section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConfigPanel — per-widget configuration
// ---------------------------------------------------------------------------

function ConfigPanel({ widget, onChange, onRemove, extraQueryIds, spec, activeBreakpoint = 'lg', onLayoutCommit, onMoveToTab }) {
  if (!widget) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-sm text-muted py-10 px-5 text-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-surface-2 flex items-center justify-center">
          <Settings2 size={18} className="text-muted/50" />
        </div>
        <div>
          <p className="text-sm font-medium text-fg/80">Nothing selected</p>
          <p className="text-xs text-muted/70 mt-1 leading-relaxed">Click a widget on the canvas to configure it.</p>
        </div>
        <p className="text-[11px] text-muted/50 leading-relaxed">
          Background, grid &amp; variables live in the <span className="text-fg/70 font-medium">Dashboard</span> tab.
        </p>
      </div>
    )
  }

  const isDataWidget = ['kpi', 'metric', 'table', 'pivot', 'chart'].includes(widget.type)
  const setQueryId = qid => {
    const next = { ...widget, query_id: qid }
    // One-time convenience: pre-fill an empty chart title from the query's
    // display name. Once the user has their own title, leave it alone.
    if (widget.type === 'chart' && !widget.config?.title) {
      const match = extraQueryIds.find(q => q.id === qid)
      if (match?.name) next.config = { ...(widget.config ?? {}), title: match.name }
    }
    onChange(next)
  }

  return (
    <div className="p-3 space-y-4 overflow-y-auto h-full">
      {/* Widget header */}
      <div className="flex items-center justify-between gap-2 pb-1 border-b border-border/60">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-fg capitalize">
          {(() => { const I = WIDGET_ICONS[widget.type]; return I ? <I size={14} className="text-primary" /> : null })()}
          {widget.type} widget
        </h3>
        <button
          onClick={onRemove}
          title="Remove widget"
          className={[
            'flex items-center gap-1 text-xs px-2 h-7 rounded-lg border border-transparent',
            'text-muted hover:text-red-500 hover:border-red-200 hover:bg-red-50/80',
            'transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400/60',
            'dark:hover:bg-red-950/40',
          ].join(' ')}
        >
          <Trash2 size={12} /> Remove
        </button>
      </div>

      {Array.isArray(spec?.tabs) && spec.tabs.length > 0 && onMoveToTab && (
        <div>
          <FieldLabel className="flex items-center gap-1.5"><LayoutGrid size={12} /> Move to tab</FieldLabel>
          <select
            className={selectCls}
            data-testid="widget-move-to-tab"
            value={widget.tab_id ?? spec.tabs[0].id}
            onChange={e => onMoveToTab(widget.id, e.target.value)}
          >
            {spec.tabs.map(t => <option key={t.id} value={t.id}>{t.label || t.id}</option>)}
          </select>
        </div>
      )}

      {isDataWidget && (
        <div className="space-y-1.5">
          <FieldLabel className="flex items-center gap-1.5"><Database size={12} /> Query</FieldLabel>
          <QueryPicker value={widget.query_id} onChange={setQueryId} extraIds={extraQueryIds} />
        </div>
      )}

      {isDataWidget && (
        <Section title="Metric binding" defaultOpen={Boolean(widget.metric?.metric_id)} icon={Sigma}>
          <MetricBindingSection widget={widget} onChange={onChange} />
        </Section>
      )}

      {widget.type === 'chart' && <ChartConfig widget={widget} onChange={onChange} />}
      {(widget.type === 'kpi' || widget.type === 'metric') && <KpiConfig widget={widget} onChange={onChange} />}
      {widget.type === 'table' && <TableConfig widget={widget} onChange={onChange} />}
      {widget.type === 'pivot' && <PivotConfig widget={widget} onChange={onChange} />}
      {widget.type === 'filter' && <FilterConfig widget={widget} onChange={onChange} />}
      {widget.type === 'text' && <TextConfig widget={widget} onChange={onChange} />}
      {widget.type === 'section' && <SectionConfig widget={widget} onChange={onChange} />}

      {widget.type === 'chart' && <DrilldownSection widget={widget} onChange={onChange} />}

      {isDataWidget && (
        <Section title="Parameters" defaultOpen={false} icon={SlidersHorizontal}>
          <ParamBindingSection widget={widget} onChange={onChange} specVariables={spec?.variables} />
        </Section>
      )}

      <WidgetLayoutSection widget={widget} onChange={onChange} spec={spec}
        activeBreakpoint={activeBreakpoint} onLayoutCommit={onLayoutCommit} />

      <WidgetAppearanceSection widget={widget} onChange={onChange} />

      <p className="text-[10px] text-muted/50 pt-1 font-mono">{widget.id}</p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AddPanel — widget palette (lives in the right sidebar's "Add" mode)
// ---------------------------------------------------------------------------

// Widget palette items — shared by AddPanel (sidebar) and the mobile bar.
const PALETTE_ITEMS = [
  { type: 'kpi',     label: 'KPI',     icon: Hash,        desc: 'Single big number' },
  { type: 'metric',  label: 'Metric',  icon: TrendingUp,  desc: 'Stat tile: value + delta + spark' },
  { type: 'table',   label: 'Table',   icon: Table2,      desc: 'Data grid' },
  { type: 'pivot',   label: 'Pivot',   icon: Grid3x3,     desc: 'Rows × cols × measure matrix' },
  { type: 'chart',   label: 'Chart',   icon: BarChart3,   desc: 'Bar / line / scatter…' },
  { type: 'filter',  label: 'Filter',  icon: FilterIcon,  desc: 'Select / date / text filter' },
  { type: 'text',    label: 'Text',    icon: Type,        desc: 'Markdown content block' },
  { type: 'section', label: 'Section', icon: Heading,     desc: 'Section header / divider' },
]

/**
 * AddPanel — the widget palette, styled to live inside the right sidebar.
 * Clicking an item appends the widget via addWidget(type) and (because
 * addWidget selects the new widget) the parent switches to the config tab.
 */
function AddPanel({ onAdd }) {
  return (
    <div className="p-3 space-y-3 overflow-y-auto h-full">
      <p className="text-xs text-muted/70 leading-relaxed px-0.5">
        Pick a widget type to add it to the canvas.
      </p>
      <div className="space-y-1.5">
        {PALETTE_ITEMS.map(item => {
          const Icon = item.icon
          return (
            <button
              key={item.type}
              onClick={() => onAdd(item.type)}
              data-testid={`palette-add-${item.type}`}
              className={[
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border border-border',
                'bg-surface hover:bg-surface-2/70 hover:border-primary/60 text-fg',
                'transition-all duration-150 group text-left',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                'active:scale-[0.98]',
              ].join(' ')}
            >
              <span className="w-8 h-8 shrink-0 flex items-center justify-center rounded-lg bg-surface-2 text-muted group-hover:text-primary group-hover:bg-primary/10 transition-all duration-150 shadow-sm">
                <Icon size={15} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg group-hover:text-primary transition-colors duration-150 leading-tight">{item.label}</p>
                <p className="text-[11px] text-muted/80 truncate mt-0.5">{item.desc}</p>
              </div>
              <svg
                className="w-3.5 h-3.5 ml-auto shrink-0 text-muted/30 group-hover:text-primary/60 transition-all duration-150 translate-x-0 group-hover:translate-x-0.5"
                viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"
              >
                <path d="M7 3v8M3 7h8" />
              </svg>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Live widget preview — memoised so it won't re-mount during drag/resize
// ---------------------------------------------------------------------------

/**
 * Normalise a raw spec widget into the shape expected by each widget component.
 * (Mirrors SpecRenderer's normalizeWidget.)
 */
function normalizeWidget(raw) {
  const existing = raw.props ?? {}
  const merged = {
    subtype:     raw.subtype     ?? existing.subtype,
    target_var:  raw.target_var  ?? existing.target_var,
    content:     raw.content     ?? existing.content,
    label:       raw.label       ?? existing.label,
    placeholder: raw.placeholder ?? existing.placeholder,
    ...existing,
  }
  return { ...raw, props: merged }
}

/**
 * WidgetPreview — renders the REAL widget component for a given spec widget.
 * Memoised on a stable `cacheKey` that is only `query_id + JSON(encoding)`.
 * Position changes (x/y/w/h) do NOT trigger a re-render.
 */
const WidgetPreview = memo(function WidgetPreview({ widget }) {
  const w = useMemo(() => normalizeWidget(widget), [widget])

  // Wrap in VariableProvider so hooks inside widgets don't throw.
  // Editor has no filter state, so variables are empty here.
  try {
    return (
      <VariableProvider initialValues={{}}>
        <div className="h-full w-full overflow-hidden">
          {w.html ? <HtmlWidget widget={w} /> : (
            <>
              {w.type === 'chart'   && <ChartWidget   widget={w} />}
              {w.type === 'kpi'     && <KpiWidget     widget={w} />}
              {w.type === 'metric'  && <MetricWidget  widget={w} />}
              {w.type === 'table'   && <TableWidget   widget={w} />}
              {w.type === 'pivot'   && <PivotWidget   widget={w} />}
              {w.type === 'filter'  && <FilterWidget  widget={w} options={[]} />}
              {w.type === 'text'    && <TextWidget    widget={w} />}
              {w.type === 'section' && <SectionWidget widget={w} />}
              {!['chart','kpi','metric','table','pivot','filter','text','section'].includes(w.type) && (
                <div className="flex items-center justify-center h-full text-xs text-muted">{w.type}</div>
              )}
            </>
          )}
        </div>
      </VariableProvider>
    )
  } catch {
    return <WidgetCardFallback widget={widget} />
  }
}, (prev, next) => {
  // Only re-render if query_id, encoding, chart_type, props, or content changed.
  // Do NOT re-render on pos changes (that's just a drag/resize).
  const p = prev.widget
  const n = next.widget
  return (
    p.query_id === n.query_id &&
    p.chart_type === n.chart_type &&
    JSON.stringify(p.encoding) === JSON.stringify(n.encoding) &&
    JSON.stringify(p.props) === JSON.stringify(n.props) &&
    p.content === n.content &&
    p.html === n.html &&
    JSON.stringify(p.columnFormats) === JSON.stringify(n.columnFormats) &&
    JSON.stringify(p.formattingRules) === JSON.stringify(n.formattingRules) &&
    // style must be in the comparator so per-widget background/transparent edits
    // actually re-render the live preview (previously omitted → BUG).
    JSON.stringify(p.style) === JSON.stringify(n.style) &&
    JSON.stringify(p.drilldown) === JSON.stringify(n.drilldown) &&
    p.type === n.type &&
    p.subtype === n.subtype
  )
})

/** Fallback card shown while loading or on error. */
function WidgetCardFallback({ widget }) {
  const accentCls = {
    kpi:    'text-emerald-500',
    table:  'text-primary',
    chart:  'text-cyan-500',
    filter: 'text-amber-500',
    text:   'text-muted',
  }[widget.type] ?? 'text-muted'

  return (
    <div className="h-full w-full bg-surface overflow-hidden flex flex-col">
      <div className="px-3 py-1.5 flex items-center gap-2 border-b border-border/50">
        <span className={`text-[10px] font-bold uppercase tracking-widest ${accentCls}`}>{widget.type}</span>
        {widget.query_id && <span className="text-[10px] text-muted/70 truncate">{widget.query_id}</span>}
      </div>
      <div className="px-3 py-2 text-xs text-muted flex-1 overflow-hidden">
        {widget.type === 'chart' && <p className="truncate">{widget.chart_type ?? 'chart'} — <span className="font-mono">{widget.encoding?.x ?? '?'}</span> vs <span className="font-mono">{widget.encoding?.y ?? '?'}</span></p>}
        {widget.type === 'kpi' && <p className="truncate">{widget.props?.label ?? 'KPI'} · <span className="font-mono">{widget.encoding?.value || '(no column)'}</span></p>}
        {widget.type === 'table' && <p>Table · limit <span className="font-mono">{widget.props?.limit ?? 50}</span></p>}
        {widget.type === 'filter' && <p className="truncate">{widget.subtype ?? 'select'} → <span className="font-mono">{widget.target_var || '(no var)'}</span></p>}
        {widget.type === 'text' && <p className="line-clamp-3 italic text-muted/70">{(widget.content ?? '').split('\n')[0] || 'Empty text block'}</p>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// WidgetHoverToolbar — top-right actions: duplicate + delete
// ---------------------------------------------------------------------------

function WidgetHoverToolbar({ widget, onDuplicate, onDelete, visible, reorder = false, onHeightStep }) {
  return (
    <div
      className={`absolute top-1 right-1 flex items-center gap-1 z-10 transition-opacity ${visible ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
      style={{ transition: 'opacity 0.15s' }}
    >
      {/* Mobile (reorder) height stepper — replaces the tiny corner resize handles
          that are too fiddly on touch. Steps widget height by ±1 grid row. */}
      {reorder && (
        <span className="flex items-center rounded-md bg-surface border border-border shadow-sm overflow-hidden">
          <button
            title="Shorter"
            onMouseDown={e => { e.stopPropagation(); e.preventDefault() }}
            onClick={e => { e.stopPropagation(); onHeightStep?.(widget.id, -1) }}
            className="w-6 h-6 flex items-center justify-center text-muted hover:text-primary transition-colors"
            data-testid={`widget-shorter-${widget.id}`}
          ><ChevronDown size={13} /></button>
          <button
            title="Taller"
            onMouseDown={e => { e.stopPropagation(); e.preventDefault() }}
            onClick={e => { e.stopPropagation(); onHeightStep?.(widget.id, 1) }}
            className="w-6 h-6 flex items-center justify-center text-muted hover:text-primary transition-colors border-l border-border"
            data-testid={`widget-taller-${widget.id}`}
          ><ChevronUp size={13} /></button>
        </span>
      )}
      <button
        title="Duplicate widget (⌘D)"
        onMouseDown={e => { e.stopPropagation(); e.preventDefault() }}
        onClick={e => { e.stopPropagation(); onDuplicate(widget.id) }}
        className="w-6 h-6 flex items-center justify-center rounded-md bg-surface border border-border hover:border-primary hover:text-primary text-muted shadow-sm transition-all text-xs"
        data-testid={`widget-duplicate-${widget.id}`}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
          <rect x="1" y="3" width="7" height="8" rx="1"/>
          <path d="M4 3V2a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-1"/>
        </svg>
      </button>
      <button
        title="Delete widget (Delete)"
        onMouseDown={e => { e.stopPropagation(); e.preventDefault() }}
        onClick={e => { e.stopPropagation(); onDelete(widget.id) }}
        className="w-6 h-6 flex items-center justify-center rounded-md bg-surface border border-border hover:border-red-400 hover:text-red-500 text-muted shadow-sm transition-all text-xs"
        data-testid={`widget-delete-${widget.id}`}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
          <path d="M2 3h8M5 3V2h2v1M4.5 5l.5 4M7.5 5l-.5 4"/>
          <rect x="2.5" y="3" width="7" height="7" rx="1"/>
        </svg>
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// EditorFilterBar — the above-grid filter bar, rendered in the editor canvas so
// header-placed filters (placement: 'header') are VISIBLE + selectable. Mirrors
// the renderer's `nubi-filter-bar` flex-wrap strip. Each chip is selectable
// (opens its inspector) and removable; clicking the empty bar background passes
// through to the canvas's deselect handler.
// ---------------------------------------------------------------------------

function EditorFilterBar({ widgets, selectedId, onSelect, onRemove }) {
  if (!widgets || widgets.length === 0) return null
  return (
    <div
      className="nubi-filter-bar flex flex-wrap items-end gap-3 px-1 pb-4 mb-4 border-b border-border"
      data-testid="editor-filter-bar"
    >
      {widgets.map(w => {
        const isSelected = selectedId === w.id
        return (
          <div
            key={w.id}
            data-testid={`filter-bar-item-${w.id}`}
            onClick={(e) => { e.stopPropagation(); onSelect(w.id) }}
            className={`group relative min-w-[10rem] max-w-xs rounded-lg border-2 bg-surface px-2.5 py-1.5 cursor-pointer transition-all overflow-visible ${
              isSelected ? 'border-primary shadow-md' : 'border-border hover:border-primary/50'
            }`}
            title="Click to configure · in the filter bar"
          >
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRemove(w.id) }}
              data-testid={`filter-bar-remove-${w.id}`}
              title="Remove filter"
              aria-label="Remove filter"
              className="absolute -top-2 -right-2 w-5 h-5 flex items-center justify-center rounded-full bg-surface border border-border text-muted hover:text-red-500 hover:border-red-400 shadow-sm opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity z-10"
            >
              <X size={11} />
            </button>
            <p className="text-[10px] font-medium text-muted truncate flex items-center gap-1">
              <FilterIcon size={11} className="shrink-0" />
              {w.props?.label || w.subtype || (w.type === 'text' ? 'Text' : 'Filter')}
            </p>
            <p className="text-xs font-mono text-fg truncate mt-0.5">
              {w.type === 'filter'
                ? `${w.subtype ?? 'select'} → ${w.target_var || '(no var)'}`
                : ((w.content ?? '').split('\n')[0] || 'text')}
            </p>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// EditableTabStrip — edit-mode tab bar: add / inline-rename / drag-reorder / delete
// ---------------------------------------------------------------------------

/**
 * The editor's tab strip. Distinct from the read-only TabBar.jsx used in the
 * viewer/preview: here each tab is an editable, draggable, deletable chip plus an
 * "Add tab" button. Reorder uses native HTML5 drag-and-drop (no extra deps).
 *
 * All mutations route through callbacks the parent wires to setSpec/history so
 * undo/redo + dirty tracking + save keep working.
 */
function EditableTabStrip({
  tabs, activeTabId, onActivate, onAdd, onRename, onReorder, onDeleteRequest,
}) {
  const [editingId, setEditingId] = useState(null)
  const [draftLabel, setDraftLabel] = useState('')
  const dragIndexRef = useRef(null)
  const [overIndex, setOverIndex] = useState(null)

  const startRename = (tab) => { setEditingId(tab.id); setDraftLabel(tab.label ?? '') }
  const commitRename = () => {
    if (editingId != null) {
      const label = draftLabel.trim()
      if (label) onRename(editingId, label)
    }
    setEditingId(null)
  }

  const onDrop = (toIndex) => {
    const from = dragIndexRef.current
    dragIndexRef.current = null
    setOverIndex(null)
    if (from == null || from === toIndex) return
    onReorder(from, toIndex)
  }

  return (
    <div
      className="flex items-center gap-1 border-b border-border bg-surface px-2 h-9 shrink-0 overflow-x-auto"
      data-testid="editor-tab-strip"
    >
      {tabs.map((tab, index) => {
        const isActive = tab.id === activeTabId
        const isEditing = editingId === tab.id
        const isOver = overIndex === index
        return (
          <div
            key={tab.id}
            draggable={!isEditing}
            onDragStart={(e) => { dragIndexRef.current = index; e.dataTransfer.effectAllowed = 'move' }}
            onDragOver={(e) => { e.preventDefault(); setOverIndex(index) }}
            onDragLeave={() => setOverIndex(o => (o === index ? null : o))}
            onDrop={(e) => { e.preventDefault(); onDrop(index) }}
            onDragEnd={() => { dragIndexRef.current = null; setOverIndex(null) }}
            data-testid={`editor-tab-${tab.id}`}
            className={`group flex items-center gap-1 h-7 pl-1.5 pr-1 rounded-lg border text-sm shrink-0 transition-colors ${
              isActive ? 'border-primary bg-primary/10 text-primary' : 'border-border bg-surface text-muted hover:text-fg'
            } ${isOver ? 'ring-2 ring-ring/50' : ''}`}
          >
            <GripVertical size={12} className="text-muted/50 cursor-grab active:cursor-grabbing shrink-0" />
            {isEditing ? (
              <input
                autoFocus
                value={draftLabel}
                onChange={e => setDraftLabel(e.target.value)}
                onBlur={commitRename}
                onKeyDown={e => {
                  if (e.key === 'Enter') { e.preventDefault(); commitRename() }
                  if (e.key === 'Escape') { e.preventDefault(); setEditingId(null) }
                }}
                className="h-5 w-24 text-sm px-1 bg-surface border border-ring/40 rounded outline-none text-fg"
                data-testid={`editor-tab-input-${tab.id}`}
              />
            ) : (
              <button
                type="button"
                onClick={() => onActivate(tab.id)}
                onDoubleClick={() => startRename(tab)}
                className="font-medium px-1 whitespace-nowrap focus:outline-none"
                title="Click to switch · double-click to rename"
              >
                {tab.label || 'Tab'}
              </button>
            )}
            <button
              type="button"
              onClick={() => onDeleteRequest(tab)}
              title="Delete tab"
              aria-label={`Delete tab ${tab.label}`}
              data-testid={`editor-tab-delete-${tab.id}`}
              className="w-4 h-4 flex items-center justify-center rounded text-muted/50 hover:text-red-500 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity shrink-0"
            >
              <X size={11} />
            </button>
          </div>
        )
      })}
      <button
        type="button"
        onClick={onAdd}
        title="Add tab"
        aria-label="Add tab"
        data-testid="editor-tab-add"
        className="flex items-center gap-1 h-7 px-2 rounded-lg border border-dashed border-border text-muted hover:text-primary hover:border-primary transition-colors shrink-0 text-xs font-medium"
      >
        <Plus size={13} /> Tab
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DeleteTabDialog — prompt: move this tab's widgets elsewhere or delete them
// ---------------------------------------------------------------------------

function DeleteTabDialog({ tab, tabs, widgetCount, onMove, onDeleteWidgets, onCancel }) {
  const others = tabs.filter(t => t.id !== tab.id)
  const [target, setTarget] = useState(others[0]?.id ?? '')
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" data-testid="delete-tab-dialog">
      <div className="absolute inset-0 bg-black/40" onClick={onCancel} />
      <div className="relative w-full max-w-sm rounded-2xl border border-border bg-surface shadow-2xl p-5 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-fg">Delete “{tab.label}”?</h3>
          <p className="text-xs text-muted mt-1">
            This tab has {widgetCount} widget{widgetCount === 1 ? '' : 's'}. Choose what to do with {widgetCount === 1 ? 'it' : 'them'}.
          </p>
        </div>
        {others.length > 0 && (
          <div className="space-y-1.5">
            <FieldLabel>Move widgets to</FieldLabel>
            <select className={selectCls} value={target} onChange={e => setTarget(e.target.value)} data-testid="delete-tab-move-target">
              {others.map(t => <option key={t.id} value={t.id}>{t.label || t.id}</option>)}
            </select>
          </div>
        )}
        <div className="flex flex-col gap-2 pt-1">
          {others.length > 0 && (
            <button
              type="button"
              onClick={() => onMove(target)}
              data-testid="delete-tab-move-btn"
              className="w-full h-9 text-sm font-medium rounded-lg bg-primary text-primary-fg hover:opacity-90 transition-opacity">
              Move widgets &amp; delete tab
            </button>
          )}
          <button
            type="button"
            onClick={onDeleteWidgets}
            data-testid="delete-tab-delete-widgets-btn"
            className="w-full h-9 text-sm font-medium rounded-lg border border-red-300 text-red-600 hover:bg-red-50 transition-colors">
            Delete tab &amp; its widgets
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="w-full h-9 text-sm font-medium rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// TabsPanel — inspector: tab-bar tokens + per-tab style/background overrides
// ---------------------------------------------------------------------------

function TabsPanel({ spec, onSpecChange, activeTabId, onActivate, onAddTab }) {
  const tabs = Array.isArray(spec.tabs) ? spec.tabs : []
  const tabBar = spec.tab_bar ?? {}
  const setTabBar = (patch) => {
    const next = { ...tabBar, ...patch }
    Object.keys(next).forEach(k => { if (next[k] === '' || next[k] == null) delete next[k] })
    onSpecChange({ ...spec, tab_bar: next })
  }
  const activeTab = tabs.find(t => t.id === activeTabId) ?? null
  const setTab = (id, patch) =>
    onSpecChange({ ...spec, tabs: tabs.map(t => t.id === id ? { ...t, ...patch } : t) })
  const setTabStyle = (id, patch) => {
    const t = tabs.find(x => x.id === id)
    const style = { ...(t?.style ?? {}), ...patch }
    Object.keys(style).forEach(k => { if (style[k] === '' || style[k] == null) delete style[k] })
    setTab(id, { style: Object.keys(style).length ? style : {} })
  }

  if (tabs.length === 0) {
    return (
      <div className="p-4 space-y-3 overflow-y-auto h-full">
        <h3 className="text-sm font-semibold text-fg">Tabs</h3>
        <p className="text-xs text-muted/80 leading-relaxed rounded-lg border border-dashed border-border bg-surface-2/30 p-3">
          This dashboard has no tabs — it renders as a single canvas. Add a tab to
          split it into multiple tabbed pages. Existing widgets stay on the first
          tab; variables stay shared across all tabs.
        </p>
        <button
          onClick={onAddTab}
          data-testid="tabs-panel-add-first"
          className="w-full flex items-center justify-center gap-2 h-9 text-sm font-medium rounded-lg border border-dashed border-border text-muted hover:text-primary hover:border-primary transition-colors">
          <Plus size={14} /> Add first tab
        </button>
      </div>
    )
  }

  // Live preview reuses the read-only viewer TabBar so the variant/colors match.
  const previewTabs = tabs.length >= 2 ? tabs : [...tabs, { id: '__preview__', label: 'Preview' }]

  return (
    <div className="p-4 space-y-3 overflow-y-auto h-full">
      <h3 className="text-sm font-semibold text-fg">Tabs</h3>

      <Section title="Tab bar" icon={LayoutGrid}>
        <div className="rounded-lg border border-border bg-surface px-2 py-1.5">
          <TabBar tabs={previewTabs} activeTabId={activeTabId} onChange={() => {}} tabBar={tabBar} />
        </div>
        <div>
          <FieldLabel>Variant</FieldLabel>
          <div className="grid grid-cols-3 gap-1.5">
            {TAB_VARIANTS.map(v => {
              const active = (tabBar.variant ?? 'underline') === v
              return (
                <button key={v} onClick={() => setTabBar({ variant: v })}
                  className={`h-7 text-[11px] font-medium rounded-lg border capitalize transition-all ${active ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-muted border-border hover:border-primary hover:text-primary'}`}>
                  {v}
                </button>
              )
            })}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <FieldLabel>Align</FieldLabel>
            <select className={selectCls} value={tabBar.align ?? 'start'} onChange={e => setTabBar({ align: e.target.value })}>
              {TAB_ALIGNS.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <div>
            <FieldLabel>Size</FieldLabel>
            <select className={selectCls} value={tabBar.size ?? 'md'} onChange={e => setTabBar({ size: e.target.value })}>
              {TAB_SIZES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input type="color" className="h-8 w-10 shrink-0 rounded-lg border border-border bg-surface cursor-pointer"
            value={tabBar.accent ?? '#6366f1'} onChange={e => setTabBar({ accent: e.target.value })} />
          <input type="text" className={`${inputCls} flex-1`} placeholder="Accent color (active tab)"
            value={tabBar.accent ?? ''} onChange={e => setTabBar({ accent: e.target.value })} />
        </div>
        <div>
          <FieldLabel>Custom CSS (sanitized)</FieldLabel>
          <textarea rows={2} className={`${inputCls} h-auto py-1.5 font-mono text-xs resize-y`}
            placeholder="border-radius: 10px; gap: 6px;"
            value={tabBar.custom_css ?? ''} onChange={e => setTabBar({ custom_css: e.target.value || undefined })} />
        </div>
      </Section>

      <Section title="Tabs" icon={Heading} defaultOpen>
        <div className="flex flex-wrap gap-1.5">
          {tabs.map(t => (
            <button key={t.id} onClick={() => onActivate(t.id)}
              className={`px-2.5 h-7 text-xs font-medium rounded-lg border transition-all ${t.id === activeTabId ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-muted border-border hover:border-primary hover:text-primary'}`}>
              {t.label || t.id}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-muted/70">Select a tab to edit its label, style &amp; background. Reorder/rename/delete from the strip above the canvas.</p>
      </Section>

      {activeTab && (
        <Section title={`Tab · ${activeTab.label || activeTab.id}`} icon={Settings2} defaultOpen>
          <div>
            <FieldLabel>Label</FieldLabel>
            <input type="text" className={inputCls} value={activeTab.label ?? ''}
              onChange={e => setTab(activeTab.id, { label: e.target.value })} />
          </div>
          <div>
            <FieldLabel>Icon (optional)</FieldLabel>
            <input type="text" className={inputCls} placeholder="lucide name, e.g. BarChart3"
              value={activeTab.icon ?? ''} onChange={e => setTab(activeTab.id, { icon: e.target.value || undefined })} />
          </div>
          <div className="space-y-1">
            <SectionLabel>Active-tab accent override</SectionLabel>
            <div className="flex items-center gap-2">
              <input type="color" className="h-8 w-10 shrink-0 rounded-lg border border-border bg-surface cursor-pointer"
                value={activeTab.style?.accent ?? '#6366f1'} onChange={e => setTabStyle(activeTab.id, { accent: e.target.value })} />
              <input type="text" className={`${inputCls} flex-1`} placeholder="inherit from tab bar"
                value={activeTab.style?.accent ?? ''} onChange={e => setTabStyle(activeTab.id, { accent: e.target.value })} />
            </div>
          </div>
          <div>
            <FieldLabel>Tab custom CSS (sanitized)</FieldLabel>
            <textarea rows={2} className={`${inputCls} h-auto py-1.5 font-mono text-xs resize-y`}
              placeholder="font-weight: 600;"
              value={activeTab.style?.custom_css ?? ''} onChange={e => setTabStyle(activeTab.id, { custom_css: e.target.value || undefined })} />
          </div>
          <div className="space-y-1">
            <SectionLabel>Canvas background (this tab)</SectionLabel>
            <BackgroundEditor value={activeTab.background} onChange={bg => setTab(activeTab.id, { background: bg && bg.type ? bg : undefined })} />
          </div>
        </Section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Toolbar helpers — small presentational primitives shared by the top-bar
// clusters below. Kept generic (no editor-state closures) so they can sit
// outside the main component.
// ---------------------------------------------------------------------------

// Vertical divider between toolbar clusters.
function ToolbarDivider() {
  return <div className="hidden md:block w-px h-5 bg-border shrink-0" aria-hidden="true" />
}

/**
 * ToolbarPopover — a labelled trigger button that reveals an arbitrary panel
 * of content below it. Used to group related, lower-frequency controls (e.g.
 * device + zoom) behind one bar slot instead of spreading them out inline.
 */
function ToolbarPopover({ icon: Icon, label, title, testId, active, children }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  return (
    <div className="relative shrink-0" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        title={title}
        aria-label={title}
        aria-expanded={open}
        data-testid={testId}
        className={[
          'h-8 pl-2.5 pr-2 inline-flex items-center gap-1 rounded-lg border text-xs font-medium whitespace-nowrap',
          'transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
          open || active
            ? 'bg-primary text-primary-fg border-primary'
            : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
        ].join(' ')}
      >
        <Icon size={14} />
        <span className="hidden lg:inline">{label}</span>
        <ChevronDown size={12} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1.5 z-50 bg-surface border border-border rounded-xl shadow-xl p-3 w-max"
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// DashboardEditor — main export
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   boardId?: string|null,
 *   onSaved?: (board: object) => void,
 *   onSpecChange?: (spec: object) => void,
 * }} props
 */
export default function DashboardEditor({ boardId = null, onSaved, onSpecChange, onSetSpec }) {
  // ── History state ─────────────────────────────────────────────────────────
  const [hist, setHist] = useState(() => createHistory(DEFAULT_SPEC))
  const spec = hist.present

  const isDraggingRef = useRef(false)
  const frozenLayoutsRef = useRef(null)

  // ── Dirty tracking ────────────────────────────────────────────────────────
  // We track the spec at last save time to compute dirtyness.
  const savedSpecRef = useRef(null) // null = never saved
  const dirty = useMemo(() => {
    if (savedSpecRef.current === null && spec.widgets.length === 0) return false
    return JSON.stringify(spec) !== JSON.stringify(savedSpecRef.current)
  }, [spec])

  // beforeunload guard
  useEffect(() => {
    const handler = (e) => {
      if (!dirty) return
      e.preventDefault()
      e.returnValue = 'You have unsaved changes. Leave?'
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  // EditorPage's FILTERS button dispatches `nubi:open-filters` (OPEN_FILTERS_EVENT).
  // It cannot reach the editor's state directly, so we listen on window and open
  // the filters-drawer authoring panel here. Matches the seam documented in
  // EditorPage.jsx.
  useEffect(() => {
    const open = () => { setFiltersOpen(true); setPreview(false) }
    window.addEventListener('nubi:open-filters', open)
    return () => window.removeEventListener('nubi:open-filters', open)
  }, [])

  const commitSpec = useCallback((newSpec) => {
    setHist(h => historyPush(h, typeof newSpec === 'function' ? newSpec(h.present) : newSpec))
  }, [])

  const setSpec = commitSpec

  // Expose commitSpec to callers (e.g. EditorShell) that need to write through
  // DashboardEditor's history without owning spec state.
  useEffect(() => {
    onSetSpec?.(commitSpec)
  }, [onSetSpec, commitSpec])

  // Notify callers (e.g. EditorShell) whenever the spec changes so they can
  // observe the live spec without owning it.
  useEffect(() => {
    onSpecChange?.(spec)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec])

  const [selectedId, setSelectedId] = useState(null)
  const [preview, setPreview] = useState(false)
  // VS Code-style full-pane "Code" view (third mode alongside edit + preview).
  // When on, the canvas is replaced by DashboardCodeView (dashboard.json editor).
  const [codeView, setCodeView] = useState(false)
  const [saving, setSaving] = useState(false)
  const [_saveError, setSaveError] = useState(null)
  const [loading, setLoading] = useState(!!boardId)
  const [loadError, setLoadError] = useState(null)
  const [savedBoardId, setSavedBoardId] = useState(boardId)
  // rightPanel ∈ {'add','config','chat','board'} — drives the single RHS panel.
  const [rightPanel, setRightPanel] = useState('add')
  // Start collapsed on tablet (md) so the panel doesn't immediately overlay the
  // canvas; on lg+ the panel is always in-flow so we default to open.
  const [rightCollapsed, setRightCollapsed] = useState(
    () => typeof window !== 'undefined' && window.innerWidth < 1024
  )
  const [hoveredId, setHoveredId] = useState(null)
  // Mobile/tablet sheet state: which sheet is open (null = closed)
  // 'palette' | 'config' | 'chat' | 'board' | null
  const [mobileSheet, setMobileSheet] = useState(null)
  // Below md the toolbar cluster (device switcher, zoom controls, panel toggles)
  // collapses behind a hamburger that opens this slide-out menu.
  const [mobileMenu, setMobileMenu] = useState(false)

  // ── Tabs (Track T — T5 editor) ────────────────────────────────────────────
  // Which tab the canvas is showing. null when the spec has no tabs (today's
  // single-canvas behavior). When the spec HAS tabs we resolve to the first one
  // if the requested id is gone (e.g. after a delete or an undo).
  const [activeTabIdRaw, setActiveTabIdRaw] = useState(null)
  const activeTabId = resolveActiveTab(spec, activeTabIdRaw)
  // Pending delete-tab confirmation (null = no dialog). Holds the tab object.
  const [deletingTab, setDeletingTab] = useState(null)
  // Filters-drawer authoring panel (opened by EditorPage's nubi:open-filters event).
  const [filtersOpen, setFiltersOpen] = useState(false)

  // Registered queries (author:sql queries the user has saved/registered),
  // for QueryPicker to list alongside DEMO_QUERY_IDS. Loaded once on mount.
  const [queryIds, setQueryIds] = useState([])
  useEffect(() => {
    let cancelled = false
    listRegisteredQueries().then(list => {
      if (!cancelled) setQueryIds(list.map(q => ({ id: q.id, name: q.name })))
    })
    return () => { cancelled = true }
  }, [])

  const { width: canvasWidth, containerRef: canvasRef } = useElementWidth(900)
  // The scrollable canvas viewport (pan surface + pinch-zoom target).
  const mainRef = useRef(null)
  const { topbarSlot, setPageOwnsChat } = useUi()
  const [device, setDevice] = useState('desktop')
  // Mirror `device` into a ref so RGL's stable callbacks (commitLayout etc.)
  // always route commits to the CURRENTLY active breakpoint without re-creating.
  const deviceRef = useRef(device)
  // Sync DURING render (not via an effect): when the keyed grid remounts on a
  // device switch and fires its mount onLayoutChange / a drag stop, commits must
  // route to the CURRENT breakpoint. Child effects run before parent effects, so
  // an effect-based mirror would lag one commit and write a tablet/mobile layout
  // into the canonical desktop pos (the "bundle to the left" corruption).
  deviceRef.current = device
  // Editor-local preview width per device (not persisted to the spec) + canvas
  // zoom (`'fit'` auto-scales to the available width; a number is an explicit
  // zoom set by the buttons or a pinch gesture).
  const [deviceWidths, setDeviceWidths] = useState({ tablet: DEVICE_WIDTHS.tablet, mobile: DEVICE_WIDTHS.mobile })
  const [zoomMode, setZoomMode] = useState('fit')
  // Mirror into a ref so the (passive-free) pinch handler can read/update it
  // without re-binding the listener every render.
  const zoomModeRef = useRef(zoomMode)
  useEffect(() => { zoomModeRef.current = zoomMode }, [zoomMode])
  // NOTE: the drag-time column-guide + ghost overlay is now owned by GridCanvas
  // (its .grid-dragging class + --grid-col-w var), so the editor no longer needs a
  // boolean `isDragging` state — only the ref (frozenLayoutsRef freeze) matters.

  // Claim exclusive chat ownership while the editor is mounted so the global
  // chat button + panel are suppressed (editor has its own Chat tab in the RHS).
  useEffect(() => {
    setPageOwnsChat(true)
    return () => setPageOwnsChat(false)
  }, [setPageOwnsChat])

  // Active RGL breakpoint follows the device frame width (desktop→lg, …).
  const activeBreakpoint = DEVICE_TO_BREAKPOINT[device]

  // ── Device frame + zoom/pan canvas ─────────────────────────────────────────
  // The grid always renders at its true DESIGN width for the active breakpoint
  // (so the displayed breakpoint is chosen by the device, never by pixel width —
  // this is what fixes the "everything bundles to the left" bug). The whole frame
  // is then CSS-scaled by `effectiveZoom` to fit / zoom the available canvas, and
  // the scrollable <main> acts as the pan viewport.
  //   desktop → max(canvasWidth, floor) so it stays editable (zoomed out) on a phone
  //   tablet/mobile → the editor-local custom width
  const designWidth = device === 'desktop'
    ? Math.max(canvasWidth || DESKTOP_MIN_WIDTH, DESKTOP_MIN_WIDTH)
    : (deviceWidths[device] ?? DEVICE_WIDTHS[device])
  const fitZoom = canvasWidth > 0 && designWidth > canvasWidth ? canvasWidth / designWidth : 1
  const effectiveZoom = zoomMode === 'fit'
    ? fitZoom
    : Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoomMode))
  // Mirror the live zoom so the (once-bound) pinch handler can read it.
  const effectiveZoomRef = useRef(effectiveZoom)
  useEffect(() => { effectiveZoomRef.current = effectiveZoom }, [effectiveZoom])

  // Reset to "fit" when switching devices so each size starts framed.
  useEffect(() => { setZoomMode('fit') }, [device])

  // Pinch-to-zoom (two fingers) + ctrl/⌘-wheel zoom on the canvas viewport.
  // Bound once with { passive:false } so we can preventDefault the browser's
  // own page-zoom/scroll. One-finger drags fall through to native scroll (pan).
  useEffect(() => {
    const el = mainRef.current
    if (!el) return
    const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY)
    const pinch = { startDist: null, startZoom: 1 }
    const clampZoom = (z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z))
    const onTouchStart = (e) => {
      if (e.touches.length === 2) {
        pinch.startDist = dist(e.touches)
        pinch.startZoom = effectiveZoomRef.current
      }
    }
    const onTouchMove = (e) => {
      if (e.touches.length !== 2 || pinch.startDist == null) return
      e.preventDefault()
      setZoomMode(clampZoom(pinch.startZoom * (dist(e.touches) / pinch.startDist)))
    }
    const onTouchEnd = (e) => { if (e.touches.length < 2) pinch.startDist = null }
    const onWheel = (e) => {
      if (!e.ctrlKey && !e.metaKey) return   // ctrl/⌘+wheel (incl. trackpad pinch) only
      e.preventDefault()
      setZoomMode(clampZoom(effectiveZoomRef.current * (e.deltaY < 0 ? 1.05 : 0.95)))
    }
    el.addEventListener('touchstart', onTouchStart, { passive: false })
    el.addEventListener('touchmove', onTouchMove, { passive: false })
    el.addEventListener('touchend', onTouchEnd, { passive: false })
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
      el.removeEventListener('touchend', onTouchEnd)
      el.removeEventListener('wheel', onWheel)
    }
  }, [preview])

  // Reserve vertical space for the (scaled) frame so the canvas scrolls/pans.
  // Use the active breakpoint's effective layout (override or fallback) and skip
  // widgets hidden at this breakpoint so the reserved height is accurate.
  const _rowH = spec.layout?.row_height ?? 60
  // Reserve height for the ACTIVE tab's GRID widgets only (the canvas grid shows
  // just those — header/drawer widgets render outside the GridCanvas).
  const _maxBottom = widgetsForTab(spec, activeTabId).reduce((m, w) => {
    if (effectivePlacement(w) !== 'grid') return m
    if (isHiddenAt(w, activeBreakpoint)) return m
    const p = effectivePos(w, spec, activeBreakpoint) ?? {}
    const y = (p.y ?? 1) - 1
    const h = p.h ?? 4
    return Math.max(m, y + h)
  }, 0)
  const deviceDashHeight = _maxBottom * (_rowH + 12) + 48

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e) => {
      const tag = e.target?.tagName?.toLowerCase()
      if (
        tag === 'input' || tag === 'textarea' ||
        e.target?.isContentEditable ||
        e.target?.closest?.('.monaco-editor')
      ) return

      const isMac = navigator.platform?.toUpperCase().includes('MAC') || navigator.userAgent?.includes('Mac')
      const ctrl = isMac ? e.metaKey : e.ctrlKey

      // ── Ctrl-shortcuts ────────────────────────────────────────────────────
      if (ctrl) {
        if (e.key === 'z' && !e.shiftKey) {
          e.preventDefault()
          setHist(h => canUndo(h) ? historyUndo(h) : h)
          return
        }
        if ((e.key === 'z' && e.shiftKey) || (!isMac && e.key === 'y')) {
          e.preventDefault()
          setHist(h => canRedo(h) ? historyRedo(h) : h)
          return
        }
        if (e.key === 'd' || e.key === 'D') {
          e.preventDefault()
          if (selectedId) {
            setHist(h => {
              const prev = h.present
              const source = prev.widgets.find(w => w.id === selectedId)
              if (!source) return h
              const cols = prev.layout?.cols ?? 12
              const size = source.pos ?? WIDGET_SIZES[source.type] ?? WIDGET_SIZES.chart
              const pos = findFreeSpot(prev.widgets, size.w, size.h, cols)
              const clone = { ...source, id: genId(source.type), pos: { ...size, ...pos } }
              const newSpec = { ...prev, widgets: [...prev.widgets, clone] }
              setTimeout(() => setSelectedId(clone.id), 0)
              return historyPush(h, newSpec)
            })
          }
          return
        }
        return
      }

      // ── Non-ctrl shortcuts (need a selected widget for most) ──────────────

      if (e.key === 'Escape') {
        setSelectedId(null)
        return
      }

      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) {
        e.preventDefault()
        setHist(h => {
          const newSpec = { ...h.present, widgets: h.present.widgets.filter(w => w.id !== selectedId) }
          return historyPush(h, newSpec)
        })
        setSelectedId(null)
        return
      }

      // Arrow keys — nudge (1 cell) or resize (shift + 1 cell). Edits apply to
      // the ACTIVE breakpoint only: desktop writes widget.pos, tablet/mobile
      // write the matching spec.responsive override for the selected widget.
      if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key) && selectedId) {
        e.preventDefault()
        const dx = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0
        const dy = e.key === 'ArrowUp'   ? -1 : e.key === 'ArrowDown'  ? 1 : 0
        const bp = DEVICE_TO_BREAKPOINT[deviceRef.current]

        setHist(h => {
          const prev = h.present
          const isSm = bp === 'sm'
          const cols = isSm ? 1 : (prev.layout?.cols ?? 12)
          const w = prev.widgets.find(x => x.id === selectedId)
          if (!w) return h
          // Start from the effective pos at this breakpoint (override if any,
          // else the canonical desktop pos), then nudge.
          const base = bp === 'lg'
            ? (w.pos ?? { x: 1, y: 1, w: 4, h: 4 })
            : { ...(w.pos ?? { x: 1, y: 1, w: 4, h: 4 }), ...(prev.responsive?.[bp]?.[selectedId] ?? {}) }
          const pos = { ...base }
          const mins = WIDGET_MIN_SIZES[w.type] ?? { minW: 2, minH: 2 }
          if (e.shiftKey) {
            pos.w = Math.max(isSm ? 1 : mins.minW, Math.min(cols - pos.x + 1, pos.w + dx))
            pos.h = Math.max(mins.minH, pos.h + dy)
          } else {
            pos.x = Math.max(1, Math.min(cols - pos.w + 1, pos.x + dx))
            pos.y = Math.max(1, pos.y + dy)
          }
          // Route via the same single-breakpoint commit used by drag/resize.
          const item = { i: selectedId, x: pos.x - 1, y: pos.y - 1, w: pos.w, h: pos.h }
          return historyPush(h, applyLayoutCommit(prev, bp, [item]))
        })
        return
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedId])

  const handleUndo = useCallback(() => setHist(h => canUndo(h) ? historyUndo(h) : h), [])
  const handleRedo = useCallback(() => setHist(h => canRedo(h) ? historyRedo(h) : h), [])

  // ── Load existing board ───────────────────────────────────────────────────
  useEffect(() => {
    if (!boardId) { setLoading(false); return }
    let cancelled = false
    setLoading(true)
    get(`/boards/${boardId}`)
      .then(board => {
        if (cancelled) return
        const loadedSpec = board?.config?.spec
        if (loadedSpec) {
          setHist(createHistory(loadedSpec))
          savedSpecRef.current = loadedSpec
        } else {
          setLoadError('This board has no spec yet. Starting with a blank canvas.')
        }
        setSavedBoardId(board.id ?? boardId)
        setLoading(false)
      })
      .catch(err => {
        if (!cancelled) { setLoadError(`Failed to load board: ${err.message}`); setLoading(false) }
      })
    return () => { cancelled = true }
  }, [boardId])

  // ── Derived state ─────────────────────────────────────────────────────────
  const selectedWidget = spec.widgets.find(w => w.id === selectedId) ?? null

  // Tabs (T5). When spec.tabs is empty this is the full widget list (today's
  // behavior); otherwise the canvas only shows the active tab's widgets — exactly
  // like SpecRenderer. Layout building, height reservation and GridCanvas all use
  // this scoped list, while mutations still operate on the full spec by widget id.
  const hasTabs = Array.isArray(spec.tabs) && spec.tabs.length > 0
  // All of the active tab's widgets (any placement) — used for emptiness checks.
  const tabWidgets = useMemo(
    () => widgetsForTab(spec, activeTabId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [spec.widgets, spec.tabs, activeTabId],
  )
  // ONLY grid-placed widgets get grid cells — header (filter bar) and drawer
  // widgets are excluded from the GridCanvas layout (mirrors SpecRenderer).
  const gridTabWidgets = useMemo(
    () => tabWidgets.filter(w => effectivePlacement(w) === 'grid'),
    [tabWidgets],
  )
  // Header-placed (above-grid filter bar) widgets for the active tab, ordered.
  const headerTabWidgets = useMemo(
    () => headerWidgetsForTab(spec, activeTabId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [spec.widgets, spec.tabs, activeTabId],
  )
  // A spec whose widget list is scoped to the active tab's GRID widgets only — fed
  // to the layout builder so off-tab and non-grid widgets never participate in
  // this tab's grid geometry.
  const tabSpec = useMemo(
    () => ({ ...spec, widgets: gridTabWidgets }),
    [spec, gridTabWidgets],
  )
  // Per-tab background falls back to the dashboard background (mirrors SpecRenderer).
  const activeTab = hasTabs ? (spec.tabs.find(t => t.id === activeTabId) ?? null) : null
  const canvasBackground = (activeTab?.background && activeTab.background.type)
    ? activeTab.background
    : spec.background

  // Per-device column counts (independent now — mobile is no longer locked to 1).
  //   lg = cols (canonical), md = cols_md (fallback cols), sm = cols_sm (fallback 1).
  const lgCols = spec.layout?.cols ?? 12
  const mdCols = spec.layout?.cols_md ?? lgCols
  const smCols = spec.layout?.cols_sm ?? 1

  // Per-breakpoint layouts: lg from canonical widget.pos; md/sm apply
  // spec.responsive overrides with a fallback to the lg-derived layout. Each
  // item keeps its per-type min sizes + authored static/min/max constraints.
  // The 0-based {i,x,y,w,h,...} items are EXACTLY the shape GridCanvas's `layout`
  // prop expects (identical to what RGL consumed) — the commit contract is unchanged.
  // Built from tabSpec so each tab gets its own grid (off-tab widgets excluded).
  const gridLayouts = useMemo(() => {
    if (isDraggingRef.current && frozenLayoutsRef.current) return frozenLayoutsRef.current
    const layouts = buildResponsiveLayouts(tabSpec, lgCols, (w) => ({
      minDefaults: WIDGET_MIN_SIZES[w.type] ?? { minW: 2, minH: 2 },
    }), { lg: lgCols, md: mdCols, sm: smCols })
    frozenLayoutsRef.current = layouts
    return layouts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridTabWidgets, JSON.stringify(spec.responsive), lgCols, mdCols, smCols])

  // Grid flexibility options (spec.layout.*). The editor defaults to free-place
  // so drag-to-place keeps working; authors can opt into packing via the Advanced
  // group. GridCanvas applies compaction to the COMMITTED layout before
  // onLayoutCommit fires ('free'/'none' are identity → byte-identical commits).
  const editorCompaction = spec.layout?.compaction ?? 'free'
  const editorDense = !!spec.layout?.dense
  // GridCanvas takes a single scalar gap for both axes — map from the new `gap`
  // field, falling back to the legacy margin_x (they were equal in practice).
  const editorGap = spec.layout?.gap ?? spec.layout?.margin_x ?? 12
  const editorPadding = { x: spec.layout?.padding_x ?? 0, y: spec.layout?.padding_y ?? 0 }
  // Column count for the ACTIVE breakpoint — drives the grid geometry.
  const editorGridCols = activeBreakpoint === 'sm' ? smCols
    : activeBreakpoint === 'md' ? mdCols : lgCols
  // Mobile (sm) edits as a touch-friendly drag-to-reorder stack; desktop/tablet
  // use full 2-D grid editing.
  const gridMode = activeBreakpoint === 'sm' ? 'reorder' : 'grid'

  // ── Mutations ─────────────────────────────────────────────────────────────

  const addWidget = useCallback((type) => {
    setHist(h => {
      const prev = h.present
      const cols = prev.layout?.cols ?? 12
      // New widgets are placed within (and tagged for) the ACTIVE tab, so the
      // free-spot scan only considers that tab's occupancy. With no tabs this is
      // the full widget list and tab_id stays absent (today's behavior).
      const tab = resolveActiveTab(prev, activeTabIdRaw)
      const peers = tab ? widgetsForTab(prev, tab) : prev.widgets
      const size = WIDGET_SIZES[type] ?? WIDGET_SIZES.chart
      const pos = findFreeSpot(peers, size.w, size.h, cols)
      const widget = makeWidget(type, pos)
      if (tab) widget.tab_id = tab
      const newSpec = { ...prev, widgets: [...prev.widgets, widget] }
      setTimeout(() => setSelectedId(widget.id), 0)
      return historyPush(h, newSpec)
    })
    setPreview(false)
  }, [activeTabIdRaw])

  const removeWidget = useCallback((id) => {
    setHist(h => historyPush(h, { ...h.present, widgets: h.present.widgets.filter(w => w.id !== id) }))
    setSelectedId(prev => prev === id ? null : prev)
  }, [])

  const duplicateWidget = useCallback((id) => {
    setHist(h => {
      const prev = h.present
      const source = prev.widgets.find(w => w.id === id)
      if (!source) return h
      const cols = prev.layout?.cols ?? 12
      const size = source.pos ?? WIDGET_SIZES[source.type] ?? WIDGET_SIZES.chart
      // Scan only the source widget's tab so the clone lands beside its sibling
      // (the spread keeps source.tab_id, so it stays on the same tab).
      const peers = Array.isArray(prev.tabs) && prev.tabs.length
        ? widgetsForTab(prev, resolveActiveTab(prev, source.tab_id ?? null))
        : prev.widgets
      const pos = findFreeSpot(peers, size.w, size.h, cols)
      const clone = { ...source, id: genId(source.type), pos: { ...size, ...pos } }
      const newSpec = { ...prev, widgets: [...prev.widgets, clone] }
      setTimeout(() => setSelectedId(clone.id), 0)
      return historyPush(h, newSpec)
    })
  }, [])

  const updateWidget = useCallback((updated) => {
    setSpec(prev => ({ ...prev, widgets: prev.widgets.map(w => w.id === updated.id ? updated : w) }))
  }, [])

  // ── Tab mutations (T5) ─────────────────────────────────────────────────────
  // All route through setHist so undo/redo + dirty tracking work.

  // Add a tab. The FIRST tab created adopts all existing (untagged) widgets — they
  // already implicitly belong to the first tab, so no widget mutation is needed.
  const addTab = useCallback(() => {
    setHist(h => {
      const prev = h.present
      const tabs = Array.isArray(prev.tabs) ? prev.tabs : []
      const id = genTabId(tabs)
      const next = { ...prev, tabs: [...tabs, { id, label: `Tab ${tabs.length + 1}` }] }
      setTimeout(() => setActiveTabIdRaw(id), 0)
      return historyPush(h, next)
    })
    setPreview(false)
  }, [])

  const renameTab = useCallback((id, label) => {
    setSpec(prev => ({
      ...prev,
      tabs: (prev.tabs ?? []).map(t => t.id === id ? { ...t, label } : t),
    }))
  }, [setSpec])

  const reorderTabs = useCallback((from, to) => {
    setSpec(prev => {
      const tabs = [...(prev.tabs ?? [])]
      if (from < 0 || from >= tabs.length || to < 0 || to >= tabs.length) return prev
      const [moved] = tabs.splice(from, 1)
      tabs.splice(to, 0, moved)
      return { ...prev, tabs }
    })
  }, [setSpec])

  // Delete a tab. `mode` is 'move' (relocate its widgets to targetTabId) or
  // 'delete' (remove the tab AND its widgets). Untagged widgets count as the
  // first tab, so deleting the first tab must re-tag them onto the survivor.
  const deleteTab = useCallback((tabId, mode, targetTabId = null) => {
    setHist(h => {
      const prev = h.present
      const tabs = Array.isArray(prev.tabs) ? prev.tabs : []
      const remaining = tabs.filter(t => t.id !== tabId)
      const firstTabId = tabs[0]?.id ?? null
      // Widgets belonging to the deleted tab: explicit match, plus untagged ones
      // when deleting the first tab.
      const belongs = (w) => {
        const t = w.tab_id ?? null
        return t === tabId || (t == null && tabId === firstTabId)
      }
      let widgets
      if (mode === 'move' && targetTabId) {
        widgets = prev.widgets.map(w => belongs(w) ? { ...w, tab_id: targetTabId } : w)
      } else {
        widgets = prev.widgets.filter(w => !belongs(w))
      }
      // If the new first tab changed (deleted the old first), normalise any
      // widgets still tagged with the now-first tab's id so null === first holds.
      const next = { ...prev, tabs: remaining.length ? remaining : undefined, widgets }
      if (!remaining.length) {
        // Last tab removed → tab_id becomes meaningless; strip it so widgets render.
        next.widgets = widgets.map(w => (w.tab_id ? { ...w, tab_id: undefined } : w))
      }
      setTimeout(() => setActiveTabIdRaw(remaining[0]?.id ?? null), 0)
      return historyPush(h, next)
    })
  }, [])

  // "Move to tab →" widget action (context menu / inspector). null clears tab_id
  // back to the implicit first tab.
  const moveWidgetToTab = useCallback((widgetId, targetTabId) => {
    setSpec(prev => ({
      ...prev,
      widgets: prev.widgets.map(w => w.id === widgetId
        ? { ...w, tab_id: targetTabId ?? undefined }
        : w),
    }))
  }, [setSpec])

  // Confirm-delete flow: tabs with widgets prompt (move vs delete), empty tabs
  // delete immediately.
  const requestDeleteTab = useCallback((tab) => {
    const count = widgetsForTab(spec, tab.id).length
    if (count === 0) { deleteTab(tab.id, 'delete'); return }
    setDeletingTab(tab)
  }, [spec, deleteTab])

  // Commit a drag/resize/arrow-nudge into the ACTIVE breakpoint only.
  //   desktop (lg) → writes widget.pos (canonical)
  //   tablet (md) / mobile (sm) → writes spec.responsive[bp][id] for moved
  //   widgets, leaving the other breakpoints + untouched widgets alone.
  const commitLayout = useCallback((finalLayout) => {
    const bp = DEVICE_TO_BREAKPOINT[deviceRef.current]
    setHist(h => {
      const newSpec = applyLayoutCommit(h.present, bp, finalLayout)
      frozenLayoutsRef.current = null
      // Skip no-op commits (e.g. a gesture that ended exactly where it started, or
      // a stray commit with the unchanged fallback layout) so we don't (a) pollute
      // the undo history or (b) bake the inherited layout into an override and make
      // a never-edited breakpoint look "customised".
      if (JSON.stringify(newSpec) === JSON.stringify(h.present)) return h
      return historyPush(h, newSpec)
    })
  }, [])

  // NOTE: GridCanvas only fires onLayoutCommit at the END of a real drag/resize (or
  // a reorder), never on mount or on the keyed remount that happens when switching
  // device — so there is no spurious "full layout" commit to guard against the way
  // RGL's onLayoutChange required. The no-op JSON skip above is still a cheap
  // belt-and-braces. All other edits (arrow-key nudges, Configure numeric fields)
  // commit via commitLayout([item]) directly.

  // GridCanvas signals the START / END of a drag-or-resize gesture via
  // onInteractionStart / onInteractionEnd (the latter fires on commit AND cancel).
  // We mirror the old RGL drag/resize start/stop bookkeeping: freeze the layouts
  // during the gesture (frozenLayoutsRef, read in the gridLayouts memo) and drive
  // the column-guide overlay. The actual geometry commit arrives separately via
  // onLayoutCommit → commitLayout, so the freeze is cleared inside commitLayout.
  const handleInteractionStart = useCallback(() => { isDraggingRef.current = true }, [])
  const handleInteractionEnd = useCallback(() => {
    isDraggingRef.current = false
    // A cancelled gesture fires onInteractionEnd WITHOUT a commit; drop the freeze
    // so the next render reflects the unchanged spec.
    frozenLayoutsRef.current = null
  }, [])

  // Mobile (reorder) height stepper: bump a widget's height by ±1 grid row at the
  // ACTIVE breakpoint, routed through the same single-breakpoint commit path.
  const handleHeightStep = useCallback((id, delta) => {
    const bp = DEVICE_TO_BREAKPOINT[deviceRef.current]
    setHist(h => {
      const prev = h.present
      const w = prev.widgets.find(x => x.id === id)
      if (!w) return h
      const base = bp === 'lg'
        ? (w.pos ?? { x: 1, y: 1, w: 4, h: 4 })
        : { ...(w.pos ?? { x: 1, y: 1, w: 4, h: 4 }), ...(prev.responsive?.[bp]?.[id] ?? {}) }
      const mins = WIDGET_MIN_SIZES[w.type] ?? { minW: 2, minH: 2 }
      const nextH = Math.max(mins.minH, (base.h ?? 4) + delta)
      const item = { i: id, x: (base.x ?? 1) - 1, y: (base.y ?? 1) - 1, w: base.w ?? 4, h: nextH }
      const newSpec = applyLayoutCommit(prev, bp, [item])
      if (JSON.stringify(newSpec) === JSON.stringify(prev)) return h
      return historyPush(h, newSpec)
    })
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      let board
      if (savedBoardId) {
        board = await put(`/boards/${savedBoardId}`, { name: spec.title, config: { spec } })
      } else {
        board = await post('/boards', { name: spec.title, config: { spec } })
        setSavedBoardId(board.id)
      }
      savedSpecRef.current = spec  // mark clean
      toast.success('Dashboard saved')
      onSaved?.(board)
    } catch (err) {
      const msg = err.message ?? 'Save failed.'
      setSaveError(msg)
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  // Save affordance shown while the Configure or Dashboard (layout) panel is
  // open, so committing a widget's setup or the grid/layout settings has an
  // obvious, in-context action rather than relying on the user to notice the
  // top-bar button. Same handleSave — dashboards persist as one spec, there's
  // no separate per-widget or per-panel save endpoint.
  const renderSaveFooter = () => (
    <div className="shrink-0 border-t border-border px-3 py-2.5 bg-surface-2/30 flex items-center justify-between gap-2">
      <span className="text-[11px] text-muted">
        {dirty ? 'Unsaved changes' : 'All changes saved'}
      </span>
      <button
        onClick={handleSave}
        disabled={saving}
        data-testid="config-panel-save-btn"
        className={[
          'px-3 h-8 inline-flex items-center gap-1.5 text-xs font-medium rounded-lg transition-all duration-150',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 whitespace-nowrap',
          saving
            ? 'bg-primary/70 text-primary-fg cursor-not-allowed'
            : 'bg-primary text-primary-fg hover:opacity-90 active:scale-[0.97]',
        ].join(' ')}
      >
        {saving
          ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
          : <><Save size={13} /> {savedBoardId ? 'Save' : 'Create'}</>
        }
      </button>
    </div>
  )

  const handleAIApply = useCallback((aiSpec, mode) => {
    if (!aiSpec) return
    if (mode === 'replace') {
      setSpec({
        ...DEFAULT_SPEC,
        ...aiSpec,
        layout: { cols: 12, row_height: 60, ...(aiSpec.layout ?? {}) },
        widgets: aiSpec.widgets ?? [],
      })
      setSelectedId(null)
      setPreview(false)
      return
    }
    // mode === 'merge'
    setSpec(prev => {
      const existingMaxY = prev.widgets.reduce((max, w) => Math.max(max, (w.pos?.y ?? 1) + (w.pos?.h ?? 4)), 1)
      const existingIds = new Set(prev.widgets.map(w => w.id))
      const incomingWidgets = (aiSpec.widgets ?? []).map(w => {
        let newId = w.id
        if (existingIds.has(newId)) { _idCounter += 1; newId = `${w.type ?? 'w'}_ai_${_idCounter}` }
        existingIds.add(newId)
        const minOrigY = (aiSpec.widgets ?? []).reduce((mn, iw) => Math.min(mn, iw.pos?.y ?? 1), w.pos?.y ?? 1)
        const offsetY = existingMaxY - minOrigY
        return { ...w, id: newId, pos: { ...(w.pos ?? { x: 1, y: 1, w: 4, h: 4 }), y: (w.pos?.y ?? 1) + offsetY } }
      })
      return { ...prev, widgets: [...prev.widgets, ...incomingWidgets] }
    })
    setPreview(false)
  }, [])

  // ── Right-panel body (shared by desktop sidebar + mobile sheet) ───────────
  const RIGHT_PANEL_TITLES = { add: 'Add widget', config: 'Configure', chat: 'Chat', board: 'Dashboard', tabs: 'Tabs' }
  const renderRightPanelBody = () => {
    if (rightPanel === 'add') return <AddPanel onAdd={addWidget} />
    if (rightPanel === 'board') return <DashboardPanel spec={spec} onSpecChange={setSpec} />
    if (rightPanel === 'tabs') return <TabsPanel spec={spec} onSpecChange={setSpec} activeTabId={activeTabId} onActivate={setActiveTabIdRaw} onAddTab={addTab} />
    if (rightPanel === 'chat') return <ChatPanel boardId={savedBoardId} spec={spec} onApplySpec={handleAIApply} />
    return (
      <ConfigPanel
        widget={selectedWidget}
        onChange={updateWidget}
        onRemove={() => selectedWidget && removeWidget(selectedWidget.id)}
        extraQueryIds={queryIds}
        spec={spec}
        activeBreakpoint={activeBreakpoint}
        onLayoutCommit={(item) => commitLayout([item])}
        onMoveToTab={moveWidgetToTab}
      />
    )
  }

  // ── Loading state ─────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 min-h-0 gap-3 bg-bg">
        <Spinner size="lg" label="Loading board" />
        <p className="text-sm text-muted">Loading board…</p>
      </div>
    )
  }

  // ── Render ────────────────────────────────────────────────────────────────
  // Static shell: the whole editor is a fixed h-screen flex column that never
  // scrolls. Only the center <main> canvas (and scrollable regions *inside*
  // the fixed-width sidebar) scroll. Topbar + sidebar stay put on resize.
  // The editor's toolbar is portaled INTO the single app top bar (AppTopbar's
  // slot) so there is one bar, not a stacked second one. It closes over live
  // editor state, so it updates normally.
  // Reset the canvas view: snap zoom back to automatic "fit" AND drop any custom
  // per-device width overrides (back to the DEVICE_WIDTHS defaults).
  const resetView = () => {
    setZoomMode('fit')
    setDeviceWidths({ tablet: DEVICE_WIDTHS.tablet, mobile: DEVICE_WIDTHS.mobile })
  }

  // Zoom controls (out / fit / in) + Reset — moved out of the floating canvas bar
  // into the app top bar. Also rendered in the mobile slide-out menu via the same
  // function. Pinch / ctrl+wheel zoom still works via the canvas effect.
  const zoomControls = () => (
    <div className="flex items-center gap-0.5" data-testid="zoom-control">
      <button
        onClick={() => setZoomMode(z => Math.max(MIN_ZOOM, (z === 'fit' ? fitZoom : z) - 0.1))}
        title="Zoom out" aria-label="Zoom out"
        className="w-8 h-8 flex items-center justify-center rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <ZoomOut size={14} />
      </button>
      <button
        onClick={() => setZoomMode('fit')}
        title="Fit to screen"
        className={[
          'text-[11px] font-medium px-2 h-8 rounded-lg border transition-all duration-150 whitespace-nowrap',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
          zoomMode === 'fit'
            ? 'bg-primary text-primary-fg border-primary'
            : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
        ].join(' ')}
      >
        {zoomMode === 'fit' ? `Fit · ${Math.round(effectiveZoom * 100)}%` : `${Math.round(effectiveZoom * 100)}%`}
      </button>
      <button
        onClick={() => setZoomMode(z => Math.min(MAX_ZOOM, (z === 'fit' ? fitZoom : z) + 0.1))}
        title="Zoom in" aria-label="Zoom in"
        className="w-8 h-8 flex items-center justify-center rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <ZoomIn size={14} />
      </button>
      <button
        onClick={resetView}
        title="Reset view (fit zoom + default widths)" aria-label="Reset view"
        data-testid="reset-view"
        className="w-8 h-8 flex items-center justify-center rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <Maximize2 size={13} />
      </button>
    </div>
  )

  // Device viewport switcher — shared by the top bar and the mobile slide-out.
  const deviceSwitcher = (
    <div className="flex items-center rounded-lg border border-border bg-surface overflow-hidden" data-testid="device-switcher">
      {DEVICES.map((d, i) => (
        <button key={d.id} onClick={() => setDevice(d.id)} title={d.label} aria-label={d.label} aria-pressed={device === d.id}
          className={`flex items-center justify-center w-8 h-8 transition-colors ${i > 0 ? 'border-l border-border' : ''} ${
            device === d.id ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-fg'
          }`}>
          <d.Icon size={15} />
        </button>
      ))}
    </div>
  )

  // Panel toggle icon buttons — Add / Configure / Layout / Chat. Shared by the top
  // bar (md+) and the mobile slide-out. `compact` stacks them as full-width rows.
  const PANEL_SEGMENTS = [
    { id: 'add',    Icon: Plus,         mobileSheet: 'palette', label: 'Add widget', title: 'Add widget',                ariaLabel: 'Add widget panel' },
    { id: 'config', Icon: Settings2,    mobileSheet: 'config',  label: 'Configure',  title: 'Configure selected widget', ariaLabel: 'Configure panel' },
    { id: 'board',  Icon: LayoutGrid,   mobileSheet: 'board',   label: 'Layout',     title: 'Layout, grid & variables',  ariaLabel: 'Layout panel' },
    { id: 'tabs',   Icon: Heading,      mobileSheet: 'tabs',    label: 'Tabs',       title: 'Tab bar & per-tab style',   ariaLabel: 'Tabs panel' },
    { id: 'chat',   Icon: MessageSquare,mobileSheet: 'chat',    label: 'Chat',       title: 'AI Chat',                   ariaLabel: 'Chat panel' },
  ]
  const panelToggles = (compact = false) => (
    <div className={compact ? 'flex flex-col gap-1' : 'flex items-center gap-0.5'} data-testid="editor-panel-toggles">
      {PANEL_SEGMENTS.map(seg => {
        const isActiveDesktop = rightPanel === seg.id && !rightCollapsed
        const isActiveMobile = mobileSheet === seg.mobileSheet
        return (
          <button
            key={seg.id}
            data-testid={`panel-toggle-${seg.id}`}
            title={seg.title}
            aria-label={seg.ariaLabel}
            aria-pressed={isActiveDesktop || isActiveMobile}
            onClick={() => {
              // Desktop/tablet: toggle RHS sidebar
              if (window.innerWidth >= 768) {
                if (rightPanel === seg.id && !rightCollapsed) {
                  setRightCollapsed(true)
                } else {
                  setRightPanel(seg.id)
                  setRightCollapsed(false)
                }
              }
              // Mobile: toggle bottom sheet + close the slide-out menu
              setMobileSheet(s => s === seg.mobileSheet ? null : seg.mobileSheet)
              if (compact) setMobileMenu(false)
            }}
            className={`
              ${compact ? 'w-full h-9 justify-start gap-2 px-3' : 'w-9 h-8 justify-center'}
              flex items-center rounded-lg border
              transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring/60
              ${(isActiveDesktop || isActiveMobile)
                ? 'bg-primary text-primary-fg border-primary shadow-sm'
                : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2'
              }
            `}
          >
            <seg.Icon size={15} strokeWidth={2} />
            {compact && <span className="text-sm font-medium">{seg.label}</span>}
          </button>
        )
      })}
    </div>
  )

  const editorToolbar = (
    <div className="flex items-center gap-1.5 w-full min-w-0 overflow-x-auto">
      <input
        type="text"
        data-testid="editor-title"
        className="min-w-[80px] max-w-[180px] sm:max-w-[260px] flex-shrink h-8 text-sm font-semibold border border-transparent rounded-lg px-2.5 bg-transparent text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60 focus:border-border focus:bg-surface transition-all duration-150"
        value={spec.title}
        onChange={e => setSpec(prev => ({ ...prev, title: e.target.value }))}
        placeholder="Dashboard title…"
      />

      {loadError && (
        <span className="hidden lg:inline text-xs px-2 py-1 rounded-lg border whitespace-nowrap"
          style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
          {loadError}
        </span>
      )}

      <div className="flex items-center gap-1 ml-auto shrink-0">
        {/* Dirty pill — subtle amber indicator */}
        {dirty && !saving && (
          <span
            className="hidden sm:inline-flex items-center gap-1 text-[11px] px-2 h-6 rounded-md border whitespace-nowrap"
            style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#b45309', borderColor: 'color-mix(in srgb, #f59e0b 25%, transparent)' }}
            title="You have unsaved changes"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
            Unsaved
          </span>
        )}

        {!preview && (
          <div className="flex items-center gap-0.5">
            <button
              onClick={handleUndo}
              disabled={!canUndo(hist)}
              title="Undo (⌘Z / Ctrl+Z)"
              aria-label="Undo"
              className="w-8 h-8 flex items-center justify-center rounded-lg border border-transparent text-muted hover:border-border hover:bg-surface-2 hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            >
              <Undo2 size={14} />
            </button>
            <button
              onClick={handleRedo}
              disabled={!canRedo(hist)}
              title="Redo (⇧⌘Z / Ctrl+Y)"
              aria-label="Redo"
              className="w-8 h-8 flex items-center justify-center rounded-lg border border-transparent text-muted hover:border-border hover:bg-surface-2 hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            >
              <Redo2 size={14} />
            </button>
          </div>
        )}

        {!preview && <ToolbarDivider />}

        {/* Toolbar cluster (device switcher · zoom · panel toggles).
            md+ : shown inline in the top bar.
            <md : collapsed behind the hamburger → mobile slide-out menu. */}
        <div className="hidden md:flex items-center gap-1.5">
          {/* Device viewport switcher — shown in edit + preview so preview frames per-device. */}
          {deviceSwitcher}
          {/* Canvas zoom controls + Reset (edit mode only) — grouped behind one
              popover trigger instead of 4 inline buttons, so the bar reads as
              "Zoom" rather than a wall of icons. */}
          {!preview && (
            <ToolbarPopover
              icon={ZoomIn}
              label="Zoom"
              title="Canvas zoom & reset"
              testId="editor-zoom-menu"
              active={zoomMode !== 'fit'}
            >
              {zoomControls()}
            </ToolbarPopover>
          )}
          {/* Panel toggles — Add / Configure / Layout / Tabs / Chat. */}
          {!preview && (
            <>
              <ToolbarDivider />
              <div className="flex items-center gap-0.5">{panelToggles()}</div>
            </>
          )}
        </div>

        {/* Hamburger (<md): opens the slide-out with the whole toolbar cluster. */}
        <button
          onClick={() => setMobileMenu(true)}
          title="Menu"
          aria-label="Open editor menu"
          data-testid="editor-hamburger"
          className="md:hidden flex items-center justify-center w-9 h-8 rounded-lg border border-border bg-surface text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <Menu size={16} />
        </button>

        <ToolbarDivider />

        {/* View switcher — Canvas / Code */}
        <div
          className="flex h-8 rounded-lg border border-border bg-surface overflow-hidden shrink-0"
          data-testid="dashboard-view-switcher"
        >
          {[
            { id: 'canvas', Icon: LayoutDashboard, title: 'Canvas / grid view' },
            { id: 'code', Icon: FileCode2, title: 'Code view (dashboard.json)' },
          ].map((v, i) => {
            const active = v.id === 'code' ? codeView : (!codeView && !preview)
            return (
              <button
                key={v.id}
                onClick={() => { setCodeView(v.id === 'code'); if (v.id === 'code') setPreview(false) }}
                title={v.title}
                aria-label={v.title}
                aria-pressed={active}
                className={[
                  'flex items-center justify-center w-8 transition-all duration-150',
                  'focus:outline-none focus-visible:ring-inset focus-visible:ring-2 focus-visible:ring-ring/60',
                  i > 0 ? 'border-l border-border' : '',
                  active
                    ? 'bg-primary text-primary-fg'
                    : 'text-muted hover:text-fg hover:bg-surface-2',
                ].join(' ')}
              >
                <v.Icon size={14} />
              </button>
            )
          })}
        </div>

        {!codeView && (
          <button
            onClick={() => setPreview(p => !p)}
            title={preview ? 'Back to editing' : 'Preview dashboard'}
            aria-label={preview ? 'Back to editing' : 'Preview dashboard'}
            aria-pressed={preview}
            className={[
              'w-8 h-8 flex items-center justify-center rounded-lg border transition-all duration-150',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
              preview
                ? 'bg-primary text-primary-fg border-primary hover:opacity-90'
                : 'bg-surface text-muted border-border hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            {preview ? <Pencil size={14} /> : <Eye size={14} />}
          </button>
        )}

        <div className="hidden sm:block">
          <ExportShareMenu board={savedBoardId} spec={spec} />
        </div>

        <ToolbarDivider />

        <button
          onClick={handleSave}
          disabled={saving}
          data-testid="editor-save-btn"
          title={savedBoardId ? 'Save dashboard (⌘S)' : 'Create dashboard'}
          className={[
            'px-3 h-8 inline-flex items-center gap-1.5 text-xs font-medium rounded-lg transition-all duration-150',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 whitespace-nowrap',
            saving
              ? 'bg-primary/70 text-primary-fg cursor-not-allowed'
              : 'bg-primary text-primary-fg hover:opacity-90 active:scale-[0.97]',
          ].join(' ')}
        >
          {saving
            ? <><Loader2 size={13} className="animate-spin" /> Saving…</>
            : <><Save size={13} /> {savedBoardId ? 'Save' : 'Create'}</>
          }
        </button>
      </div>
    </div>
  )

  // Render the mobile/tablet sheet body based on mobileSheet state
  const renderSheetBody = () => {
    if (mobileSheet === 'palette') return <AddPanel onAdd={(t) => { addWidget(t); setMobileSheet(null) }} />
    if (mobileSheet === 'board') return <DashboardPanel spec={spec} onSpecChange={setSpec} />
    if (mobileSheet === 'tabs') return <TabsPanel spec={spec} onSpecChange={setSpec} activeTabId={activeTabId} onActivate={setActiveTabIdRaw} onAddTab={addTab} />
    if (mobileSheet === 'chat') return <ChatPanel boardId={savedBoardId} spec={spec} onApplySpec={handleAIApply} />
    if (mobileSheet === 'config') return (
      <ConfigPanel
        widget={selectedWidget}
        onChange={updateWidget}
        onRemove={() => { if (selectedWidget) { removeWidget(selectedWidget.id); setMobileSheet(null) } }}
        extraQueryIds={queryIds}
        spec={spec}
        activeBreakpoint={activeBreakpoint}
        onLayoutCommit={(item) => commitLayout([item])}
        onMoveToTab={moveWidgetToTab}
      />
    )
    return null
  }

  const SHEET_TITLES = {
    palette: '+ Add Widget',
    config: selectedWidget ? `Configure · ${selectedWidget.type}` : 'Configure',
    chat: 'Chat',
    board: 'Layout',
    tabs: 'Tabs',
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden bg-bg">

      {/* Editor toolbar lives in the single app top bar (portaled into AppTopbar). */}
      {topbarSlot && createPortal(editorToolbar, topbarSlot)}

      {/* ── Code view — full-pane VS Code-style dashboard.json editor ── */}
      {codeView ? (
        <div className="flex-1 min-h-0 overflow-hidden">
          <DashboardCodeView spec={spec} onSpecChange={setSpec} board={savedBoardId} />
        </div>
      ) : preview ? (
        <main ref={mainRef} className="flex-1 overflow-auto p-3 sm:p-6 bg-bg" style={{ touchAction: 'pan-x pan-y' }}>
          <div ref={canvasRef} className="w-full">
            <div
              className="mx-auto transition-[width,height]"
              style={{
                width: designWidth * effectiveZoom,
                height: effectiveZoom !== 1 ? deviceDashHeight * effectiveZoom : undefined,
              }}
            >
              <div
                className={device === 'desktop' ? '' : 'rounded-2xl border border-border shadow-md bg-bg overflow-hidden'}
                style={{
                  width: designWidth,
                  transform: effectiveZoom !== 1 ? `scale(${effectiveZoom})` : undefined,
                  transformOrigin: 'top left',
                }}
              >
                <SpecRenderer spec={spec} forceBreakpoint={activeBreakpoint} />
              </div>
            </div>
          </div>
        </main>
      ) : (
        /* ── Edit mode ── */
        <div className="flex flex-col flex-1 min-h-0 overflow-hidden relative">

          {/* ── Tab strip (T5): only shown once the dashboard has tabs ── */}
          {hasTabs && (
            <EditableTabStrip
              tabs={spec.tabs}
              activeTabId={activeTabId}
              onActivate={setActiveTabIdRaw}
              onAdd={addTab}
              onRename={renameTab}
              onReorder={reorderTabs}
              onDeleteRequest={requestDeleteTab}
            />
          )}

          <div className="flex flex-1 min-h-0 overflow-hidden relative">

          {/* ── Tablet slide-over backdrop (md only, not on lg+) ── */}
          {!rightCollapsed && (
            <div
              className="hidden md:block lg:hidden fixed inset-0 z-20 bg-black/30"
              onClick={() => setRightCollapsed(true)}
            />
          )}

          {/* Center: grid canvas — the ONLY scrolling region in edit mode. Doubles
              as the zoom/pan viewport (pinch to zoom, one-finger drag to pan). */}
          <main
            ref={mainRef}
            className="flex-1 min-w-0 overflow-auto p-3 sm:p-4 bg-bg"
            data-testid="editor-canvas"
            style={{ ...backgroundToCss(canvasBackground), touchAction: 'pan-x pan-y' }}
            onClick={(e) => {
              // Click on empty canvas → deselect
              if (e.target === e.currentTarget) setSelectedId(null)
            }}
          >
            {/* Canvas top bar: viewport width + custom-layout indicators
                (device switcher now lives in the app top bar) */}
            <div className="flex items-center gap-2 mb-3 flex-wrap empty:hidden">
              {/* Custom screen width (tablet/mobile only) */}
              {device !== 'desktop' && (
                <div className="flex items-center gap-1" data-testid="device-width-control">
                  {WIDTH_PRESETS.map(w => (
                    <button key={w} onClick={() => setDeviceWidths(s => ({ ...s, [device]: w }))}
                      title={`${w}px wide`}
                      className={`text-[10px] font-medium px-1.5 h-6 rounded-md border transition-colors ${
                        deviceWidths[device] === w ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-muted border-border hover:text-fg'
                      }`}>{w}</button>
                  ))}
                  <input
                    type="number" min={200} max={2000}
                    value={deviceWidths[device]}
                    onChange={e => { const v = parseInt(e.target.value, 10); if (v) setDeviceWidths(s => ({ ...s, [device]: v })) }}
                    className="w-14 h-6 text-[10px] px-1.5 rounded-md border border-border bg-surface text-fg focus:outline-none focus:ring-1 focus:ring-ring/50"
                  />
                  <span className="text-[10px] text-muted">px</span>
                </div>
              )}

              {/* Zoom controls now live in the app top bar (and mobile slide-out). */}
              {device !== 'desktop' && (() => {
                const custom = hasOverrides(spec, activeBreakpoint)
                return (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span
                      title={custom
                        ? 'This size has a custom layout — edits here only affect this breakpoint.'
                        : 'This size inherits the desktop layout. Move/resize a widget to start a custom layout.'}
                      className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 h-5 rounded-md border whitespace-nowrap ${
                        custom
                          ? 'bg-primary/10 text-primary border-primary/30'
                          : 'bg-surface-2 text-muted border-border'
                      }`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${custom ? 'bg-primary' : 'bg-muted/50'}`} />
                      {custom ? 'Custom layout' : 'Inherits desktop'}
                    </span>
                    {custom && (
                      <button
                        onClick={() => setSpec(prev => clearBreakpointOverrides(prev, activeBreakpoint))}
                        title="Reset this size to the desktop layout"
                        className="text-[10px] font-medium px-1.5 h-5 rounded-md border border-border bg-surface text-muted hover:text-fg hover:border-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
                        Reset to desktop
                      </button>
                    )}
                  </div>
                )
              })()}

              {/* Mobile panel toggles are now in the topbar (panel-toggle-* buttons) */}
            </div>

            {tabWidgets.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 sm:py-24 text-center rounded-2xl mx-2 min-h-[50vh] border-2 border-dashed border-border/60 bg-surface/30 transition-colors">
                {/* Grid icon */}
                <div className="w-14 h-14 rounded-2xl bg-surface-2 flex items-center justify-center mb-4 shadow-sm">
                  <svg className="w-7 h-7 text-muted/40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.2}
                      d="M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM14 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zM14 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
                  </svg>
                </div>
                <p className="text-sm font-semibold text-fg mb-1.5">
                  {hasTabs ? 'This tab is empty' : 'Your dashboard is empty'}
                </p>
                <p className="text-xs text-muted/70 mb-6 px-6 leading-relaxed max-w-xs">
                  <span className="md:hidden">Tap <strong className="text-fg/80">Add</strong> below, or </span>
                  <span className="hidden md:inline">Use the <strong className="text-fg/80">Add</strong> panel on the right, or </span>
                  ask <strong className="text-fg/80">Chat</strong> to build one for you.
                </p>
                <div className="flex flex-wrap gap-2 justify-center px-4">
                  {PALETTE_ITEMS.filter(p => ['kpi','table','chart','text'].includes(p.type)).map(p => {
                    const Icon = p.icon
                    return (
                      <button
                        key={p.type}
                        onClick={() => addWidget(p.type)}
                        className={[
                          'flex items-center gap-1.5 px-3 py-1.5 min-h-[40px] sm:min-h-0',
                          'text-xs font-medium rounded-lg border border-border bg-surface',
                          'hover:border-primary/60 hover:text-primary hover:bg-surface-2/60',
                          'text-muted transition-all duration-150',
                          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                        ].join(' ')}
                      >
                        <Icon size={13} />
                        {p.label}
                      </button>
                    )
                  })}
                </div>
              </div>
            ) : (
              <div
                ref={canvasRef}
                className="w-full"
                onClick={(e) => {
                  // Click directly on the grid container background → deselect
                  if (e.target === e.currentTarget) setSelectedId(null)
                }}
              >
                {/* Outer: reserves the scaled frame's footprint so the viewport
                    scrolls/pans. Inner: the true-design-width frame, CSS-scaled. */}
                <div
                  className="mx-auto transition-[width,height]"
                  style={{
                    width: designWidth * effectiveZoom,
                    height: effectiveZoom !== 1 ? deviceDashHeight * effectiveZoom : undefined,
                  }}
                >
                <div
                  className={device === 'desktop' ? '' : 'rounded-2xl border border-border shadow-md bg-bg overflow-hidden'}
                  style={{
                    width: designWidth,
                    transform: effectiveZoom !== 1 ? `scale(${effectiveZoom})` : undefined,
                    transformOrigin: 'top left',
                  }}
                >
                {/* Above-grid filter bar (placement: 'header'). Rendered between the
                    tab strip and the GridCanvas so header filters are visible +
                    editable in-place. Mirrors the renderer's `nubi-filter-bar`.
                    Padded to match the grid's container padding so it lines up. */}
                {headerTabWidgets.length > 0 && (
                  <div style={{ paddingLeft: editorPadding.x, paddingRight: editorPadding.x, paddingTop: editorPadding.y }}>
                    <EditorFilterBar
                      widgets={headerTabWidgets}
                      selectedId={selectedId}
                      onSelect={(id) => { setSelectedId(id); setRightCollapsed(false); setRightPanel(p => p === 'chat' ? p : 'config'); setMobileSheet('config') }}
                      onRemove={removeWidget}
                    />
                  </div>
                )}
                {/* CSS-Grid engine (dnd-kit). GridCanvas owns drag/resize math + zoom
                    scaling and the column-guide / ghost overlays. We feed it the SAME
                    0-based layout array RGL consumed and the SAME commit callback, so
                    the commit contract is byte-for-byte preserved. Desktop/tablet =
                    free 2-D editing; mobile = drag-to-reorder stack. Keyed by
                    activeBreakpoint so a device switch remounts with fresh geometry. */}
                <GridCanvas
                  key={`${activeBreakpoint}:${activeTabId ?? '_'}`}
                  layout={gridLayouts[activeBreakpoint]}
                  cols={editorGridCols}
                  rowHeight={spec.layout.row_height ?? 60}
                  gap={editorGap}
                  padding={editorPadding}
                  width={designWidth}
                  zoom={effectiveZoom}
                  draggable
                  resizable={gridMode === 'grid'}
                  mode={gridMode}
                  compaction={editorCompaction}
                  dense={editorDense}
                  selectedId={selectedId}
                  dragHandle=".drag-handle"
                  onInteractionStart={handleInteractionStart}
                  onInteractionEnd={handleInteractionEnd}
                  onLayoutCommit={commitLayout}
                  renderItem={(item) => {
                    const widget = spec.widgets.find(w => w.id === item.i)
                    if (!widget) return null
                    const isSelected = selectedId === widget.id
                    const isHovered = hoveredId === widget.id
                    const reorder = gridMode === 'reorder'
                    const WidgetIcon = WIDGET_ICONS[widget.type]
                    return (
                      // Note: NO overflow-hidden here — the hover toolbar uses position:absolute
                      // and must not be clipped. Inner areas handle their own overflow.
                      <div
                        data-testid={`widget-${widget.id}`}
                        onClick={(e) => { e.stopPropagation(); setSelectedId(widget.id); setRightCollapsed(false); setRightPanel(p => p === 'chat' ? p : 'config'); setMobileSheet('config') }}
                        onMouseEnter={() => setHoveredId(widget.id)}
                        onMouseLeave={() => setHoveredId(null)}
                        style={styleToCss(widget.style)}
                        className={[
                          'h-full w-full rounded-xl border-2 bg-surface transition-all duration-150 relative flex flex-col',
                          isSelected
                            ? 'border-primary shadow-lg shadow-primary/10'
                            : 'border-border hover:border-primary/30 hover:shadow-sm',
                        ].join(' ')}
                      >
                        {/* Hover toolbar: duplicate + delete (+ height stepper on mobile). */}
                        <WidgetHoverToolbar
                          widget={widget}
                          onDuplicate={duplicateWidget}
                          onDelete={removeWidget}
                          visible={isHovered || isSelected}
                          reorder={reorder}
                          onHeightStep={handleHeightStep}
                        />

                        {/* Drag handle — top strip */}
                        <div
                          className={[
                            'drag-handle h-7 shrink-0 cursor-grab active:cursor-grabbing',
                            'flex items-center gap-1.5 px-2.5 border-b border-border/80',
                            'transition-colors duration-150 select-none rounded-t-[10px]',
                            isSelected
                              ? 'bg-primary/8 hover:bg-primary/12'
                              : 'bg-surface-2/50 hover:bg-surface-2',
                          ].join(' ')}
                          title={reorder ? 'Drag to reorder' : 'Drag to move'}
                        >
                          {/* Grip dots */}
                          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className="text-muted/40 shrink-0">
                            <circle cx="2.5" cy="2.5" r="1" fill="currentColor"/>
                            <circle cx="7.5" cy="2.5" r="1" fill="currentColor"/>
                            <circle cx="2.5" cy="5" r="1" fill="currentColor"/>
                            <circle cx="7.5" cy="5" r="1" fill="currentColor"/>
                            <circle cx="2.5" cy="7.5" r="1" fill="currentColor"/>
                            <circle cx="7.5" cy="7.5" r="1" fill="currentColor"/>
                          </svg>
                          {WidgetIcon && (
                            <WidgetIcon size={11} className={isSelected ? 'text-primary/70' : 'text-muted/50'} />
                          )}
                          <span className={[
                            'text-[11px] font-medium truncate flex-1 capitalize',
                            isSelected ? 'text-primary' : 'text-muted/70',
                          ].join(' ')}>
                            {widget.props?.label || widget.type}
                          </span>
                          {isSelected && (
                            <span className="shrink-0 text-[9px] font-semibold text-primary/80 uppercase tracking-wider">
                              selected
                            </span>
                          )}
                        </div>

                        {/* Live widget preview — scrolls/clips independently */}
                        <div className="flex-1 min-h-0 overflow-hidden rounded-b-[10px]">
                          <WidgetPreview widget={widget} />
                        </div>
                      </div>
                    )
                  }}
                />
                </div>
                </div>
              </div>
            )}
          </main>

          {/* ── Right sidebar ──
              Desktop (lg+): always-visible 320px panel.
              Tablet (md–lg): slide-over drawer (fixed, z-30), toggled by the top-bar buttons.
              Mobile (<md): hidden — uses the bottom sheet instead. */}
          {!rightCollapsed && (
            <aside
              className={[
                'border-l border-border bg-surface flex flex-col overflow-hidden',
                'hidden md:flex',
                'lg:static lg:w-80 lg:shrink-0',
                'md:fixed md:inset-y-0 md:right-0 md:z-30 md:w-80 md:shadow-2xl',
                'lg:shadow-none',
              ].join(' ')}
            >
              {/* Panel header */}
              <div className="flex items-center justify-between px-3 h-10 border-b border-border shrink-0 bg-surface-2/30">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-muted/80">
                  {RIGHT_PANEL_TITLES[rightPanel] ?? 'Configure'}
                </span>
                <button
                  onClick={() => setRightCollapsed(true)}
                  title="Collapse panel"
                  aria-label="Collapse side panel"
                  className="flex items-center justify-center w-7 h-7 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                >
                  <PanelRightClose size={15} />
                </button>
              </div>
              {/* Body scrolls WITHIN the fixed-width sidebar */}
              <div className="flex-1 min-h-0 overflow-y-auto flex flex-col">
                {renderRightPanelBody()}
              </div>
              {((rightPanel === 'config' && selectedWidget) || rightPanel === 'board') && renderSaveFooter()}
            </aside>
          )}
          </div>
        </div>
      )}

      {/* ── Mobile slide-out menu (<md) ──
          Holds the toolbar cluster that the top bar hides below md: device
          switcher, zoom controls (+ Reset), and the panel toggles. Slides in from
          the right; tap the backdrop or × to dismiss. */}
      {mobileMenu && (
        <div className="md:hidden fixed inset-0 z-50 flex justify-end" data-testid="editor-mobile-menu">
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-[2px] transition-opacity"
            onClick={() => setMobileMenu(false)}
          />
          <div className="relative w-72 max-w-[85vw] h-full bg-surface border-l border-border shadow-2xl flex flex-col animate-in slide-in-from-right duration-200">
            <div className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0 bg-surface-2/30">
              <span className="text-sm font-semibold text-fg">Editor options</span>
              <button
                onClick={() => setMobileMenu(false)}
                aria-label="Close menu"
                className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                <X size={16} />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-5">
              <div className="space-y-2">
                <SectionLabel>Device</SectionLabel>
                {deviceSwitcher}
              </div>
              {!preview && (
                <div className="space-y-2">
                  <SectionLabel>Zoom</SectionLabel>
                  {zoomControls()}
                </div>
              )}
              {!preview && (
                <div className="space-y-2">
                  <SectionLabel>Panels</SectionLabel>
                  {panelToggles(true)}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Mobile bottom sheet (<md) ──
          Slides up from the bottom when mobileSheet is set. A fixed overlay with
          a drag handle and scrollable body. Tap the backdrop or × to dismiss. */}
      {!preview && mobileSheet && (
        <div className="md:hidden fixed inset-0 z-40 flex flex-col justify-end" style={{ pointerEvents: 'auto' }}>
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-[2px]"
            onClick={() => setMobileSheet(null)}
          />
          {/* Sheet */}
          <div className="relative bg-surface rounded-t-2xl border-t border-border flex flex-col max-h-[80vh] shadow-2xl">
            {/* Drag pill */}
            <div className="flex justify-center pt-2 shrink-0">
              <div className="w-8 h-1 rounded-full bg-border/60" />
            </div>
            {/* Sheet header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border shrink-0">
              <span className="text-sm font-semibold text-fg">
                {SHEET_TITLES[mobileSheet] ?? 'Panel'}
              </span>
              <button
                onClick={() => setMobileSheet(null)}
                aria-label="Close sheet"
                className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                <X size={16} />
              </button>
            </div>
            {/* Sheet body — scrollable */}
            <div className="flex-1 min-h-0 overflow-y-auto">
              {renderSheetBody()}
            </div>
            {((mobileSheet === 'config' && selectedWidget) || mobileSheet === 'board') && renderSaveFooter()}
          </div>
        </div>
      )}

      {/* ── Delete-tab confirmation (T5) ──
          A tab with widgets prompts: relocate them to another tab, or delete the
          tab AND its widgets. Routed through deleteTab → setHist (undoable). */}
      {deletingTab && (
        <DeleteTabDialog
          tab={deletingTab}
          tabs={spec.tabs ?? []}
          widgetCount={widgetsForTab(spec, deletingTab.id).length}
          onMove={(targetId) => { deleteTab(deletingTab.id, 'move', targetId); setDeletingTab(null) }}
          onDeleteWidgets={() => { deleteTab(deletingTab.id, 'delete'); setDeletingTab(null) }}
          onCancel={() => setDeletingTab(null)}
        />
      )}

      {/* ── Filters drawer (authoring) ──
          Opened by EditorPage's FILTERS button via the nubi:open-filters event
          (OPEN_FILTERS_EVENT seam). Lists the dashboard's filter widgets — the
          ones a viewer would see in the global Filters drawer — and lets the
          author add a filter or jump straight to one's settings. The drawer is
          global across tabs (filter variables are dashboard-wide). */}
      {filtersOpen && (
        <div className="fixed inset-0 z-50 flex justify-end" data-testid="editor-filters-drawer">
          <div className="absolute inset-0 bg-black/40" onClick={() => setFiltersOpen(false)} />
          <div className="relative w-80 max-w-[90vw] h-full bg-surface border-l border-border shadow-2xl flex flex-col">
            <div className="flex items-center justify-between px-4 h-12 border-b border-border shrink-0">
              <span className="text-sm font-semibold text-fg flex items-center gap-2">
                <FilterIcon size={15} className="text-primary" /> Filters
              </span>
              <button onClick={() => setFiltersOpen(false)} aria-label="Close filters"
                className="w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors">
                <X size={16} />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
              <p className="text-xs text-muted/80 leading-relaxed">
                Filter widgets drive dashboard variables. They are shared across all
                tabs. Add one here, then configure its target variable &amp; options.
              </p>
              <button
                onClick={() => { addWidget('filter'); setFiltersOpen(false); setRightPanel('config'); setRightCollapsed(false) }}
                data-testid="filters-drawer-add"
                className="w-full flex items-center justify-center gap-2 h-9 text-sm font-medium rounded-lg border border-dashed border-border text-muted hover:text-primary hover:border-primary transition-colors">
                <Plus size={14} /> Add filter
              </button>
              <div className="space-y-1.5">
                {spec.widgets.filter(w => w.type === 'filter').length === 0 ? (
                  <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-3 text-center">
                    No filters yet.
                  </p>
                ) : (
                  spec.widgets.filter(w => w.type === 'filter').map(w => (
                    <button
                      key={w.id}
                      onClick={() => { setSelectedId(w.id); setRightPanel('config'); setRightCollapsed(false); setFiltersOpen(false) }}
                      data-testid={`filters-drawer-item-${w.id}`}
                      className="w-full flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-surface hover:border-primary text-left transition-colors">
                      <FilterIcon size={14} className="text-muted shrink-0" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-fg truncate">{w.props?.label || w.subtype || 'Filter'}</span>
                        <span className="block text-[11px] text-muted truncate font-mono">{w.target_var || '(no variable)'}</span>
                      </span>
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
