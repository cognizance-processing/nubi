/**
 * EditorShell.jsx — T8 unified editor shell.
 *
 * Wraps the existing DashboardEditor (grid/dashboard surface) with a top-level
 * surface switch: Dashboard | Report | Presentation. Shared chrome (widget
 * palette, inspector, insert ribbon, theme) is owned by DashboardEditor and
 * reused on every surface — only the canvas + the contextual left rail swap.
 *
 * HARD CONSTRAINTS (must not be violated):
 *  - The dashboard/grid surface MUST behave EXACTLY as before. DashboardEditor
 *    is rendered unchanged; the shell is purely additive.
 *  - ONE widget library — DashboardEditor's widget renderer/inspector/palette
 *    stays the single source; report and slides canvases will render the same
 *    widgets (T9/T10/T11).
 *  - Widgets stay LIVE/data-bound on every surface (they share spec.widgets).
 *
 * Shell structure:
 *
 *   <EditorShell>
 *     <SurfaceSwitch />          ← top-level tab bar (Dashboard | Report | Presentation)
 *
 *     surface === 'dashboard'
 *       <DashboardEditor />      ← unchanged; owns the grid canvas + shared chrome
 *
 *     surface === 'report'
 *       <ReportCanvas />         ← placeholder; T9 builds here
 *
 *     surface === 'presentation'
 *       <PresentationCanvas />   ← placeholder; T10 builds here
 *   </EditorShell>
 *
 * Props mirror DashboardEditor (boardId, onSaved) so existing page wiring is
 * a one-line swap: replace <DashboardEditor> with <EditorShell>.
 */

import { useState, useCallback, useRef } from 'react'
import { LayoutDashboard, FileText, Presentation } from 'lucide-react'
import DashboardEditor from './DashboardEditor.jsx'
import { useSurfaceState } from './useSurfaceState.js'
import DocCanvas from './DocCanvas.jsx'
import SlideCanvas from './SlideCanvas.jsx'

// ---------------------------------------------------------------------------
// Surface switch bar — renders above everything
// ---------------------------------------------------------------------------

const SURFACE_TABS = [
  { id: 'dashboard',    label: 'Dashboard',    Icon: LayoutDashboard },
  { id: 'report',       label: 'Report',       Icon: FileText },
  { id: 'presentation', label: 'Presentation', Icon: Presentation },
]

function SurfaceSwitch({ activeSurface, onChange }) {
  return (
    <div
      className="flex items-center gap-0.5 px-2 h-9 border-b border-border bg-surface shrink-0 z-10"
      data-testid="surface-switch"
      role="tablist"
      aria-label="Editor surface"
    >
      {SURFACE_TABS.map(tab => {
        const active = activeSurface === tab.id
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={active}
            data-testid={`surface-tab-${tab.id}`}
            onClick={() => onChange(tab.id)}
            className={[
              'flex items-center gap-1.5 h-7 px-3 rounded-lg text-xs font-medium transition-colors',
              'focus:outline-none focus:ring-2 focus:ring-ring/60',
              active
                ? 'bg-primary text-primary-fg shadow-sm'
                : 'text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            <tab.Icon size={13} strokeWidth={2} />
            <span className="hidden sm:inline">{tab.label}</span>
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Placeholder canvases — T9/T10/T11 will replace these
// ---------------------------------------------------------------------------

/**
 * ReportCanvas — placeholder for T9 (report surface editor).
 * Receives spec + surfaceAPI via props when T9 is ready.
 */
function ReportCanvas({ spec }) {
  const pages = spec?.surfaces?.report?.pages ?? []
  return (
    <div
      className="flex flex-col flex-1 min-h-0 items-center justify-center bg-bg"
      data-testid="report-canvas"
    >
      <div className="max-w-sm text-center space-y-3 p-8">
        <div className="w-12 h-12 rounded-2xl bg-surface border border-border flex items-center justify-center mx-auto">
          <FileText size={22} className="text-muted/60" />
        </div>
        <h2 className="text-sm font-semibold text-fg">Report surface</h2>
        <p className="text-xs text-muted/70 leading-relaxed">
          Paginated document view. T9 will implement the full canvas here.
          The spec already carries the report surface schema at{' '}
          <code className="font-mono text-muted">spec.surfaces.report</code>.
        </p>
        {pages.length > 0 && (
          <p className="text-xs text-muted/70">
            {pages.length} page{pages.length !== 1 ? 's' : ''} configured.
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * PresentationCanvas — placeholder for T10 (slides surface editor).
 * Receives spec + surfaceAPI via props when T10 is ready.
 */
function PresentationCanvas({ spec }) {
  const slides = spec?.surfaces?.slides?.slides ?? []
  return (
    <div
      className="flex flex-col flex-1 min-h-0 items-center justify-center bg-bg"
      data-testid="presentation-canvas"
    >
      <div className="max-w-sm text-center space-y-3 p-8">
        <div className="w-12 h-12 rounded-2xl bg-surface border border-border flex items-center justify-center mx-auto">
          <Presentation size={22} className="text-muted/60" />
        </div>
        <h2 className="text-sm font-semibold text-fg">Presentation surface</h2>
        <p className="text-xs text-muted/70 leading-relaxed">
          Slide deck view (16:9). T10 will implement the full canvas here.
          The spec already carries the slides surface schema at{' '}
          <code className="font-mono text-muted">spec.surfaces.slides</code>.
        </p>
        {slides.length > 0 && (
          <p className="text-xs text-muted/70">
            {slides.length} slide{slides.length !== 1 ? 's' : ''} configured.
          </p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// EditorShell — main export
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   boardId?: string|null,
 *   onSaved?: (board: object) => void,
 * }} props
 */
export default function EditorShell({ boardId = null, onSaved }) {
  const [activeSurface, setActiveSurface] = useState('dashboard')

  // ── Spec bridge ──────────────────────────────────────────────────────────
  // DashboardEditor owns spec state + history. The shell reads the live spec
  // via onSpecChange and can write back through setSpecBridgeRef (a stable ref
  // to DashboardEditor's commitSpec). This is the single seam that keeps one
  // spec without duplicating it: report/slides canvases read liveSpec and write
  // via setSpecBridge → DashboardEditor's history stack.

  const [liveSpec, setLiveSpec] = useState(null)
  // A ref to DashboardEditor's spec setter, forwarded via onSetSpec prop.
  const setSpecBridgeRef = useRef(null)

  // Stable setter so useSurfaceState never re-subscribes.
  const setSpecBridge = useCallback((updater) => {
    setSpecBridgeRef.current?.(updater)
  }, [])

  // DashboardEditor calls this whenever spec changes.
  const handleSpecChange = useCallback((spec) => {
    setLiveSpec(spec)
  }, [])

  // DashboardEditor exposes its commitSpec via this callback so the shell
  // (and DocCanvas) can write into history.
  const handleSetSpec = useCallback((fn) => {
    setSpecBridgeRef.current = fn
  }, [])

  // useSurfaceState wires report/slides surface API on top of liveSpec + setSpecBridge.
  // When liveSpec is null (before first render from DashboardEditor) we fall back to
  // an empty object so the hook still produces stable API references.
  const surfaceAPI = useSurfaceState(liveSpec ?? {}, setSpecBridge)

  const [selectedWidgetId, setSelectedWidgetId] = useState(null)

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden" data-testid="editor-shell">

      {/* Surface switch — always visible */}
      <SurfaceSwitch activeSurface={activeSurface} onChange={setActiveSurface} />

      {/* Dashboard surface — full DashboardEditor, unchanged */}
      {activeSurface === 'dashboard' && (
        <DashboardEditor
          boardId={boardId}
          onSaved={onSaved}
          onSpecChange={handleSpecChange}
          onSetSpec={handleSetSpec}
        />
      )}

      {/* Report surface — DocCanvas (T9) */}
      {activeSurface === 'report' && (
        <DocCanvas
          spec={liveSpec ?? {}}
          surfaceAPI={surfaceAPI}
          setSpec={setSpecBridge}
          selectedWidgetId={selectedWidgetId}
          onSelectWidget={setSelectedWidgetId}
        />
      )}

      {/* Presentation surface — SlideCanvas (T10) */}
      {activeSurface === 'presentation' && (
        <SlideCanvas
          spec={liveSpec ?? {}}
          surfaceAPI={surfaceAPI}
          setSpec={setSpecBridge}
        />
      )}
    </div>
  )
}
