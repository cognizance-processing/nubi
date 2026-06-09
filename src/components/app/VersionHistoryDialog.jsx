/**
 * VersionHistoryDialog — version history modal for a versioned resource
 * (flow / board / query).
 *
 * Lists the resource's checkpointed versions (newest first) with the
 * environment pointers shown as chips on the pinned rows. Per-row actions:
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
 *   environments {Array}?    optional env list passed through to PromoteDialog
 */

import { useCallback, useEffect, useState } from 'react'
import { History, Loader2, RefreshCw, Rocket, RotateCcw, X } from 'lucide-react'

import { listVersions, restoreVersion } from '../../lib/versions.js'
import PromoteDialog from './PromoteDialog.jsx'

function envChipClass(key) {
  if (key === 'prod') return 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
  if (key === 'dev') return 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30'
  return 'bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/30'
}

export default function VersionHistoryDialog({
  kind,
  resourceId,
  resourceName,
  open,
  onClose,
  onRestored,
  environments,
}) {
  const [loading, setLoading] = useState(false)
  const [versions, setVersions] = useState([])
  const [pointers, setPointers] = useState([])
  const [error, setError] = useState(null)
  const [restoring, setRestoring] = useState(null)   // version number in flight
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

  /** Env-pointer chips for one version row. */
  const chipsFor = (versionId) =>
    pointers.filter(p => p.version_id === versionId)

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

          {/* Body */}
          <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-1.5">
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

            {versions.map(v => {
              const chips = chipsFor(v.id)
              return (
                <div
                  key={v.id}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-xl border border-border bg-surface hover:bg-surface-2/60 transition-colors"
                >
                  <span className="shrink-0 w-10 text-xs font-mono font-semibold text-fg">
                    v{v.version}
                  </span>

                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-fg truncate">
                      {v.message || <span className="text-muted italic">No message</span>}
                    </p>
                    <p className="text-[10px] text-muted mt-0.5">
                      {v.created_at ? new Date(v.created_at).toLocaleString() : ''}
                    </p>
                  </div>

                  {/* Env-pointer badges */}
                  {chips.length > 0 && (
                    <div className="shrink-0 flex items-center gap-1">
                      {chips.map(p => (
                        <span
                          key={p.environment_id}
                          title={`Pinned to ${p.env_key}`}
                          className={[
                            'inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono font-medium rounded-md border',
                            envChipClass(p.env_key),
                          ].join(' ')}
                        >
                          {p.env_key}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Row actions */}
                  <div className="shrink-0 flex items-center gap-0.5">
                    <button
                      type="button"
                      onClick={() => handleRestore(v)}
                      disabled={restoring !== null}
                      title={`Restore v${v.version} into the draft`}
                      className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40"
                    >
                      {restoring === v.version
                        ? <Loader2 size={13} className="animate-spin" />
                        : <RotateCcw size={13} />}
                    </button>
                    <button
                      type="button"
                      onClick={() => setPromoteFrom(chips[0]?.env_key ?? 'dev')}
                      title="Promote between environments"
                      className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors"
                    >
                      <Rocket size={13} />
                    </button>
                  </div>
                </div>
              )
            })}
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
