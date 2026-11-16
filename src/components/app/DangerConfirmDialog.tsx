/**
 * DangerConfirmDialog — destructive bulk-action confirmation modal gated by a
 * RANDOM confirmation code (vs DangerDeleteDialog which asks for the resource
 * name — use that one for single named resources).
 *
 * A fresh 6-character code (unambiguous letters + digits) is generated each
 * time the dialog mounts (i.e. per open) via crypto.getRandomValues. The red
 * confirm button stays disabled until the user types the exact code.
 *
 * Props:
 *   title        {string}        e.g. 'Delete 3 dashboards'
 *   description  {string}        body copy explaining the blast radius
 *   items        {string[]}      display names of what will be deleted
 *                                (first few are listed, the rest summarised)
 *   count        {number}        total number of items (defaults to items.length)
 *   itemNoun     {string}        e.g. 'dashboard' — used in the summary line
 *   itemNounPlural {string}      irregular plural (default: itemNoun + 's')
 *   confirmLabel {string}        label for the red button (default 'Delete')
 *   loading      {boolean}       true while the deletes are in flight
 *   error        {string|null}   error text shown above the footer
 *   onCancel     {() => void}    close without confirming
 *   onConfirm    {() => void}    called when the user confirms (exact code typed)
 *
 * Keyboard: Escape cancels (unless loading), Enter submits when enabled.
 * Render it conditionally — mounting generates the code:
 *   {dialog && <DangerConfirmDialog ... />}
 */

import { useEffect, useRef, useState } from 'react'
import Modal from '../ui/Modal.jsx'
import Button from '../ui/Button.jsx'

// Unambiguous alphabet (no 0/O, 1/I/L) so the code is easy to re-type.
const CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
const CODE_LENGTH = 6

/** Generate a random confirmation code via crypto.getRandomValues. */
function makeConfirmCode() {
  const buf = new Uint32Array(CODE_LENGTH)
  crypto.getRandomValues(buf)
  let out = ''
  for (let i = 0; i < CODE_LENGTH; i++) {
    out += CODE_ALPHABET[buf[i] % CODE_ALPHABET.length]
  }
  return out
}

const MAX_LISTED = 5

export default function DangerConfirmDialog({
  title,
  description,
  items = [],
  count,
  itemNoun = 'item',
  itemNounPlural = undefined,
  confirmLabel = 'Delete',
  loading = false,
  error = null,
  onCancel,
  onConfirm,
}) {
  // One code per mount — the parent mounts this dialog on open.
  const [code] = useState(makeConfirmCode)
  const [typed, setTyped] = useState('')
  const inputRef = useRef(null)

  const total = typeof count === 'number' ? count : items.length
  const listed = items.slice(0, MAX_LISTED)
  const remaining = total - listed.length
  const enabled = typed === code && !loading

  // Focus the code input when the dialog mounts.
  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 80)
    return () => clearTimeout(t)
  }, [])

  return (
    <Modal
      open
      onClose={loading ? undefined : onCancel}
      title={title}
      description="This action is permanent and cannot be undone."
      size="md"
    >
      <div data-testid="danger-confirm-dialog">
        <Modal.Body className="space-y-4">
          {description && <p className="m-0 text-sm leading-relaxed text-muted">{description}</p>}

          {/* What will be deleted */}
          <div className="nubi-notice nubi-notice-error" style={{ display: 'block' }}>
            <p className="m-0 mb-2 text-sm font-semibold">
              This will permanently delete {total.toLocaleString()} {total === 1 ? itemNoun : (itemNounPlural ?? `${itemNoun}s`)}:
            </p>
            <ul className="m-0 list-none space-y-1 p-0">
              {listed.map((name, i) => (
                <li key={i} className="flex min-w-0 items-center gap-2 text-sm">
                  <span className="nubi-dot nubi-dot-danger" aria-hidden="true" />
                  <span className="truncate">{name}</span>
                </li>
              ))}
              {remaining > 0 && (
                <li className="flex items-center gap-2 text-sm">
                  <span className="nubi-dot nubi-dot-danger" aria-hidden="true" />
                  and {remaining.toLocaleString()} more
                </li>
              )}
            </ul>
          </div>

          {/* Type-the-code confirmation */}
          <div className="space-y-2">
            <label htmlFor="danger-confirm-code-input" className="block text-sm text-muted">
              To confirm, type{' '}
              <span
                data-testid="danger-confirm-code"
                className="select-all bg-surface-2 px-1.5 py-0.5 font-mono font-semibold tracking-widest text-fg"
                style={{ borderRadius: 2 }}
              >
                {code}
              </span>{' '}
              below:
            </label>
            <input
              id="danger-confirm-code-input"
              data-testid="danger-confirm-input"
              ref={inputRef}
              type="text"
              value={typed}
              onChange={e => setTyped(e.target.value.toUpperCase())}
              onKeyDown={e => {
                if (e.key === 'Enter' && enabled) onConfirm()
              }}
              disabled={loading}
              placeholder={'·'.repeat(CODE_LENGTH)}
              autoComplete="off"
              spellCheck={false}
              className="nubi-input font-mono tracking-widest"
            />
          </div>

          {/* Error from a failed delete */}
          {error && <p className="nubi-field-error m-0 text-sm" role="alert">{error}</p>}
        </Modal.Body>

        <Modal.Footer>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={loading}>Cancel</Button>
          <Button
            type="button"
            variant="danger"
            data-testid="danger-confirm-button"
            onClick={onConfirm}
            disabled={!enabled}
            aria-disabled={!enabled}
            loading={loading}
          >
            {loading ? 'Deleting' : confirmLabel}
          </Button>
        </Modal.Footer>
      </div>
    </Modal>
  )
}
