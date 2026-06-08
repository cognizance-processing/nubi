/**
 * DangerDeleteDialog — reusable destructive-delete confirmation modal.
 *
 * Props:
 *   resourceType  {string}        e.g. 'organization' | 'project'
 *   name          {string}        exact name the user must re-type to confirm
 *   impact        {object|null}   deletion-impact payload from the backend:
 *                                   { can_delete: bool,
 *                                     blockers: [{ type, count, reason }],
 *                                     deletes:  [{ type, count }] }
 *   loading       {boolean}       true while the DELETE is in flight
 *   onCancel      {() => void}    called when the user closes without confirming
 *   onConfirm     {() => void}    called when the user confirms (exact name typed)
 *
 * Behaviour:
 *   - If impact.blockers is non-empty, the delete button is DISABLED entirely
 *     and the blockers are shown as the reason why.
 *   - Otherwise: the impact.deletes list is rendered, plus a type-the-name
 *     input. The delete button becomes enabled only when the input exactly
 *     equals `name`.
 *   - ESC key closes the dialog via onCancel.
 *   - Clicking the backdrop closes via onCancel.
 *
 * Usage:
 *   <DangerDeleteDialog
 *     resourceType="organization"
 *     name={org.name}
 *     impact={impactData}
 *     loading={deleting}
 *     onCancel={() => setDialogOpen(false)}
 *     onConfirm={handleDelete}
 *   />
 */

import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Trash2, Loader2, X } from 'lucide-react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Human-readable label for a deletion impact item type.
 * @param {string} type
 * @param {number} count
 * @returns {string}
 */
function impactLabel(type, count) {
  const labels = {
    dashboards:   count === 1 ? 'dashboard'      : 'dashboards',
    queries:      count === 1 ? 'query'           : 'queries',
    flows:        count === 1 ? 'flow'            : 'flows',
    connectors:   count === 1 ? 'connector'       : 'connectors',
    secrets:      count === 1 ? 'secret'          : 'secrets',
    projects:     count === 1 ? 'project'         : 'projects',
    automations:  count === 1 ? 'automation'      : 'automations',
    members:      count === 1 ? 'member'          : 'members',
    invitations:  count === 1 ? 'invitation'      : 'invitations',
  }
  return labels[type] ?? type
}

// ---------------------------------------------------------------------------
// DangerDeleteDialog (default export)
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   resourceType: string,
 *   name: string,
 *   impact: {
 *     can_delete: boolean,
 *     blockers: Array<{type:string, count:number, reason:string}>,
 *     deletes:  Array<{type:string, count:number}>,
 *   } | null,
 *   loading: boolean,
 *   onCancel: () => void,
 *   onConfirm: () => void,
 * }} props
 */
export default function DangerDeleteDialog({
  resourceType,
  name,
  impact,
  loading = false,
  onCancel,
  onConfirm,
}) {
  const [typed, setTyped] = useState('')
  const inputRef = useRef(null)

  // Focus the name input when the dialog mounts
  useEffect(() => {
    // Small delay so the transition doesn't fight focus
    const t = setTimeout(() => inputRef.current?.focus(), 80)
    return () => clearTimeout(t)
  }, [])

  // ESC to close
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape' && !loading) onCancel()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [loading, onCancel])

  // Reset typed value if the dialog is remounted for a different resource
  useEffect(() => {
    setTyped('')
  }, [name])

  // --------------------------------------------------------------------------

  const hasBlockers = Array.isArray(impact?.blockers) && impact.blockers.length > 0
  const canDelete = impact?.can_delete !== false && !hasBlockers
  const deleteEnabled = canDelete && typed === name && !loading

  const impactItems = Array.isArray(impact?.deletes)
    ? impact.deletes.filter(d => d.count > 0)
    : []

  // --------------------------------------------------------------------------

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
        aria-labelledby="danger-dialog-title"
        className="
          fixed inset-0 z-50 flex items-center justify-center p-4
          pointer-events-none
        "
      >
        <div
          className="
            pointer-events-auto
            w-full max-w-md
            bg-surface rounded-2xl border border-red-200 dark:border-red-900/50
            shadow-2xl
            flex flex-col
          "
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start gap-3 px-6 pt-6 pb-4 border-b border-border">
            <div className="
              shrink-0 w-10 h-10 rounded-xl
              flex items-center justify-center
              bg-red-100 dark:bg-red-900/30
            ">
              <AlertTriangle size={20} className="text-red-600 dark:text-red-400" />
            </div>
            <div className="flex-1 min-w-0">
              <h2
                id="danger-dialog-title"
                className="font-display font-semibold text-base text-fg"
              >
                Delete {resourceType}
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
              className="
                shrink-0 p-1.5 rounded-lg text-muted
                hover:text-fg hover:bg-surface-2
                transition-colors disabled:opacity-50
              "
            >
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <div className="px-6 py-5 space-y-4">
            {/* ---- BLOCKERS — deletion is not possible ---- */}
            {hasBlockers && (
              <div className="
                rounded-xl border border-amber-300 dark:border-amber-700
                bg-amber-50 dark:bg-amber-900/20
                px-4 py-3 space-y-2
              ">
                <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
                  This {resourceType} cannot be deleted yet.
                </p>
                <ul className="space-y-1">
                  {impact.blockers.map((b, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400"
                    >
                      <span className="mt-1 w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
                      {b.reason ?? `Contains ${b.count} ${impactLabel(b.type, b.count)} that must be removed first.`}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* ---- IMPACT LIST — what will be deleted ---- */}
            {!hasBlockers && impactItems.length > 0 && (
              <div className="
                rounded-xl border border-red-200 dark:border-red-900/50
                bg-red-50 dark:bg-red-900/10
                px-4 py-3 space-y-2
              ">
                <p className="text-sm font-medium text-red-700 dark:text-red-400">
                  Deleting this {resourceType} will permanently remove:
                </p>
                <ul className="space-y-1">
                  {impactItems.map((item, i) => (
                    <li
                      key={i}
                      className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400"
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
                      <strong>{item.count}</strong>&nbsp;{impactLabel(item.type, item.count)}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* ---- No associated data ---- */}
            {!hasBlockers && impactItems.length === 0 && (
              <p className="text-sm text-muted">
                No associated resources will be deleted alongside this {resourceType}.
              </p>
            )}

            {/* ---- Type-the-name confirmation ---- */}
            {!hasBlockers && (
              <div className="space-y-2">
                <label
                  htmlFor="danger-confirm-input"
                  className="block text-sm text-muted"
                >
                  To confirm, type{' '}
                  <span className="font-mono font-semibold text-fg bg-surface-2 px-1.5 py-0.5 rounded-md">
                    {name}
                  </span>{' '}
                  below:
                </label>
                <input
                  id="danger-confirm-input"
                  ref={inputRef}
                  type="text"
                  value={typed}
                  onChange={e => setTyped(e.target.value)}
                  disabled={loading}
                  placeholder={name}
                  autoComplete="off"
                  spellCheck={false}
                  className="
                    w-full px-3 py-2 rounded-xl text-sm font-mono
                    bg-bg border border-border text-fg placeholder:text-muted
                    focus:outline-none focus:border-red-400 dark:focus:border-red-600
                    disabled:opacity-50 disabled:cursor-not-allowed
                    transition-colors
                  "
                />
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-3 px-6 pb-6">
            <button
              type="button"
              onClick={onCancel}
              disabled={loading}
              className="
                px-4 py-2 rounded-xl text-sm font-medium
                border border-border text-fg
                hover:bg-surface-2 transition-colors
                disabled:opacity-50 disabled:cursor-not-allowed
              "
            >
              Cancel
            </button>

            <button
              type="button"
              onClick={onConfirm}
              disabled={!deleteEnabled}
              aria-disabled={!deleteEnabled}
              className="
                inline-flex items-center gap-2
                px-4 py-2 rounded-xl text-sm font-semibold
                bg-red-600 text-white
                hover:bg-red-700
                disabled:opacity-40 disabled:cursor-not-allowed
                transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2
              "
            >
              {loading
                ? <Loader2 size={15} className="animate-spin" />
                : <Trash2 size={15} />
              }
              {loading ? 'Deleting…' : `Delete ${resourceType}`}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
