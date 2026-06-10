/**
 * EditorPage.jsx — Route component for /editor (new) and /editor/:id (edit).
 *
 * Wraps DashboardEditor and handles:
 *   - Reading :id from the URL param (undefined → new board)
 *   - Redirecting to /editor/:newId after a successful create so the URL stays
 *     canonical and a refresh re-opens the same board.
 *   - Showing a "View board" link after a save.
 *   - A route-level edit-mode toolbar cluster (FILTERS authoring + dark-mode
 *     toggle) that lives ABOVE the editor — see "Edit-mode toolbar" below.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * Edit-mode toolbar + Track-T (T5) integration seam
 * ────────────────────────────────────────────────────────────────────────────
 * The detailed tab EDITOR work in DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md
 * §T5 (tab strip CRUD, canvas active-tab filtering, "Move to tab →" widget menu,
 * tab-bar / per-tab inspector panels) operates on the live DashboardSpec, the
 * GridCanvas, the widget palette and the inspector — ALL of which are owned by
 * `src/editor/DashboardEditor.jsx`, not this page. DashboardEditor keeps that
 * state private (it takes only `{ boardId, onSaved }`) and portals its own
 * toolbar into the shared top bar via UiContext's `topbarSlot`. None of it is
 * reachable from this route wrapper.
 *
 * So this file implements the part that genuinely belongs at the route level —
 * the edit-mode toolbar cluster the user asked for — and exposes a clean,
 * non-colliding seam for the editor:
 *
 *   - The dark-mode toggle is fully functional here: it uses the app's
 *     `useTheme()` hook (src/contexts/ThemeContext.jsx → { theme, toggleTheme }),
 *     which flips the `.dark` class on <html> and persists to localStorage.
 *   - The FILTERS button dispatches a documented `nubi:open-filters` CustomEvent
 *     on `window`. DashboardEditor (owned by another agent) listens for this and
 *     opens its filters-drawer authoring UI. This page does not — and cannot —
 *     reach into the editor's drawer state, so an event seam keeps the two files
 *     independent while still wiring the button to a real action.
 */

import { useState } from 'react'
import { useParams, Link, useNavigate, Navigate } from 'react-router-dom'
import { SlidersHorizontal, Sun, Moon } from 'lucide-react'
import DashboardEditor from '../editor/DashboardEditor.jsx'
import { useCanWrite } from '../contexts/OrgContext.jsx'
import { useTheme } from '../contexts/ThemeContext.jsx'

// Event name the editor (DashboardEditor.jsx, owned separately) listens for to
// open its filters-drawer authoring UI. Kept as a constant so both sides can
// reference the exact same string.
export const OPEN_FILTERS_EVENT = 'nubi:open-filters'

/**
 * EditModeToolbar — a slim route-level toolbar cluster shown above the editor.
 *
 * Holds the two controls the user asked to sit beside each other:
 *   - FILTERS: opens the filters drawer for authoring (via OPEN_FILTERS_EVENT).
 *   - Dark-mode toggle: reuses the app theme hook.
 */
function EditModeToolbar() {
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'

  return (
    <div
      role="toolbar"
      aria-label="Editor tools"
      className="shrink-0 flex items-center gap-2 bg-surface border-b border-border px-4 py-2"
    >
      <button
        type="button"
        onClick={() => window.dispatchEvent(new CustomEvent(OPEN_FILTERS_EVENT))}
        className="inline-flex items-center gap-1.5 h-8 px-3 text-sm font-medium rounded-lg border border-border bg-surface-2 text-fg hover:border-primary hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
      >
        <SlidersHorizontal size={15} className="shrink-0" />
        Filters
      </button>

      <button
        type="button"
        onClick={toggleTheme}
        aria-pressed={isDark}
        title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
        className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border bg-surface-2 text-muted hover:text-fg hover:border-border/80 transition-colors focus:outline-none focus:ring-2 focus:ring-ring/60"
      >
        {isDark ? <Sun size={15} /> : <Moon size={15} />}
        <span className="sr-only">{isDark ? 'Switch to light mode' : 'Switch to dark mode'}</span>
      </button>
    </div>
  )
}

export default function EditorPage() {
  const { id } = useParams()  // undefined on /editor (new board)
  const navigate = useNavigate()
  const [savedBoard, setSavedBoard] = useState(null)

  // The editor is pure editing — viewers (read-only) cannot reach it.
  // Backend enforces the same rule on save (see app/auth/roles.py).
  const canWrite = useCanWrite()
  if (!canWrite) {
    return <Navigate to="/dashboards" replace />
  }

  function handleSaved(board) {
    setSavedBoard(board)
    // If this was a create (no id in URL), update the URL to /editor/:newId
    if (!id && board?.id) {
      navigate(`/editor/${board.id}`, { replace: true })
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 overflow-x-hidden">
      {/* Success banner */}
      {savedBoard && (
        <div className="shrink-0 bg-surface border-b border-border px-4 py-2 flex items-center gap-3 text-sm text-fg" style={{ background: 'color-mix(in srgb, #22c55e 8%, transparent)', borderColor: 'color-mix(in srgb, #22c55e 25%, transparent)', color: '#15803d' }}>
          <span className="font-medium">Dashboard saved.</span>
          {savedBoard.id && (
            <Link
              to={`/d/${savedBoard.id}`}
              className="font-semibold underline hover:opacity-80 transition-opacity"
            >
              View live →
            </Link>
          )}
          <button
            className="ml-auto opacity-70 hover:opacity-100 transition-opacity text-lg leading-none"
            onClick={() => setSavedBoard(null)}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}

      <EditModeToolbar />

      <DashboardEditor boardId={id ?? null} onSaved={handleSaved} />
    </div>
  )
}
