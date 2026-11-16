/**
 * EditorPage.jsx — Route component for /editor (create a new dashboard).
 *
 * Editing an EXISTING board no longer lives here. A board is one URL with two
 * modes — `/d/:id` (live) and `/d/:id/edit` — owned by DashboardViewPage, the
 * way Power BI (Reading/Editing views) and Looker Studio (View/Edit) work. The
 * old `/editor/:id` route redirects into `/d/:id/edit` (see LegacyEditorRedirect
 * in App.jsx).
 *
 * This page survives for the one case that genuinely has no board URL yet:
 * creating a dashboard from scratch. The moment the first save mints an id we
 * hand off to the real surface at `/d/:newId/edit`, so an author never has two
 * different places to edit the same board.
 *
 * The route-level toolbar that used to sit here (a FILTERS button dispatching a
 * `nubi:open-filters` CustomEvent + a dark-mode toggle) is gone: it stacked a
 * second toolbar above DashboardEditor's own portaled one, which was half of why
 * the editor felt so busy. The theme toggle now lives in the board header, and
 * Filters is reachable from the editor itself. The `nubi:open-filters` listener
 * inside DashboardEditor is left intact — it's a documented seam and still the
 * way any future chrome should ask the editor to open its filters drawer.
 */

import { useParams, useNavigate, Navigate } from 'react-router-dom'
import EditorShell from '../editor/EditorShell.jsx'
import { useCanWrite } from '../contexts/OrgContext.jsx'

// Event name DashboardEditor listens for to open its filters-drawer authoring
// UI. Kept exported here because this module has always been its home.
export const OPEN_FILTERS_EVENT = 'nubi:open-filters'

export default function EditorPage() {
  const { id } = useParams()
  const navigate = useNavigate()

  // The editor is pure editing — viewers (read-only) cannot reach it.
  // Backend enforces the same rule on save (see app/auth/roles.py).
  const canWrite = useCanWrite()
  if (!canWrite) {
    return <Navigate to="/dashboards" replace />
  }

  // Defensive: /editor/:id is redirected at the route level, but if anything
  // ever renders this page with an id, send it to the real surface rather than
  // quietly opening a second editor for the same board.
  if (id) {
    return <Navigate to={`/d/${id}/edit`} replace />
  }

  return (
    <div className="flex flex-col h-full min-h-0 overflow-x-hidden">
      <EditorShell
        boardId={null}
        onSaved={(board) => {
          // The first save mints the board id — from here on the board has a home.
          if (board?.id) navigate(`/d/${board.id}/edit`, { replace: true })
        }}
      />
    </div>
  )
}
