/**
 * DashboardEditor.jsx — Drag-and-drop DashboardSpec editor (Wave EDITOR-2C).
 *
 * Props
 * -----
 * boardId   {string|null}  If set, loads the board via GET /boards/:id on mount.
 * onSaved   {function}     Called with the saved board object after a successful save.
 *
 * Layout
 * ------
 * ┌──────────────────── top bar ──────────────────────────────────────────┐
 * │ title input          [Preview toggle]   [Save]                        │
 * ├── palette (left) ──┬──── RGL canvas (center) ──┬── config panel (right)─┤
 * │ + KPI              │  drag / resize widgets    │  selected widget cfg  │
 * │ + Table            │                           │                       │
 * │ + Chart            │                           │                       │
 * └────────────────────┴───────────────────────────┴───────────────────────┘
 *
 * Spec shape (matches backend spec.py DashboardSpec EXACTLY):
 * {
 *   version: 1,
 *   title: string,
 *   layout: { cols: 12, row_height: 60 },
 *   widgets: [{ id, type, query_id, chart_type, encoding, props, pos:{x,y,w,h} }]
 * }
 *
 * NOTE: pos is 1-based (spec.py) but RGL is 0-based; conversions happen at the
 * RGL boundary (specToRgl / rglToSpec helpers).
 */

import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { ResponsiveGridLayout, useContainerWidth } from 'react-grid-layout'
import { get, post, put } from '../lib/api.js'
import { runArrowQueryById } from '../lib/wasmRuntime.js'
import SpecRenderer from '../dashboards/SpecRenderer.jsx'
import AskAIPanel from './AskAIPanel.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEMO_QUERY_IDS = ['demo_all', 'demo_active', 'demo_points_10k', 'demo_points_100k']

const CHART_TYPES = ['line', 'bar', 'scatter', 'area', 'pie']

const DEFAULT_SPEC = {
  version: 1,
  title: 'New Dashboard',
  layout: { cols: 12, row_height: 60 },
  widgets: [],
}

// ---------------------------------------------------------------------------
// Helpers: spec <-> RGL layout conversions
// ---------------------------------------------------------------------------

/** Convert a spec widget pos (1-based) to an RGL layout item (0-based). */
function specToRgl(widget) {
  const pos = widget.pos ?? { x: 1, y: 1, w: 4, h: 4 }
  return {
    i: widget.id,
    x: Math.max(0, pos.x - 1),
    y: Math.max(0, pos.y - 1),
    w: pos.w,
    h: pos.h,
  }
}

/** Merge RGL layout item back into the spec widget pos (convert 0-based → 1-based). */
function rglToPos(item) {
  return {
    x: item.x + 1,
    y: item.y + 1,
    w: item.w,
    h: item.h,
  }
}

/** Generate a unique widget id. */
let _idCounter = 0
function genId(type) {
  _idCounter += 1
  return `${type}_${_idCounter}`
}

// ---------------------------------------------------------------------------
// Default widget factories
// ---------------------------------------------------------------------------

function makeKpiWidget() {
  return {
    id: genId('kpi'),
    type: 'kpi',
    query_id: 'demo_all',
    chart_type: null,
    encoding: { value: '' },
    props: { label: 'KPI', format: 'number' },
    pos: { x: 1, y: 1, w: 3, h: 3 },
  }
}

function makeTableWidget() {
  return {
    id: genId('table'),
    type: 'table',
    query_id: 'demo_all',
    chart_type: null,
    encoding: {},
    props: { limit: 50, columns: '' },
    pos: { x: 1, y: 1, w: 6, h: 5 },
  }
}

function makeChartWidget() {
  return {
    id: genId('chart'),
    type: 'chart',
    query_id: 'demo_all',
    chart_type: 'bar',
    encoding: { x: '', y: '', color: '' },
    props: {},
    pos: { x: 1, y: 1, w: 6, h: 5 },
  }
}

// ---------------------------------------------------------------------------
// Shared input class helpers (token-based)
// ---------------------------------------------------------------------------

const inputCls = 'w-full text-sm border border-border rounded-lg px-2.5 py-1.5 bg-surface text-fg focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors'
const selectCls = inputCls

// ---------------------------------------------------------------------------
// QueryPicker sub-component
// ---------------------------------------------------------------------------

/**
 * A combined <select> + free-text input for picking a query_id.
 * Shows DEMO_QUERY_IDS as well as any additional ids passed via `extraIds`.
 */
function QueryPicker({ value, onChange, extraIds = [] }) {
  const [freeText, setFreeText] = useState('')
  const allIds = useMemo(() => {
    const set = new Set([...DEMO_QUERY_IDS, ...extraIds])
    if (value && !set.has(value)) set.add(value)
    return [...set]
  }, [extraIds, value])

  return (
    <div className="space-y-1.5">
      <select
        className={selectCls}
        value={allIds.includes(value) ? value : '__custom__'}
        onChange={e => {
          if (e.target.value !== '__custom__') onChange(e.target.value)
        }}
      >
        {allIds.map(id => (
          <option key={id} value={id}>{id}</option>
        ))}
        <option value="__custom__">Custom...</option>
      </select>
      {(!allIds.includes(value) || !value) && (
        <input
          type="text"
          placeholder="Enter query_id..."
          className={inputCls}
          value={freeText || value}
          onChange={e => {
            setFreeText(e.target.value)
            onChange(e.target.value)
          }}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// useColumnIntrospection — run a query once, return field names
// ---------------------------------------------------------------------------

function useColumnIntrospection(queryId) {
  const [columns, setColumns] = useState([])
  const [introspecting, setIntrospecting] = useState(false)

  useEffect(() => {
    if (!queryId) { setColumns([]); return }
    let cancelled = false
    setIntrospecting(true)
    runArrowQueryById(queryId).then(({ table }) => {
      if (!cancelled) {
        setColumns(table.schema.fields.map(f => f.name))
        setIntrospecting(false)
      }
    }).catch(() => {
      if (!cancelled) { setColumns([]); setIntrospecting(false) }
    })
    return () => { cancelled = true }
  }, [queryId])

  return { columns, introspecting }
}

// ---------------------------------------------------------------------------
// ColumnSelect — a small dropdown for picking a column from an introspected list
// ---------------------------------------------------------------------------

function ColumnSelect({ label, value, onChange, columns, optional = false }) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-muted">{label}</label>
      <select
        className={selectCls}
        value={value || ''}
        onChange={e => onChange(e.target.value)}
      >
        {optional && <option value="">— none —</option>}
        {!optional && !value && <option value="">Select column...</option>}
        {columns.map(col => (
          <option key={col} value={col}>{col}</option>
        ))}
      </select>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ChartConfig, KpiConfig, TableConfig — config panel for each widget type
// ---------------------------------------------------------------------------

function ChartConfig({ widget, onChange }) {
  const { columns, introspecting } = useColumnIntrospection(widget.query_id)
  const enc = widget.encoding ?? {}
  const props = widget.props ?? {}

  const setEncoding = (key, val) => onChange({ ...widget, encoding: { ...enc, [key]: val } })
  const setChartType = val => onChange({ ...widget, chart_type: val })

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <label className="block text-xs font-medium text-muted">Chart type</label>
        <select
          className={selectCls}
          value={widget.chart_type || 'bar'}
          onChange={e => setChartType(e.target.value)}
        >
          {CHART_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      {introspecting && <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>}
      <ColumnSelect label="X column" value={enc.x} onChange={v => setEncoding('x', v)} columns={columns} />
      <ColumnSelect label="Y column" value={enc.y} onChange={v => setEncoding('y', v)} columns={columns} />
      <ColumnSelect label="Color column" value={enc.color} onChange={v => setEncoding('color', v)} columns={columns} optional />
    </div>
  )
}

function KpiConfig({ widget, onChange }) {
  const { columns, introspecting } = useColumnIntrospection(widget.query_id)
  const enc = widget.encoding ?? {}
  const props = widget.props ?? {}

  const setEncoding = (key, val) => onChange({ ...widget, encoding: { ...enc, [key]: val } })
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })

  return (
    <div className="space-y-3">
      {introspecting && <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>}
      <ColumnSelect label="Value column" value={enc.value} onChange={v => setEncoding('value', v)} columns={columns} />
      <div className="space-y-1">
        <label className="block text-xs font-medium text-muted">Label</label>
        <input
          type="text"
          className={inputCls}
          value={props.label ?? ''}
          onChange={e => setProps('label', e.target.value)}
        />
      </div>
      <div className="space-y-1">
        <label className="block text-xs font-medium text-muted">Format</label>
        <select
          className={selectCls}
          value={props.format ?? 'number'}
          onChange={e => setProps('format', e.target.value)}
        >
          {['number', 'integer', 'percent', 'currency'].map(f => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
      </div>
    </div>
  )
}

function TableConfig({ widget, onChange }) {
  const props = widget.props ?? {}
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <label className="block text-xs font-medium text-muted">Row limit</label>
        <input
          type="number"
          min={1}
          max={10000}
          className={inputCls}
          value={props.limit ?? 50}
          onChange={e => setProps('limit', parseInt(e.target.value, 10) || 50)}
        />
      </div>
      <div className="space-y-1">
        <label className="block text-xs font-medium text-muted">Columns (comma-separated, optional)</label>
        <input
          type="text"
          placeholder="e.g. id, name, value"
          className={inputCls}
          value={props.columns ?? ''}
          onChange={e => setProps('columns', e.target.value)}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConfigPanel — right-side panel for the selected widget
// ---------------------------------------------------------------------------

function ConfigPanel({ widget, onChange, onRemove, extraQueryIds }) {
  if (!widget) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-sm text-muted py-8 px-4 text-center">
        <svg className="w-8 h-8 text-muted/40 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
        </svg>
        <p className="text-xs">Click a widget to configure it.</p>
      </div>
    )
  }

  const setQueryId = qid => onChange({ ...widget, query_id: qid })

  return (
    <div className="p-4 space-y-5 overflow-y-auto h-full">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-fg capitalize">{widget.type} widget</h3>
        <button
          onClick={onRemove}
          className="text-xs px-2 py-0.5 rounded-lg border border-transparent hover:border-border transition-colors"
          style={{ color: '#ef4444' }}
        >
          Remove
        </button>
      </div>

      {/* Query picker */}
      <div className="space-y-1.5">
        <label className="block text-xs font-medium text-muted">Query ID</label>
        <QueryPicker value={widget.query_id} onChange={setQueryId} extraIds={extraQueryIds} />
      </div>

      <hr className="border-border" />

      {/* Type-specific config */}
      {widget.type === 'chart' && (
        <ChartConfig widget={widget} onChange={onChange} />
      )}
      {widget.type === 'kpi' && (
        <KpiConfig widget={widget} onChange={onChange} />
      )}
      {widget.type === 'table' && (
        <TableConfig widget={widget} onChange={onChange} />
      )}

      {/* Widget id info */}
      <p className="text-xs text-muted/60 pt-2">ID: {widget.id}</p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Palette — left sidebar
// ---------------------------------------------------------------------------

function Palette({ onAdd }) {
  const items = [
    { type: 'kpi',   label: 'KPI',   icon: '▣', desc: 'Single big number' },
    { type: 'table', label: 'Table', icon: '☰', desc: 'Data grid' },
    { type: 'chart', label: 'Chart', icon: '▨', desc: 'Bar / line / scatter...' },
  ]

  return (
    <div className="p-3 space-y-2">
      <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">Add widget</p>
      {items.map(item => (
        <button
          key={item.type}
          onClick={() => onAdd(item.type)}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border border-dashed border-border bg-surface hover:bg-surface-2 hover:border-primary text-fg transition-all group text-left focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <span className="text-lg leading-none group-hover:scale-110 transition-transform text-muted">{item.icon}</span>
          <div>
            <p className="text-sm font-medium text-fg group-hover:text-primary transition-colors">{item.label}</p>
            <p className="text-xs text-muted">{item.desc}</p>
          </div>
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// WidgetCard — the widget as shown on the canvas (while dragging)
// ---------------------------------------------------------------------------

function WidgetCard({ widget, selected, onClick }) {
  // Token-based type accents using CSS custom properties
  const typeStyle = {
    kpi:   { accent: 'text-brand-teal', bg: 'bg-surface-2' },
    table: { accent: 'text-primary',    bg: 'bg-surface-2' },
    chart: { accent: 'text-brand-cyan', bg: 'bg-surface-2' },
  }

  const style = typeStyle[widget.type] ?? { accent: 'text-muted', bg: 'bg-surface-2' }

  return (
    <div
      className={`
        h-full w-full rounded-xl border-2 cursor-pointer select-none transition-all overflow-hidden bg-surface
        ${selected
          ? 'border-primary shadow-lg'
          : 'border-border hover:border-primary/50'
        }
      `}
      onClick={onClick}
    >
      {/* Header */}
      <div className={`px-3 py-2 border-b border-border flex items-center gap-2 ${selected ? 'bg-surface-2' : 'bg-surface'}`}>
        <span className={`text-xs font-semibold uppercase tracking-wider ${style.accent}`}>{widget.type}</span>
        {widget.query_id && (
          <span className="text-xs text-muted truncate">{widget.query_id}</span>
        )}
        {selected && (
          <span className="ml-auto text-xs text-primary font-medium">selected</span>
        )}
      </div>
      {/* Body preview */}
      <div className="px-3 py-2 text-xs text-muted">
        {widget.type === 'chart' && (
          <span>{widget.chart_type ?? 'chart'} — {widget.encoding?.x ?? '?'} vs {widget.encoding?.y ?? '?'}</span>
        )}
        {widget.type === 'kpi' && (
          <span>{widget.props?.label ?? 'KPI'} ({widget.encoding?.value ?? '?'})</span>
        )}
        {widget.type === 'table' && (
          <span>Table · limit {widget.props?.limit ?? 50}</span>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// DashboardEditor — main export
// ---------------------------------------------------------------------------

/**
 * @param {{ boardId?: string|null, onSaved?: (board: object) => void }} props
 */
export default function DashboardEditor({ boardId = null, onSaved }) {
  const [spec, setSpec] = useState(DEFAULT_SPEC)
  const [selectedId, setSelectedId] = useState(null)
  const [preview, setPreview] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [loading, setLoading] = useState(!!boardId)
  const [loadError, setLoadError] = useState(null)
  const [savedBoardId, setSavedBoardId] = useState(boardId)
  // AI panel — toggles open/closed; 'ai' or 'config' determines right-panel mode
  const [rightPanel, setRightPanel] = useState('config') // 'config' | 'ai'

  // RGL needs explicit width — measure the canvas container
  const { width: canvasWidth, containerRef: canvasRef } = useContainerWidth({ initialWidth: 900 })

  // ── Load existing board ─────────────────────────────────────────────────
  useEffect(() => {
    if (!boardId) { setLoading(false); return }
    let cancelled = false
    setLoading(true)
    get(`/boards/${boardId}`)
      .then(board => {
        if (cancelled) return
        const loadedSpec = board?.config?.spec
        if (loadedSpec) {
          setSpec(loadedSpec)
        } else {
          // Board has no spec (legacy html-only board)
          setLoadError('This board has no spec yet. Starting with a blank canvas.')
        }
        setSavedBoardId(board.id ?? boardId)
        setLoading(false)
      })
      .catch(err => {
        if (!cancelled) {
          setLoadError(`Failed to load board: ${err.message}`)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [boardId])

  // ── Derived state ────────────────────────────────────────────────────────
  const selectedWidget = spec.widgets.find(w => w.id === selectedId) ?? null

  const rglLayouts = useMemo(() => {
    const lg = spec.widgets.map(specToRgl)
    const sm = spec.widgets.map((w, idx) => ({
      i: w.id,
      x: 0,
      y: idx * (w.pos?.h ?? 4),
      w: 1,
      h: w.pos?.h ?? 4,
    }))
    return { lg, md: lg, sm }
  }, [spec.widgets])

  // ── Mutations ────────────────────────────────────────────────────────────

  const addWidget = useCallback((type) => {
    let widget
    if (type === 'kpi') widget = makeKpiWidget()
    else if (type === 'table') widget = makeTableWidget()
    else widget = makeChartWidget()

    setSpec(prev => ({ ...prev, widgets: [...prev.widgets, widget] }))
    setSelectedId(widget.id)
    setPreview(false)
  }, [])

  const removeWidget = useCallback((id) => {
    setSpec(prev => ({ ...prev, widgets: prev.widgets.filter(w => w.id !== id) }))
    setSelectedId(prev => prev === id ? null : prev)
  }, [])

  const updateWidget = useCallback((updated) => {
    setSpec(prev => ({
      ...prev,
      widgets: prev.widgets.map(w => w.id === updated.id ? updated : w),
    }))
  }, [])

  /** Called by RGL when layout changes due to drag / resize. */
  const handleLayoutChange = useCallback((currentLayout) => {
    setSpec(prev => ({
      ...prev,
      widgets: prev.widgets.map(w => {
        const item = currentLayout.find(l => l.i === w.id)
        if (!item) return w
        return { ...w, pos: rglToPos(item) }
      }),
    }))
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
      onSaved?.(board)
    } catch (err) {
      setSaveError(err.message ?? 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  /**
   * Called by AskAIPanel when the user clicks "Replace canvas" or "Merge widgets".
   */
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
      const existingMaxY = prev.widgets.reduce((max, w) => {
        const bottom = (w.pos?.y ?? 1) + (w.pos?.h ?? 4)
        return Math.max(max, bottom)
      }, 1)

      const existingIds = new Set(prev.widgets.map(w => w.id))

      const incomingWidgets = (aiSpec.widgets ?? []).map(w => {
        let newId = w.id
        if (existingIds.has(newId)) {
          _idCounter += 1
          newId = `${w.type ?? 'w'}_ai_${_idCounter}`
        }
        existingIds.add(newId)

        const origY = w.pos?.y ?? 1
        const minOrigY = (aiSpec.widgets ?? []).reduce(
          (mn, iw) => Math.min(mn, iw.pos?.y ?? 1),
          origY,
        )
        const offsetY = existingMaxY - minOrigY

        return {
          ...w,
          id: newId,
          pos: {
            ...(w.pos ?? { x: 1, y: 1, w: 4, h: 4 }),
            y: (w.pos?.y ?? 1) + offsetY,
          },
        }
      })

      return {
        ...prev,
        widgets: [...prev.widgets, ...incomingWidgets],
      }
    })
    setPreview(false)
  }, [])

  // ── Loading state ───────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-sm text-muted animate-pulse bg-bg min-h-screen">
        Loading board…
      </div>
    )
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full min-h-screen bg-bg">

      {/* ── Top Bar ── */}
      <header className="shrink-0 bg-surface border-b border-border px-4 py-3 flex items-center gap-3 flex-wrap">
        <input
          type="text"
          className="flex-1 min-w-[160px] text-sm font-semibold border border-border rounded-lg px-3 py-1.5 bg-surface text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors font-display"
          value={spec.title}
          onChange={e => setSpec(prev => ({ ...prev, title: e.target.value }))}
          placeholder="Dashboard title…"
        />

        {loadError && (
          <span className="text-xs px-2 py-1 rounded-lg border"
            style={{ background: 'color-mix(in srgb, #f59e0b 8%, transparent)', color: '#d97706', borderColor: 'color-mix(in srgb, #f59e0b 20%, transparent)' }}>
            {loadError}
          </span>
        )}

        <div className="flex items-center gap-2 ml-auto flex-wrap">
          {saveError && (
            <span className="text-xs" style={{ color: '#ef4444' }}>{saveError}</span>
          )}

          {/* Ask AI toggle — only shown in edit mode */}
          {!preview && (
            <button
              onClick={() => setRightPanel(p => p === 'ai' ? 'config' : 'ai')}
              className={`px-3 py-1.5 text-sm font-medium rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 ${
                rightPanel === 'ai'
                  ? 'bg-primary text-primary-fg border-primary hover:opacity-90'
                  : 'bg-surface text-fg border-border hover:bg-surface-2'
              }`}
              title="Toggle the Ask AI panel"
            >
              ✨ Ask AI
            </button>
          )}

          {/* Preview toggle */}
          <button
            onClick={() => setPreview(p => !p)}
            className={`px-3 py-1.5 text-sm font-medium rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 ${
              preview
                ? 'bg-primary text-primary-fg border-primary hover:opacity-90'
                : 'bg-surface text-fg border-border hover:bg-surface-2'
            }`}
          >
            {preview ? 'Edit' : 'Preview'}
          </button>

          {/* Save */}
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 text-sm font-medium bg-primary text-primary-fg rounded-lg hover:opacity-90 disabled:opacity-60 transition-opacity focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
          >
            {saving ? 'Saving…' : savedBoardId ? 'Save' : 'Create'}
          </button>
        </div>
      </header>

      {/* ── Preview mode ── */}
      {preview ? (
        <div className="flex-1 overflow-auto p-6 bg-bg">
          <SpecRenderer spec={spec} />
        </div>
      ) : (
        /* ── Edit mode ── */
        <div className="flex flex-1 overflow-hidden">
          {/* Left: palette */}
          <aside className="w-48 shrink-0 border-r border-border bg-surface overflow-y-auto hidden sm:block">
            <Palette onAdd={addWidget} />
          </aside>

          {/* Center: RGL canvas */}
          <main ref={canvasRef} className="flex-1 overflow-auto p-4 bg-bg">
            {/* Mobile palette */}
            <div className="flex gap-2 mb-3 sm:hidden flex-wrap">
              {['kpi', 'table', 'chart'].map(t => (
                <button
                  key={t}
                  onClick={() => addWidget(t)}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg border border-dashed border-border bg-surface text-fg hover:bg-surface-2 hover:border-primary transition-all focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  + {t.toUpperCase()}
                </button>
              ))}
            </div>

            {spec.widgets.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-24 text-center bg-surface border-2 border-dashed border-border rounded-xl mx-2">
                <svg className="w-10 h-10 text-muted/30 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM14 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zM14 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
                </svg>
                <p className="text-sm text-muted mb-1">No widgets yet.</p>
                <p className="text-xs text-muted/60">Add a widget from the palette on the left.</p>
              </div>
            ) : (
              <ResponsiveGridLayout
                width={canvasWidth}
                className="layout"
                layouts={rglLayouts}
                breakpoints={{ lg: 1200, md: 768, sm: 0 }}
                cols={{ lg: spec.layout.cols, md: spec.layout.cols, sm: 1 }}
                rowHeight={spec.layout.row_height}
                onLayoutChange={handleLayoutChange}
                isDraggable
                isResizable
                margin={[12, 12]}
                containerPadding={[0, 0]}
                draggableHandle=".drag-handle"
              >
                {spec.widgets.map(widget => (
                  <div key={widget.id}>
                    {/* Drag handle bar */}
                    <div className="drag-handle h-5 bg-surface-2 hover:bg-surface cursor-grab active:cursor-grabbing flex items-center justify-center rounded-t-xl border-b border-border transition-colors">
                      <span className="text-muted/50 text-xs select-none">⋮⋮⋮</span>
                    </div>
                    <div className="h-[calc(100%-1.25rem)]">
                      <WidgetCard
                        widget={widget}
                        selected={selectedId === widget.id}
                        onClick={() => setSelectedId(widget.id)}
                      />
                    </div>
                  </div>
                ))}
              </ResponsiveGridLayout>
            )}
          </main>

          {/* Right: config panel or Ask AI panel */}
          <aside className={`shrink-0 border-l border-border bg-surface overflow-y-auto hidden md:flex md:flex-col transition-all ${rightPanel === 'ai' ? 'w-80' : 'w-64'}`}>
            {/* Tab switcher */}
            <div className="flex border-b border-border shrink-0">
              <button
                onClick={() => setRightPanel('config')}
                className={`flex-1 py-2.5 text-xs font-medium transition-colors focus:outline-none ${
                  rightPanel === 'config'
                    ? 'text-primary border-b-2 border-primary bg-surface-2'
                    : 'text-muted hover:text-fg hover:bg-surface-2'
                }`}
              >
                Configure
              </button>
              <button
                onClick={() => setRightPanel('ai')}
                className={`flex-1 py-2.5 text-xs font-medium transition-colors focus:outline-none ${
                  rightPanel === 'ai'
                    ? 'text-primary border-b-2 border-primary bg-surface-2'
                    : 'text-muted hover:text-fg hover:bg-surface-2'
                }`}
              >
                ✨ Ask AI
              </button>
            </div>

            {rightPanel === 'config' ? (
              <ConfigPanel
                widget={selectedWidget}
                onChange={updateWidget}
                onRemove={() => selectedWidget && removeWidget(selectedWidget.id)}
                extraQueryIds={[]}
              />
            ) : (
              <AskAIPanel onApply={handleAIApply} />
            )}
          </aside>
        </div>
      )}

      {/* Mobile bottom sheet — config or Ask AI panel */}
      {!preview && (rightPanel === 'ai' || selectedWidget) && (
        <div className="md:hidden shrink-0 border-t border-border bg-surface max-h-72 overflow-y-auto flex flex-col">
          {/* Tab switcher */}
          <div className="flex border-b border-border shrink-0">
            <button
              onClick={() => setRightPanel('config')}
              className={`flex-1 py-2.5 text-xs font-medium transition-colors focus:outline-none ${
                rightPanel === 'config'
                  ? 'text-primary border-b-2 border-primary bg-surface-2'
                  : 'text-muted hover:text-fg hover:bg-surface-2'
              }`}
            >
              Configure
            </button>
            <button
              onClick={() => setRightPanel('ai')}
              className={`flex-1 py-2.5 text-xs font-medium transition-colors focus:outline-none ${
                rightPanel === 'ai'
                  ? 'text-primary border-b-2 border-primary bg-surface-2'
                  : 'text-muted hover:text-fg hover:bg-surface-2'
              }`}
            >
              ✨ Ask AI
            </button>
          </div>

          {rightPanel === 'config' ? (
            selectedWidget ? (
              <ConfigPanel
                widget={selectedWidget}
                onChange={updateWidget}
                onRemove={() => removeWidget(selectedWidget.id)}
                extraQueryIds={[]}
              />
            ) : (
              <div className="flex items-center justify-center py-6 text-xs text-muted">
                Select a widget to configure it.
              </div>
            )
          ) : (
            <AskAIPanel onApply={handleAIApply} />
          )}
        </div>
      )}
    </div>
  )
}
