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
    <div className="flex flex-col min-h-screen">
      {/* Success banner */}
      {savedBoard && (
        <div className="bg-green-50 border-b border-green-200 px-4 py-2 flex items-center gap-3 text-sm text-green-800">
          <span>Dashboard saved.</span>
          {savedBoard.id && (
            <Link
              to={`/d/${savedBoard.id}`}
              className="text-green-700 font-medium underline hover:text-green-900"
            >
              View live &rarr;
            </Link>
          )}
          <button
            className="ml-auto text-green-600 hover:text-green-900"
            onClick={() => setSavedBoard(null)}
          >
            &times;
          </button>
        </div>
      )}

      <DashboardEditor boardId={id ?? null} onSaved={handleSaved} />
    </div>
  )
}
