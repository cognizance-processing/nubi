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
import Modal from '../ui/Modal.jsx'
import Button from '../ui/Button.jsx'

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
 *   loading?: boolean,
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
    <Modal
      open
      onClose={loading ? undefined : onCancel}
      title={`Delete ${resourceType}`}
      description="This action is permanent and cannot be undone."
      size="md"
    >
      <Modal.Body className="space-y-4">
        {/* ---- BLOCKERS: deletion is not possible ---- */}
        {hasBlockers && (
          <div className="nubi-notice nubi-notice-warning" style={{ display: 'block' }} role="alert">
            <p className="m-0 mb-2 text-sm font-semibold">This {resourceType} cannot be deleted yet.</p>
            <ul className="m-0 list-none space-y-1 p-0">
              {impact.blockers.map((b, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="nubi-dot nubi-dot-warning mt-1.5 shrink-0" aria-hidden="true" />
                  {b.reason ?? `Contains ${b.count} ${impactLabel(b.type, b.count)} that must be removed first.`}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* ---- IMPACT LIST: what will be deleted ---- */}
        {!hasBlockers && impactItems.length > 0 && (
          <div className="nubi-notice nubi-notice-error" style={{ display: 'block' }}>
            <p className="m-0 mb-2 text-sm font-semibold">Deleting this {resourceType} will permanently remove:</p>
            <ul className="m-0 list-none space-y-1 p-0">
              {impactItems.map((item, i) => (
                <li key={i} className="flex items-center gap-2 text-sm">
                  <span className="nubi-dot nubi-dot-danger" aria-hidden="true" />
                  <span className="ap-mono font-semibold">{item.count.toLocaleString()}</span>&nbsp;{impactLabel(item.type, item.count)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* ---- No associated data ---- */}
        {!hasBlockers && impactItems.length === 0 && (
          <p className="m-0 text-sm text-muted">
            No associated resources will be deleted alongside this {resourceType}.
          </p>
        )}

        {/* ---- Type-the-name confirmation ---- */}
        {!hasBlockers && (
          <div className="space-y-2">
            <label htmlFor="danger-confirm-input" className="block text-sm text-muted">
              To confirm, type{' '}
              <span className="bg-surface-2 px-1.5 py-0.5 font-mono font-semibold text-fg" style={{ borderRadius: 2 }}>
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
              className="nubi-input font-mono"
            />
          </div>
        )}
      </Modal.Body>

      <Modal.Footer>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={loading}>Cancel</Button>
        <Button
          type="button"
          variant="danger"
          onClick={onConfirm}
          disabled={!deleteEnabled}
          aria-disabled={!deleteEnabled}
          loading={loading}
        >
          {loading ? 'Deleting' : `Delete ${resourceType}`}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}
