/**
 * SlideCanvas.jsx — T10 Presentation surface editor.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  Toolbar (add slide, aspect toggle, present button)     │
 *   ├───────────┬─────────────────────────────────────────────┤
 *   │ Slide     │  16:9 fixed-aspect canvas (centre)          │
 *   │ Rail      │  abs-positioned widget boxes (drag+resize)  │
 *   │ (thumbs)  ├─────────────────────────────────────────────┤
 *   │           │  Speaker notes (markdown textarea)          │
 *   └───────────┴─────────────────────────────────────────────┘
 *
 * The coordinate space for widget items is 0–100 normalized on both axes,
 * aspect-independent. Pixel conversion: px = value/100 * canvasPx.
 *
 * Widgets are rendered via the same widget components used by the grid
 * surface (imported directly, same as DashboardEditor's WidgetPreview).
 *
 * Keyboard shortcuts:
 *   Ctrl+M   → new slide (append)
 *   Ctrl+D   → duplicate current slide
 *   ArrowLeft / ArrowRight  → navigate slides (no modifier)
 *   Esc      → exit present mode
 *   F5 / Ctrl+Shift+P → enter present mode
 *
 * Present mode: full-screen overlay, arrow-key nav, Esc to exit.
 *
 * Props:
 *   spec         — live dashboard spec (read)
 *   surfaceAPI   — object from useSurfaceState (write)
 */

import {
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
  memo,
  Component,
} from 'react'
import {
  Plus, Copy, Trash2, Play, ChevronLeft, ChevronRight,
  X, GripVertical, Maximize2, Minimize2, AlignJustify,
  SlidersHorizontal, Presentation,
  Wand2,
} from 'lucide-react'
import {
  gridSpecToSlides,
  canGenerateSlides,
  slidesHasContent,
} from '../dashboards/surfaceGenerators.js'
import { VariableProvider } from '../dashboards/VariableStore.jsx'
import ChartWidget from '../dashboards/widgets/ChartWidget.jsx'
import KpiWidget from '../dashboards/widgets/KpiWidget.jsx'
import TableWidget from '../dashboards/widgets/TableWidget.jsx'
import FilterWidget from '../dashboards/widgets/FilterWidget.jsx'
import TextWidget from '../dashboards/widgets/TextWidget.jsx'
import HtmlWidget from '../dashboards/widgets/HtmlWidget.jsx'
import MetricWidget from '../dashboards/widgets/MetricWidget.jsx'
import PivotWidget from '../dashboards/widgets/PivotWidget.jsx'
import SectionWidget from '../dashboards/widgets/SectionWidget.jsx'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ASPECT_RATIOS = {
  '16:9': 9 / 16,
  '4:3': 3 / 4,
}

// Snap grid size in normalized units (100-unit space)
const SNAP = 2
const MIN_W = 10
const MIN_H = 8

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function snap(v) {
  return Math.round(v / SNAP) * SNAP
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v))
}

function genSlideId() {
  return `slide_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
}

/**
 * Convert normalized (0–100) position to pixel position given a canvas size.
 */
function normToPixel(normValue, canvasSize) {
  return (normValue / 100) * canvasSize
}

/**
 * Convert pixel delta to normalized delta given a canvas size.
 */
function pixelToNorm(pixelDelta, canvasSize) {
  return (pixelDelta / canvasSize) * 100
}

// ---------------------------------------------------------------------------
// Widget renderer — same widgets as DashboardEditor, live/data-bound
// ---------------------------------------------------------------------------

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

/** Error boundary so a broken widget doesn't crash the whole slide canvas. */
class WidgetErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: false }
  }
  static getDerivedStateFromError() {
    return { error: true }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="h-full w-full flex items-center justify-center bg-surface-2/50 text-xs text-muted/60">
          {this.props.widgetType}
        </div>
      )
    }
    return this.props.children
  }
}

const WidgetRenderer = memo(function WidgetRenderer({ widget }) {
  const w = useMemo(() => normalizeWidget(widget), [widget])
  return (
    <WidgetErrorBoundary widgetType={widget.type}>
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
                <div className="flex items-center justify-center h-full text-xs text-muted/70">
                  {w.type}
                </div>
              )}
            </>
          )}
        </div>
      </VariableProvider>
    </WidgetErrorBoundary>
  )
}, (prev, next) => {
  const p = prev.widget
  const n = next.widget
  return (
    p.query_id === n.query_id &&
    p.chart_type === n.chart_type &&
    JSON.stringify(p.encoding) === JSON.stringify(n.encoding) &&
    JSON.stringify(p.props) === JSON.stringify(n.props) &&
    p.content === n.content &&
    p.html === n.html &&
    p.type === n.type &&
    p.subtype === n.subtype
  )
})

// ---------------------------------------------------------------------------
// SlideItemBox — single draggable/resizable widget box on the canvas
// ---------------------------------------------------------------------------

const HANDLE_SIZE = 8

const RESIZE_HANDLES = [
  { id: 'se', cursor: 'se-resize', style: { bottom: 0, right: 0 } },
  { id: 'sw', cursor: 'sw-resize', style: { bottom: 0, left: 0 } },
  { id: 'ne', cursor: 'ne-resize', style: { top: 0, right: 0 } },
  { id: 'nw', cursor: 'nw-resize', style: { top: 0, left: 0 } },
  { id: 'n',  cursor: 'n-resize',  style: { top: 0, left: '50%', transform: 'translateX(-50%)' } },
  { id: 's',  cursor: 's-resize',  style: { bottom: 0, left: '50%', transform: 'translateX(-50%)' } },
  { id: 'e',  cursor: 'e-resize',  style: { right: 0, top: '50%', transform: 'translateY(-50%)' } },
  { id: 'w',  cursor: 'w-resize',  style: { left: 0, top: '50%', transform: 'translateY(-50%)' } },
]

function SlideItemBox({
  item,
  widget,
  canvasW,
  canvasH,
  selected,
  onSelect,
  onPositionChange,
  onRemove,
  readOnly = false,
}) {
  const px = normToPixel(item.x, canvasW)
  const py = normToPixel(item.y, canvasH)
  const pw = normToPixel(item.w, canvasW)
  const ph = normToPixel(item.h, canvasH)

  // ----- Drag to move -----
  const handleDragStart = useCallback((e) => {
    if (readOnly) return
    e.stopPropagation()
    onSelect(item.widgetId)
    const startX = e.clientX
    const startY = e.clientY
    const origX = item.x
    const origY = item.y

    const move = (me) => {
      const dx = pixelToNorm(me.clientX - startX, canvasW)
      const dy = pixelToNorm(me.clientY - startY, canvasH)
      const nx = clamp(snap(origX + dx), 0, 100 - item.w)
      const ny = clamp(snap(origY + dy), 0, 100 - item.h)
      onPositionChange(item.widgetId, { ...item, x: nx, y: ny })
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }, [item, canvasW, canvasH, onPositionChange, onSelect, readOnly])

  // ----- Resize handles -----
  const handleResizeStart = useCallback((e, handleId) => {
    if (readOnly) return
    e.stopPropagation()
    e.preventDefault()
    const startX = e.clientX
    const startY = e.clientY
    const orig = { ...item }

    const move = (me) => {
      const dx = pixelToNorm(me.clientX - startX, canvasW)
      const dy = pixelToNorm(me.clientY - startY, canvasH)

      let { x, y, w, h } = orig

      if (handleId.includes('e')) w = clamp(snap(orig.w + dx), MIN_W, 100 - orig.x)
      if (handleId.includes('w')) {
        const nx = clamp(snap(orig.x + dx), 0, orig.x + orig.w - MIN_W)
        w = orig.x + orig.w - nx
        x = nx
      }
      if (handleId.includes('s')) h = clamp(snap(orig.h + dy), MIN_H, 100 - orig.y)
      if (handleId.includes('n')) {
        const ny = clamp(snap(orig.y + dy), 0, orig.y + orig.h - MIN_H)
        h = orig.y + orig.h - ny
        y = ny
      }

      onPositionChange(item.widgetId, { ...item, x, y, w, h })
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }, [item, canvasW, canvasH, onPositionChange, readOnly])

  return (
    <div
      data-slide-item={item.widgetId}
      style={{
        position: 'absolute',
        left: px,
        top: py,
        width: pw,
        height: ph,
        cursor: readOnly ? 'default' : 'move',
        zIndex: selected ? 10 : 1,
        outline: selected && !readOnly ? '2px solid hsl(var(--primary))' : 'none',
        outlineOffset: 1,
        boxSizing: 'border-box',
        userSelect: 'none',
      }}
      onMouseDown={handleDragStart}
      onClick={(e) => { e.stopPropagation(); onSelect(item.widgetId) }}
    >
      {/* Widget content */}
      <div style={{ width: '100%', height: '100%', pointerEvents: 'none', overflow: 'hidden' }}>
        <WidgetRenderer widget={widget} />
      </div>

      {/* Resize handles */}
      {selected && !readOnly && RESIZE_HANDLES.map(h => (
        <div
          key={h.id}
          onMouseDown={(e) => handleResizeStart(e, h.id)}
          style={{
            position: 'absolute',
            width: HANDLE_SIZE,
            height: HANDLE_SIZE,
            background: 'hsl(var(--primary))',
            borderRadius: 2,
            cursor: h.cursor,
            zIndex: 20,
            ...h.style,
          }}
        />
      ))}

      {/* Delete button (shown when selected) */}
      {selected && !readOnly && (
        <button
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onRemove(item.widgetId) }}
          style={{
            position: 'absolute',
            top: -10,
            right: -10,
            width: 20,
            height: 20,
            borderRadius: '50%',
            background: 'hsl(var(--destructive, 0 84% 60%))',
            color: 'white',
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 30,
            fontSize: 10,
            lineHeight: 1,
          }}
          title="Remove widget from slide"
        >
          ✕
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SnapGuides — horizontal + vertical center guides
// ---------------------------------------------------------------------------

function SnapGuides({ canvasW, canvasH, activeItem }) {
  if (!activeItem) return null
  const cx = normToPixel(activeItem.x + activeItem.w / 2, canvasW)
  const cy = normToPixel(activeItem.y + activeItem.h / 2, canvasH)
  const midX = canvasW / 2
  const midY = canvasH / 2
  const showV = Math.abs(cx - midX) < 6
  const showH = Math.abs(cy - midY) < 6

  return (
    <>
      {showV && (
        <div style={{
          position: 'absolute', left: midX - 0.5, top: 0, width: 1, height: canvasH,
          background: 'hsl(var(--primary)/0.5)', pointerEvents: 'none', zIndex: 5,
        }} />
      )}
      {showH && (
        <div style={{
          position: 'absolute', top: midY - 0.5, left: 0, height: 1, width: canvasW,
          background: 'hsl(var(--primary)/0.5)', pointerEvents: 'none', zIndex: 5,
        }} />
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// SlideThumbnail — tiny preview in the rail
// ---------------------------------------------------------------------------

function SlideThumbnail({
  slide,
  index,
  active,
  widgets,
  aspect,
  onSelect,
  onDuplicate,
  onDelete,
  onDragStart,
  onDragOver,
  onDrop,
}) {
  const thumbW = 120
  const thumbH = Math.round(thumbW * (ASPECT_RATIOS[aspect] ?? ASPECT_RATIOS['16:9']))
  const scale = thumbW / 100 // 100-unit space → thumbW px

  return (
    <div
      draggable
      onDragStart={() => onDragStart(slide.id)}
      onDragOver={(e) => { e.preventDefault(); onDragOver(slide.id) }}
      onDrop={() => onDrop(slide.id)}
      data-testid={`slide-thumb-${slide.id}`}
      className={[
        'group relative shrink-0 cursor-pointer rounded-lg border-2 transition-all',
        active ? 'border-primary shadow-md' : 'border-border hover:border-primary/50',
      ].join(' ')}
      style={{ width: thumbW, height: thumbH + 28 }}
      onClick={() => onSelect(slide.id)}
    >
      {/* Slide number */}
      <div className="absolute -top-5 left-0 text-[10px] text-muted font-medium select-none">
        {index + 1}
      </div>

      {/* Thumbnail canvas */}
      <div
        style={{
          width: thumbW,
          height: thumbH,
          position: 'relative',
          overflow: 'hidden',
          background: 'white',
          borderRadius: 5,
        }}
      >
        {slide.items.map(item => {
          const w = widgets.find(w => w.id === item.widgetId)
          if (!w) return null
          return (
            <div
              key={item.widgetId}
              style={{
                position: 'absolute',
                left: item.x * scale,
                top: item.y * scale,
                width: item.w * scale,
                height: item.h * scale,
                overflow: 'hidden',
                pointerEvents: 'none',
                transform: `scale(${scale})`,
                transformOrigin: 'top left',
              }}
            >
              <div style={{ width: `${100 / scale}%`, height: `${100 / scale}%` }}>
                <WidgetRenderer widget={w} />
              </div>
            </div>
          )
        })}
        {slide.items.length === 0 && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <span style={{ fontSize: 10, color: '#aaa' }}>Empty</span>
          </div>
        )}
      </div>

      {/* Hover actions */}
      <div className="absolute bottom-0 inset-x-0 flex justify-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity pb-0.5">
        <button
          onClick={(e) => { e.stopPropagation(); onDuplicate(slide.id) }}
          title="Duplicate slide (Ctrl+D)"
          className="w-5 h-5 rounded flex items-center justify-center bg-surface border border-border text-muted hover:text-primary hover:border-primary transition-colors text-[10px]"
        >
          <Copy size={9} />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(slide.id) }}
          title="Delete slide"
          className="w-5 h-5 rounded flex items-center justify-center bg-surface border border-border text-muted hover:text-red-500 hover:border-red-300 transition-colors"
        >
          <Trash2 size={9} />
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PresentMode — full-screen overlay for presenting
// ---------------------------------------------------------------------------

function PresentMode({ slides, widgets, currentIndex, onExit, aspect }) {
  const [idx, setIdx] = useState(currentIndex)

  const go = useCallback((delta) => {
    setIdx(i => clamp(i + delta, 0, slides.length - 1))
  }, [slides.length])

  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') { onExit(); return }
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ') go(1)
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') go(-1)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [go, onExit])

  const slide = slides[idx]
  if (!slide) return null

  const aspectRatio = ASPECT_RATIOS[aspect] ?? ASPECT_RATIOS['16:9']

  return (
    <div
      data-testid="present-mode"
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: '#000',
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Presentation mode"
    >
      {/* Slide canvas — fills screen maintaining aspect ratio */}
      <div style={{
        position: 'relative',
        maxWidth: '100vw',
        maxHeight: '100vh',
        aspectRatio: `${1 / aspectRatio}`,
        width: '100%',
        background: 'white',
        overflow: 'hidden',
      }}>
        <PresentSlide slide={slide} widgets={widgets} />
      </div>

      {/* Controls */}
      <div style={{
        position: 'fixed', bottom: 24,
        display: 'flex', alignItems: 'center', gap: 12,
        background: 'rgba(0,0,0,0.6)', borderRadius: 8,
        padding: '6px 14px', color: 'white',
      }}>
        <button
          onClick={() => go(-1)}
          disabled={idx === 0}
          style={{ background: 'none', border: 'none', color: idx === 0 ? '#555' : 'white', cursor: idx === 0 ? 'default' : 'pointer', padding: 4 }}
          title="Previous slide (←)"
        >
          <ChevronLeft size={20} />
        </button>
        <span style={{ fontSize: 13, minWidth: 60, textAlign: 'center' }}>
          {idx + 1} / {slides.length}
        </span>
        <button
          onClick={() => go(1)}
          disabled={idx === slides.length - 1}
          style={{ background: 'none', border: 'none', color: idx === slides.length - 1 ? '#555' : 'white', cursor: idx === slides.length - 1 ? 'default' : 'pointer', padding: 4 }}
          title="Next slide (→)"
        >
          <ChevronRight size={20} />
        </button>
        <div style={{ width: 1, background: 'rgba(255,255,255,0.2)', height: 20, margin: '0 4px' }} />
        <button
          onClick={onExit}
          style={{ background: 'none', border: 'none', color: 'white', cursor: 'pointer', padding: 4 }}
          title="Exit (Esc)"
        >
          <X size={16} />
        </button>
      </div>

      {/* Notes overlay (bottom of slide, small) */}
      {slide.notes && (
        <div style={{
          position: 'fixed', bottom: 80,
          maxWidth: '60vw',
          background: 'rgba(0,0,0,0.75)',
          color: 'rgba(255,255,255,0.85)',
          borderRadius: 6, padding: '6px 12px',
          fontSize: 13, textAlign: 'center',
        }}>
          {slide.notes}
        </div>
      )}
    </div>
  )
}

function PresentSlide({ slide, widgets }) {
  // Uses 100vw/100vh as the coordinate space reference via CSS
  return (
    <div style={{ position: 'absolute', inset: 0 }}>
      {slide.items.map(item => {
        const widget = widgets.find(w => w.id === item.widgetId)
        if (!widget) return null
        return (
          <div
            key={item.widgetId}
            style={{
              position: 'absolute',
              left: `${item.x}%`,
              top: `${item.y}%`,
              width: `${item.w}%`,
              height: `${item.h}%`,
              overflow: 'hidden',
            }}
          >
            <WidgetRenderer widget={widget} />
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SlideEditorCanvas — the main 16:9 canvas with drag/resize
// ---------------------------------------------------------------------------

function SlideEditorCanvas({
  slide,
  widgets,
  aspect,
  onPositionChange,
  onRemoveItem,
}) {
  const containerRef = useRef(null)
  const [canvasSize, setCanvasSize] = useState({ w: 800, h: 450 })
  const [selectedId, setSelectedId] = useState(null)

  const aspectRatio = ASPECT_RATIOS[aspect] ?? ASPECT_RATIOS['16:9']

  // Track canvas pixel size
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect()
      if (r.width > 0) {
        setCanvasSize({ w: r.width, h: r.width * aspectRatio })
      }
    })
    ro.observe(el)
    const r = el.getBoundingClientRect()
    if (r.width > 0) setCanvasSize({ w: r.width, h: r.width * aspectRatio })
    return () => ro.disconnect()
  }, [aspectRatio])

  const handleCanvasClick = useCallback(() => {
    setSelectedId(null)
  }, [])

  const selectedItem = slide?.items.find(i => i.widgetId === selectedId)

  if (!slide) {
    return (
      <div className="flex flex-1 items-center justify-center bg-bg text-muted/60 text-sm">
        No slide selected
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="w-full"
      data-testid="slide-editor-canvas"
    >
      <div
        style={{
          position: 'relative',
          width: '100%',
          height: canvasSize.h,
          background: 'white',
          boxShadow: '0 4px 24px rgba(0,0,0,0.12)',
          overflow: 'hidden',
          cursor: 'default',
        }}
        onClick={handleCanvasClick}
      >
        {/* Snap guides */}
        <SnapGuides
          canvasW={canvasSize.w}
          canvasH={canvasSize.h}
          activeItem={selectedItem}
        />

        {/* Widget items */}
        {slide.items.map(item => {
          const widget = widgets.find(w => w.id === item.widgetId)
          if (!widget) return null
          return (
            <SlideItemBox
              key={item.widgetId}
              item={item}
              widget={widget}
              canvasW={canvasSize.w}
              canvasH={canvasSize.h}
              selected={selectedId === item.widgetId}
              onSelect={setSelectedId}
              onPositionChange={onPositionChange}
              onRemove={onRemoveItem}
            />
          )
        })}

        {/* Empty state */}
        {slide.items.length === 0 && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            pointerEvents: 'none',
          }}>
            <Presentation size={32} style={{ opacity: 0.15, marginBottom: 8 }} />
            <p style={{ fontSize: 13, color: '#aaa' }}>
              Drag widgets from the palette to add them here
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SlideCanvas — main export
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   spec: object,
 *   surfaceAPI: {
 *     slidesSurface: { aspect: string, slides: Array },
 *     addSlide: Function,
 *     removeSlide: Function,
 *     reorderSlides: Function,
 *     updateSlide: Function,
 *     updateSlideWidgetPos: Function,
 *   }
 * }} props
 */
export default function SlideCanvas({ spec, surfaceAPI, setSpec }) {
  const {
    slidesSurface,
    addSlide,
    removeSlide,
    reorderSlides,
    updateSlide,
    updateSlideWidgetPos,
  } = surfaceAPI

  const slides = useMemo(() => slidesSurface?.slides ?? [], [slidesSurface])
  const aspect = slidesSurface?.aspect ?? '16:9'
  const widgets = spec?.widgets ?? []

  const [activeSlideId, setActiveSlideId] = useState(() => slides[0]?.id ?? null)
  const [presentMode, setPresentMode] = useState(false)
  const [_dragOverId, setDragOverId] = useState(null)
  const dragSourceIdRef = useRef(null)

  // Keep activeSlideId valid when slides change.
  // Derive the correction inline (during render) rather than inside an effect
  // to avoid the cascading-setState lint rule.
  const activeSlideExists = slides.length === 0 || slides.some(s => s.id === activeSlideId)
  const derivedActiveSlideId = activeSlideExists
    ? (slides.length === 0 ? null : activeSlideId)
    : slides[0].id
  // Sync local state when derived value differs (one extra render at most).
  if (derivedActiveSlideId !== activeSlideId) {
    setActiveSlideId(derivedActiveSlideId)
  }

  const activeSlide = slides.find(s => s.id === activeSlideId) ?? null
  const activeIndex = slides.findIndex(s => s.id === activeSlideId)

  // ── Slide CRUD ──────────────────────────────────────────────────────────────

  const handleAddSlide = useCallback(() => {
    const id = genSlideId()
    addSlide({ id, notes: null, items: [] })
    setActiveSlideId(id)
  }, [addSlide])

  const handleDuplicateSlide = useCallback((slideId) => {
    const src = slides.find(s => s.id === slideId)
    if (!src) return
    const id = genSlideId()
    addSlide({ id, notes: src.notes, items: src.items.map(i => ({ ...i })) })
    setActiveSlideId(id)
  }, [slides, addSlide])

  const handleDeleteSlide = useCallback((slideId) => {
    const idx = slides.findIndex(s => s.id === slideId)
    removeSlide(slideId)
    // Move to adjacent slide
    const remaining = slides.filter(s => s.id !== slideId)
    if (remaining.length > 0) {
      const next = remaining[Math.min(idx, remaining.length - 1)]
      setActiveSlideId(next.id)
    } else {
      setActiveSlideId(null)
    }
  }, [slides, removeSlide])

  // ── Reorder (drag) ──────────────────────────────────────────────────────────

  const handleRailDragStart = useCallback((slideId) => {
    dragSourceIdRef.current = slideId
  }, [])

  const handleRailDragOver = useCallback((slideId) => {
    setDragOverId(slideId)
  }, [])

  const handleRailDrop = useCallback((targetId) => {
    const srcId = dragSourceIdRef.current
    if (!srcId || srcId === targetId) { setDragOverId(null); return }
    const ids = slides.map(s => s.id)
    const srcIdx = ids.indexOf(srcId)
    const tgtIdx = ids.indexOf(targetId)
    if (srcIdx < 0 || tgtIdx < 0) { setDragOverId(null); return }
    const next = [...ids]
    next.splice(srcIdx, 1)
    next.splice(tgtIdx, 0, srcId)
    reorderSlides(next)
    setDragOverId(null)
    dragSourceIdRef.current = null
  }, [slides, reorderSlides])

  // ── Widget positioning ──────────────────────────────────────────────────────

  const handlePositionChange = useCallback((widgetId, pos) => {
    if (!activeSlideId) return
    updateSlideWidgetPos(activeSlideId, widgetId, pos)
  }, [activeSlideId, updateSlideWidgetPos])

  const handleRemoveItem = useCallback((widgetId) => {
    if (!activeSlide) return
    updateSlide(activeSlideId, {
      items: activeSlide.items.filter(i => i.widgetId !== widgetId),
    })
  }, [activeSlide, activeSlideId, updateSlide])

  // ── Notes ──────────────────────────────────────────────────────────────────

  const handleNotesChange = useCallback((e) => {
    if (!activeSlideId) return
    updateSlide(activeSlideId, { notes: e.target.value || null })
  }, [activeSlideId, updateSlide])

  // ── Navigation ─────────────────────────────────────────────────────────────

  const goSlide = useCallback((delta) => {
    const idx = slides.findIndex(s => s.id === activeSlideId)
    const next = slides[clamp(idx + delta, 0, slides.length - 1)]
    if (next) setActiveSlideId(next.id)
  }, [slides, activeSlideId])

  // ── Keyboard shortcuts ─────────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e) => {
      // Only fire when presentation surface is active (not in an input)
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return

      if ((e.ctrlKey || e.metaKey) && e.key === 'm') {
        e.preventDefault()
        handleAddSlide()
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'd') {
        e.preventDefault()
        if (activeSlideId) handleDuplicateSlide(activeSlideId)
      }
      if (e.key === 'ArrowLeft' && !e.ctrlKey && !e.metaKey) goSlide(-1)
      if (e.key === 'ArrowRight' && !e.ctrlKey && !e.metaKey) goSlide(1)
      if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'P')) {
        e.preventDefault()
        if (slides.length > 0) setPresentMode(true)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleAddSlide, handleDuplicateSlide, activeSlideId, goSlide, slides.length])

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div
      className="flex flex-col flex-1 min-h-0 overflow-hidden"
      data-testid="presentation-canvas"
    >
      {/* Present mode overlay */}
      {presentMode && slides.length > 0 && (
        <PresentMode
          slides={slides}
          widgets={widgets}
          aspect={aspect}
          currentIndex={Math.max(0, activeIndex)}
          onExit={() => setPresentMode(false)}
        />
      )}

      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 h-10 border-b border-border bg-surface shrink-0">
        <button
          onClick={handleAddSlide}
          title="New slide (Ctrl+M)"
          className="flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-border bg-surface text-xs font-medium text-muted hover:text-fg hover:border-primary/60 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
        >
          <Plus size={13} />
          <span className="hidden sm:inline">New slide</span>
        </button>
        {setSpec && canGenerateSlides(spec) && (
          <button
            onClick={() => {
              if (slidesHasContent(spec)) {
                if (!window.confirm('This will replace the existing slides. Continue?')) return
              }
              setSpec(prev => gridSpecToSlides(prev, { force: true }))
            }}
            title="Auto-generate slides from dashboard widget layout"
            className="flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-border bg-surface text-xs font-medium text-muted hover:text-primary hover:border-primary/60 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
            data-testid="generate-slides-from-dashboard"
          >
            <Wand2 size={13} />
            <span className="hidden sm:inline">Generate</span>
          </button>
        )}

        {activeSlideId && (
          <>
            <button
              onClick={() => handleDuplicateSlide(activeSlideId)}
              title="Duplicate slide (Ctrl+D)"
              className="flex items-center gap-1 h-7 px-2 rounded-lg border border-border bg-surface text-xs text-muted hover:text-fg hover:border-primary/60 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
            >
              <Copy size={13} />
            </button>
            <button
              onClick={() => handleDeleteSlide(activeSlideId)}
              title="Delete slide"
              className="flex items-center gap-1 h-7 px-2 rounded-lg border border-border bg-surface text-xs text-muted hover:text-red-500 hover:border-red-300 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
            >
              <Trash2 size={13} />
            </button>
          </>
        )}

        <div className="flex-1" />

        {/* Slide counter */}
        {slides.length > 0 && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => goSlide(-1)}
              disabled={activeIndex <= 0}
              className="w-6 h-6 flex items-center justify-center rounded border border-border text-muted hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft size={12} />
            </button>
            <span className="text-xs text-muted tabular-nums min-w-[40px] text-center">
              {activeIndex + 1} / {slides.length}
            </span>
            <button
              onClick={() => goSlide(1)}
              disabled={activeIndex >= slides.length - 1}
              className="w-6 h-6 flex items-center justify-center rounded border border-border text-muted hover:text-fg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight size={12} />
            </button>
          </div>
        )}

        {/* Present button */}
        <button
          onClick={() => { if (slides.length > 0) setPresentMode(true) }}
          disabled={slides.length === 0}
          title="Present (F5 or Ctrl+Shift+P)"
          className="flex items-center gap-1.5 h-7 px-2.5 rounded-lg bg-primary text-primary-fg text-xs font-medium hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity focus:outline-none focus:ring-2 focus:ring-ring/60"
        >
          <Play size={12} fill="currentColor" />
          <span className="hidden sm:inline">Present</span>
        </button>
      </div>

      {/* Body: rail + canvas + notes */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Slide thumbnail rail */}
        <div
          className="flex flex-col gap-5 px-3 py-3 overflow-y-auto shrink-0 border-r border-border bg-surface"
          style={{ width: 152, paddingTop: 24 }}
          data-testid="slide-rail"
        >
          {slides.map((slide, idx) => (
            <SlideThumbnail
              key={slide.id}
              slide={slide}
              index={idx}
              active={slide.id === activeSlideId}
              widgets={widgets}
              aspect={aspect}
              onSelect={setActiveSlideId}
              onDuplicate={handleDuplicateSlide}
              onDelete={handleDeleteSlide}
              onDragStart={handleRailDragStart}
              onDragOver={handleRailDragOver}
              onDrop={handleRailDrop}
            />
          ))}

          {/* Add slide button at bottom of rail */}
          <button
            onClick={handleAddSlide}
            title="Add slide (Ctrl+M)"
            className="flex items-center justify-center gap-1 rounded-lg border-2 border-dashed border-border text-muted hover:border-primary/50 hover:text-primary transition-colors py-2 text-xs"
            style={{ width: 120, height: 36 }}
          >
            <Plus size={13} />
            Add
          </button>
        </div>

        {/* Canvas + notes column */}
        <div className="flex flex-col flex-1 min-h-0 overflow-hidden bg-bg">
          {/* Canvas area */}
          <div className="flex-1 min-h-0 overflow-auto flex items-start justify-center p-6">
            <div style={{ width: '100%', maxWidth: 960 }}>
              <SlideEditorCanvas
                slide={activeSlide}
                widgets={widgets}
                aspect={aspect}
                onPositionChange={handlePositionChange}
                onRemoveItem={handleRemoveItem}
              />
            </div>
          </div>

          {/* Speaker notes */}
          <div
            className="shrink-0 border-t border-border bg-surface"
            style={{ minHeight: 80, maxHeight: 160 }}
          >
            <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border/60">
              <AlignJustify size={12} className="text-muted/60" />
              <span className="text-[11px] font-medium text-muted">Speaker notes</span>
            </div>
            <textarea
              placeholder="Add speaker notes for this slide… (Markdown)"
              value={activeSlide?.notes ?? ''}
              onChange={handleNotesChange}
              className="w-full resize-none px-3 py-2 text-xs text-fg bg-transparent placeholder:text-muted/40 focus:outline-none"
              style={{ height: 'calc(100% - 32px)', minHeight: 48 }}
              data-testid="speaker-notes"
              disabled={!activeSlide}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
