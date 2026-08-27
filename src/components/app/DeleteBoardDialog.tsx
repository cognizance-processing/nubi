/**
 * DeleteBoardDialog — the confirm step in front of deleting ONE dashboard.
 *
 * Deliberately lighter than DangerDeleteDialog (type the exact name) and
 * DangerConfirmDialog (type a random code): a single board is a cheap thing to
 * rebuild and an author deletes them routinely, so the gate is a click, not a
 * transcription exercise. The heavier dialogs stay for org/project deletes and
 * for bulk board deletes, where the blast radius actually warrants the friction.
 *
 * Shared by the dashboards list (per-card menu) and the board surface at
 * /d/:id — one dialog so the two entry points can't drift into wording each
 * other's confirmation differently.
 *
 * Props:
 *   board     {{ name?: string }}  the board being deleted (name is shown)
 *   busy      {boolean}            true while the DELETE is in flight
 *   onConfirm {() => void}
 *   onCancel  {() => void}
 *
 * Keyboard: Escape cancels, unless a delete is already in flight.
 * Render it conditionally — it takes focus on mount.
 */

import { useEffect, useRef } from 'react'
import { Trash2, Loader2, X } from 'lucide-react'

export default function DeleteBoardDialog({ board, onConfirm, onCancel, busy = false }) {
  const dialogRef = useRef(null)

  // Escape to close (ignored while a delete is in flight)
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [busy, onCancel])

  // Focus the dialog panel on open
  useEffect(() => {
    const t = setTimeout(() => dialogRef.current?.focus(), 50)
    return () => clearTimeout(t)
  }, [])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-dlg-title"
      data-testid="delete-board-dialog"
    >
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={busy ? undefined : onCancel} />
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="relative z-10 w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-nubi-xl nubi-animate-scale-in outline-none"
      >
        <button
          onClick={onCancel}
          disabled={busy}
          className="absolute top-4 right-4 text-muted hover:text-fg transition-colors p-1 rounded-lg hover:bg-surface-2 disabled:opacity-50"
          aria-label="Cancel"
        >
          <X size={16} />
        </button>
        <div className="flex items-center justify-center w-11 h-11 rounded-xl bg-danger-bg mb-4">
          <Trash2 size={20} className="text-danger" />
        </div>
        <h2 id="delete-dlg-title" className="font-display font-semibold text-lg text-fg mb-1">
          Delete dashboard?
        </h2>
        <p className="text-muted text-sm mb-6 leading-relaxed">
          <span className="font-medium text-fg">&ldquo;{board?.name}&rdquo;</span> will be
          permanently deleted. This cannot be undone.
        </p>
        <div className="flex gap-2.5">
          <button
            onClick={onCancel}
            disabled={busy}
            className="flex-1 h-9 rounded-xl border border-border bg-surface-2 text-sm font-medium text-fg hover:bg-surface-2/80 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            data-testid="delete-board-confirm"
            className="flex-1 h-9 rounded-xl bg-danger text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {busy && <Loader2 size={13} className="animate-spin" />}
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}
