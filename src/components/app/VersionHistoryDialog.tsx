/**
 * VersionHistoryDialog — version history modal for a versioned resource
 * (flow / board / query).
 *
 * Renders the resource's checkpointed versions as a VersionTimeline (a ruled
 * ledger, newest first, env-pointer Tags on pinned rows). Per-row actions:
 *   - View (when onView is given): fetches the FULL version (incl. config via
 *     getVersion) and hands it to the host so it can show that version
 *     read-only; the dialog closes afterwards.
 *   - Restore: write that version's config back into the draft. Asks first, in
 *     a second dialog that says what will be overwritten; calls onRestored()
 *     afterwards so the host can reload.
 *   - Promote: opens PromoteDialog pre-filled with that row's env (when the
 *     row is pinned to one) as the source.
 *
 * Props:
 *   kind         {'flow'|'board'|'query'}
 *   resourceId   {string}
 *   resourceName {string}   display name in the header
 *   open         {boolean}
 *   onClose      {() => void}
 *   onRestored   {() => void}  called after a successful restore
 *   onView       {(version) => void}?  optional — receives the full version
 *                row { id, version, config, message, created_at, ... }
 *   environments {Array}?    optional env list passed through to PromoteDialog
 */

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'

import { getVersion, listVersions, restoreVersion } from '../../lib/versions.js'
import PromoteDialog from './PromoteDialog.jsx'
import VersionTimeline from './VersionTimeline.jsx'
import Modal from '../ui/Modal.jsx'
import Button from '../ui/Button.jsx'
import Skeleton from '../ui/Skeleton.jsx'
import EmptyState from '../ui/EmptyState.jsx'
import Tooltip from '../ui/Tooltip.jsx'
import { toast } from '../ui/Toast.jsx'
import { History } from 'lucide-react'
import '../../dashboards/widgets/chrome.css'

export default function VersionHistoryDialog({
  kind,
  resourceId,
  resourceName,
  open,
  onClose,
  onRestored,
  onView = undefined,
  environments = undefined,
}) {
  const [loading, setLoading] = useState(false)
  const [versions, setVersions] = useState([])
  const [pointers, setPointers] = useState([])
  const [error, setError] = useState(null)
  const [restoring, setRestoring] = useState(null)   // version number in flight
  const [confirming, setConfirming] = useState(null) // version row awaiting the restore confirm
  const [viewing, setViewing] = useState(null)       // version number being fetched for View
  const [promoteFrom, setPromoteFrom] = useState(null) // env key → PromoteDialog open

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const data = await listVersions(kind, resourceId)
    if (data) {
      setVersions(Array.isArray(data.versions) ? data.versions : [])
      setPointers(Array.isArray(data.pointers) ? data.pointers : [])
    } else {
      setError('Could not load version history.')
    }
    setLoading(false)
  }, [kind, resourceId])

  // Fetch on open.
  useEffect(() => {
    if (!open || !resourceId) return
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [open, resourceId, load])

  async function handleRestore(v) {
    setRestoring(v.version)
    try {
      await restoreVersion(kind, resourceId, v.version)
      setConfirming(null)
      onRestored?.()
      onClose()
    } catch (cause) {
      toast.error(cause?.message || 'Restore failed.')
    } finally {
      setRestoring(null)
    }
  }

  // View — fetch the full version (incl. config) and hand it to the host.
  async function handleView(v) {
    setViewing(v.version)
    const full = await getVersion(kind, resourceId, v.version)
    setViewing(null)
    if (!full) {
      toast.error(`Could not load v${v.version}.`)
      return
    }
    onView?.(full)
    onClose()
  }

  return (
    <>
      <Modal
        open={open && promoteFrom === null && confirming === null}
        onClose={onClose}
        size="lg"
        title="Version history"
        description={<>{kind} · <span className="font-medium text-fg">{resourceName}</span></>}
      >
        <Modal.Body className="!p-0" data-testid="version-history-dialog">
          {loading && versions.length === 0 && (
            <div className="px-6 py-4" role="status" aria-busy="true" aria-label="Loading versions">
              <Skeleton.Rows rows={4} />
            </div>
          )}

          {!loading && error && (
            <EmptyState
              kind="error"
              compact
              title="Version history couldn't load"
              description={error}
              action={<Button size="sm" variant="secondary" onClick={load}>Retry</Button>}
            />
          )}

          {!loading && !error && versions.length === 0 && (
            <EmptyState
              compact
              icon={<History size={20} strokeWidth={1.5} />}
              title="No versions yet"
              description="A checkpoint snapshots the current draft so you can come back to it."
            />
          )}

          {versions.length > 0 && (
            <div className="px-6 pb-2 pt-4">
              <VersionTimeline
                versions={versions}
                pointers={pointers}
                restoring={restoring}
                viewing={viewing}
                onView={onView ? handleView : undefined}
                onRestore={(v) => setConfirming(v)}
                onPromote={(envKey) => setPromoteFrom(envKey)}
              />
            </div>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Tooltip content="Refresh the list" side="top">
            <Button variant="ghost" size="sm" onClick={load} loading={loading} aria-label="Refresh versions" className="mr-auto">
              {!loading && <RefreshCw size={14} strokeWidth={1.5} aria-hidden="true" />}
              Refresh
            </Button>
          </Tooltip>
          <Button variant="secondary" size="sm" onClick={onClose}>Close</Button>
        </Modal.Footer>
      </Modal>

      {/* Restore overwrites the draft, so say so before doing it. */}
      <Modal
        open={open && confirming !== null}
        onClose={() => { if (restoring === null) setConfirming(null) }}
        size="sm"
        title={confirming ? `Restore v${confirming.version}?` : 'Restore'}
        description="The draft is replaced by this version. Unsaved changes in the draft are lost."
      >
        <Modal.Footer>
          <Button variant="ghost" size="sm" onClick={() => setConfirming(null)} disabled={restoring !== null}>Cancel</Button>
          <Button
            size="sm"
            onClick={() => confirming && handleRestore(confirming)}
            loading={restoring !== null}
            data-testid="version-restore-confirm"
          >
            Restore into draft
          </Button>
        </Modal.Footer>
      </Modal>

      {/* Nested promote dialog, pre-filled from the clicked row */}
      <PromoteDialog
        kind={kind}
        resourceId={resourceId}
        open={open && promoteFrom !== null}
        onClose={() => { setPromoteFrom(null); load() }}
        environments={environments}
        defaultFrom={promoteFrom ?? 'dev'}
        defaultTo={promoteFrom === 'prod' ? 'dev' : 'prod'}
      />
    </>
  )
}
