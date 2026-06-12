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
import { AlertTriangle, Trash2, Loader2, X } from 'lucide-react'

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
  itemNounPlural,
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

  // Escape cancels.
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape' && !loading) onCancel()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [loading, onCancel])

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        onClick={loading ? undefined : onCancel}
        aria-hidden="true"
      />

      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="danger-confirm-title"
        className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none"
      >
        <div
          data-testid="danger-confirm-dialog"
          className="pointer-events-auto w-full max-w-md bg-surface rounded-2xl border border-red-200 dark:border-red-900/50 shadow-2xl flex flex-col"
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start gap-3 px-6 pt-6 pb-4 border-b border-border">
            <div className="shrink-0 w-10 h-10 rounded-xl flex items-center justify-center bg-red-100 dark:bg-red-900/30">
              <AlertTriangle size={20} className="text-red-600 dark:text-red-400" />
            </div>
            <div className="flex-1 min-w-0">
              <h2
                id="danger-confirm-title"
                className="font-display font-semibold text-base text-fg"
              >
                {title}
              </h2>
              <p className="text-sm text-muted mt-0.5">
                This action is <strong className="text-fg">permanent</strong> and cannot be undone.
              </p>
            </div>
            <button
              type="button"
              onClick={onCancel}
              disabled={loading}
              aria-label="Close"
              className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
            >
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <div className="px-6 py-5 space-y-4">
            {description && (
              <p className="text-sm text-muted leading-relaxed">{description}</p>
            )}

            {/* What will be deleted */}
            <div className="rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/10 px-4 py-3 space-y-2">
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                This will permanently delete{' '}
                <strong>{total}</strong>{' '}
                {total === 1 ? itemNoun : (itemNounPlural ?? `${itemNoun}s`)}:
              </p>
              <ul className="space-y-1">
                {listed.map((name, i) => (
                  <li
                    key={i}
                    className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400 min-w-0"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
                    <span className="truncate">{name}</span>
                  </li>
                ))}
                {remaining > 0 && (
                  <li className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
                    …and {remaining} more
                  </li>
                )}
              </ul>
            </div>

            {/* Type-the-code confirmation */}
            <div className="space-y-2">
              <label
                htmlFor="danger-confirm-code-input"
                className="block text-sm text-muted"
              >
                To confirm, type{' '}
                <span
                  data-testid="danger-confirm-code"
                  className="font-mono font-semibold tracking-widest text-fg bg-surface-2 px-1.5 py-0.5 rounded-md select-all"
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
                className="w-full px-3 py-2 rounded-xl text-sm font-mono tracking-widest bg-bg border border-border text-fg placeholder:text-muted focus:outline-none focus:border-red-400 dark:focus:border-red-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              />
            </div>

            {/* Error from a failed delete */}
            {error && (
              <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                {error}
              </p>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-3 px-6 pb-6">
            <button
              type="button"
              onClick={onCancel}
              disabled={loading}
              className="px-4 py-2 rounded-xl text-sm font-medium border border-border text-fg hover:bg-surface-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="danger-confirm-button"
              onClick={onConfirm}
              disabled={!enabled}
              aria-disabled={!enabled}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-red-600 text-white hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
            >
              {loading
                ? <Loader2 size={15} className="animate-spin" />
                : <Trash2 size={15} />}
              {loading ? 'Deleting…' : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
