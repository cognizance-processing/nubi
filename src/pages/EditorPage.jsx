/**
 * EditorPage.jsx — Route component for /editor (new) and /editor/:id (edit).
 *
 * Wraps DashboardEditor and handles:
 *   - Reading :id from the URL param (undefined → new board)
 *   - Redirecting to /editor/:newId after a successful create so the URL stays
 *     canonical and a refresh re-opens the same board.
 *   - Showing a "View board" link after a save.
 */

import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import DashboardEditor from '../editor/DashboardEditor.jsx'

export default function EditorPage() {
  const { id } = useParams()  // undefined on /editor (new board)
  const navigate = useNavigate()
  const [savedBoard, setSavedBoard] = useState(null)

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

      <DashboardEditor boardId={id ?? null} onSaved={handleSaved} />
    </div>
  )
}
