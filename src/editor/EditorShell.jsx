/**
 * EditorShell.jsx — thin wrapper around DashboardEditor.
 *
 * Nubi ships one dashboard-authoring surface: the grid. The report (paginated
 * document) and presentation (slide deck) surfaces — and the surface switch
 * tab bar that toggled between them — have been removed; they were off-wedge
 * authoring products alongside the wedge (the dashboard grid). Scheduled
 * email PDF delivery of a dashboard (report_send) is unaffected — it renders
 * the dashboard surface, not a separate document.
 *
 * This wrapper exists so page wiring stays a one-line swap
 * (<EditorShell boardId onSaved /> in place of <DashboardEditor ... />) and so
 * future shell-level chrome has a seam to attach to without touching
 * DashboardEditor itself.
 */

import DashboardEditor from './DashboardEditor.jsx'

/**
 * @param {{
 *   boardId?: string|null,
 *   onSaved?: (board: object) => void,
 * }} props
 */
export default function EditorShell({ boardId = null, onSaved }) {
  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden" data-testid="editor-shell">
      <DashboardEditor boardId={boardId} onSaved={onSaved} />
    </div>
  )
}
