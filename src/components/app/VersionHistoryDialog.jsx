/**
 * VersionHistoryDialog — version history modal for a versioned resource
 * (flow / board / query).
 *
 * Renders the resource's checkpointed versions as a VersionTimeline (vertical
 * rail, newest first, env-pointer chips on pinned rows). Per-row actions:
 *   - View (when onView is given): fetches the FULL version (incl. config via
 *     getVersion) and hands it to the host so it can show that version
 *     read-only; the dialog closes afterwards.
 *   - Restore: write that version's config back into the draft (confirmed via
 *     window.confirm; calls onRestored() afterwards so the host can reload).
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
import { History, Loader2, RefreshCw, X } from 'lucide-react'

import { getVersion, listVersions, restoreVersion } from '../../lib/versions.js'
import PromoteDialog from './PromoteDialog.jsx'
import VersionTimeline from './VersionTimeline.jsx'

export default function VersionHistoryDialog({
  kind,
  resourceId,
  resourceName,
  open,
  onClose,
  onRestored,
  onView,
  environments,
}) {
  const [loading, setLoading] = useState(false)
  const [versions, setVersions] = useState([])
  const [pointers, setPointers] = useState([])
  const [error, setError] = useState(null)
  const [restoring, setRestoring] = useState(null)   // version number in flight
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

  // ESC to close (only when the nested PromoteDialog isn't open — it handles
  // its own ESC).
  useEffect(() => {
    if (!open || promoteFrom !== null) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, promoteFrom, onClose])

  if (!open) return null

  async function handleRestore(v) {
    if (!window.confirm(`Restore version v${v.version} into the current draft? Unsaved draft changes are overwritten.`)) return
    setRestoring(v.version)
    try {
      await restoreVersion(kind, resourceId, v.version)
      onRestored?.()
      onClose()
    } catch (cause) {
      window.alert(cause?.message || 'Restore failed.')
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
      window.alert(`Could not load v${v.version}.`)
      return
    }
    onView?.(full)
    onClose()
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="version-history-title"
        className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none"
      >
        <div
          className="pointer-events-auto w-full max-w-lg max-h-[80dvh] bg-surface rounded-2xl border border-border shadow-2xl flex flex-col"
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="shrink-0 flex items-start gap-3 px-6 pt-6 pb-4 border-b border-border">
            <div className="shrink-0 w-10 h-10 rounded-xl flex items-center justify-center bg-primary/10">
              <History size={18} className="text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 id="version-history-title" className="font-display font-semibold text-base text-fg truncate">
                Version history
              </h2>
              <p className="text-sm text-muted mt-0.5 truncate">
                {kind} · <span className="font-medium text-fg">{resourceName}</span>
              </p>
            </div>
            <button
              type="button"
              onClick={load}
              disabled={loading}
              aria-label="Refresh versions"
              title="Refresh"
              className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
            >
              <X size={16} />
            </button>
          </div>

          {/* Body — timeline */}
          <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
            {loading && versions.length === 0 && (
              <div className="flex items-center gap-2 text-xs text-muted py-8 justify-center">
                <Loader2 size={13} className="animate-spin" />
                Loading versions…
              </div>
            )}

            {!loading && error && (
              <p className="text-xs text-red-500 text-center py-8">{error}</p>
            )}

            {!loading && !error && versions.length === 0 && (
              <div className="text-xs text-muted text-center py-10 rounded-xl border border-dashed border-border">
                <History size={20} className="mx-auto mb-2 opacity-30" />
                No versions yet. Create a checkpoint to snapshot the current draft.
              </div>
            )}

            {versions.length > 0 && (
              <VersionTimeline
                versions={versions}
                pointers={pointers}
                restoring={restoring}
                viewing={viewing}
                onView={onView ? handleView : undefined}
                onRestore={handleRestore}
                onPromote={(envKey) => setPromoteFrom(envKey)}
              />
            )}
          </div>

          {/* Footer */}
          <div className="shrink-0 flex items-center justify-end px-6 py-4 border-t border-border">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-sm font-medium border border-border text-fg hover:bg-surface-2 transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>

      {/* Nested promote dialog, pre-filled from the clicked row */}
      <PromoteDialog
        kind={kind}
        resourceId={resourceId}
        open={promoteFrom !== null}
        onClose={() => { setPromoteFrom(null); load() }}
        environments={environments}
        defaultFrom={promoteFrom ?? 'dev'}
        defaultTo={promoteFrom === 'prod' ? 'dev' : 'prod'}
      />
    </>
  )
}
