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
 * Keyboard: Escape cancels, unless a delete is already in flight. Focus is
 * trapped while open and returns to the trigger on close (Modal).
 * Render it conditionally.
 */

import Modal from '../ui/Modal.jsx'
import Button from '../ui/Button.jsx'

export default function DeleteBoardDialog({ board, onConfirm, onCancel, busy = false }) {
  return (
    <Modal
      open
      onClose={() => { if (!busy) onCancel() }}
      size="sm"
      title="Delete dashboard?"
      persistent={busy}
    >
      <Modal.Body data-testid="delete-board-dialog">
        <p className="m-0 text-[14px] leading-relaxed text-muted">
          <span className="font-semibold text-fg">&ldquo;{board?.name}&rdquo;</span> will be
          permanently deleted. This cannot be undone.
        </p>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="danger"
          size="sm"
          onClick={onConfirm}
          loading={busy}
          data-testid="delete-board-confirm"
        >
          {busy ? 'Deleting…' : 'Delete'}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}
