/**
 * GitSyncPanel — the unified, app-wide git surface (BUILD_PLAN.md Wave 2).
 *
 * One slide-in panel shared across dashboards, queries and flows: a single
 * project context with environment-branch push/pull, the project commit graph
 * and a read-only synced-file browser. It supersedes the scattered per-surface
 * git affordances — mount it once in AppShell (another agent wires the trigger).
 *
 * Sections
 *   Status header — connected repo / branch / last sync (GET /git/status,
 *                   the same shape GitPanel.jsx fetches), self-contained here.
 *   Sync         — Push / Pull for the ACTIVE environment via lib/gitenv.js
 *                  pushEnvironment / pullEnvironment, with the 409-divergence
 *                  resolver (divergedPayload → take_branch | take_env).
 *   Branch graph — opens the existing GitGraphDialog.jsx.
 *   Files        — embeds the existing GitFilesPanel.jsx (projectId).
 *
 * The active environment is read from useEnv() when an <EnvProvider> is in
 * scope; the panel falls back to an explicit `envId` prop otherwise, so it
 * stays self-contained and mountable anywhere.
 *
 * Props:
 *   projectId {string}        required — the project whose repo this drives.
 *   open      {boolean}       slide-in visibility.
 *   onClose   {() => void}    request to close.
 *   envId     {string=}       fallback active-env id when useEnv() is absent.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle,
  Download,
  Files,
  GitBranch,
  GitCommitHorizontal,
  Loader2,
  RefreshCw,
  Upload,
  X,
} from 'lucide-react'

import * as api from '../../lib/api.js'
import { pushEnvironment, pullEnvironment, divergedPayload } from '../../lib/gitenv.js'
import { useEnv, envDotClass } from '../../contexts/EnvContext.jsx'
import { shortSha, formatPushNotice, formatPullNotice } from '../../shell/shellLogic.js'
import GitGraphDialog from './GitGraphDialog.jsx'
import GitFilesPanel from './GitFilesPanel.jsx'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Read the active environment from EnvContext when available, degrading to a
 * plain fallback when no <EnvProvider> is mounted (useEnv throws otherwise).
 * Returns { env, refreshEnvs } where env may be null.
 */
function useActiveEnv(envId) {
  let ctx = null
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    ctx = useEnv()
  } catch {
    ctx = null
  }

  return useMemo(() => {
    if (ctx) {
      const list = ctx.environments ?? []
      const env =
        list.find(e => e.key === ctx.activeEnv) ??
        (envId ? list.find(e => e.id === envId) : null) ??
        (envId ? { id: envId, key: ctx.activeEnv } : null)
      return { env, refreshEnvs: ctx.refresh }
    }
    return {
      env: envId ? { id: envId, key: null } : null,
      refreshEnvs: async () => {},
    }
  }, [ctx, envId])
}

// ---------------------------------------------------------------------------
// Inline divergence resolver — shown after a 409 pull
// ---------------------------------------------------------------------------

function DivergedNotice({ diverged, busy, onResolve, onDismiss }) {
  return (
    <div className="rounded-xl border border-amber-300/60 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-900/10 px-3 py-2.5 space-y-2">
      <p className="flex items-start gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
        <AlertTriangle size={13} className="shrink-0 mt-0.5" />
        <span>
          Branch diverged — <span className="font-mono">{shortSha(diverged.branch_sha)}</span> on
          the branch vs <span className="font-mono">{shortSha(diverged.env_sha) || 'none'}</span>{' '}
          last synced. Choose which side to keep.
        </span>
      </p>
      {Array.isArray(diverged.files) && diverged.files.length > 0 && (
        <ul className="max-h-24 overflow-y-auto space-y-0.5">
          {diverged.files.map(file => (
            <li
              key={file}
              className="text-[11px] font-mono text-amber-700/80 dark:text-amber-400/80 truncate"
            >
              {file}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <button
          type="button"
          disabled={busy}
          onClick={() => onResolve('take_branch')}
          title="Import the branch state into the environment"
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border border-amber-400/60 text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-900/30 transition-colors disabled:opacity-50"
        >
          <Download size={11} /> Use branch
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onResolve('take_env')}
          title="Overwrite the branch from the environment's pinned state (force-with-lease)"
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border border-amber-400/60 text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-900/30 transition-colors disabled:opacity-50"
        >
          <Upload size={11} /> Use environment
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onDismiss}
          className="ml-auto text-[11px] text-muted hover:text-fg transition-colors disabled:opacity-50"
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Status header — connected repo / branch / last sync (GET /git/status)
// ---------------------------------------------------------------------------

function StatusHeader({ loading, error, status, onRetry }) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-bg border border-border text-sm text-muted">
        <Loader2 size={15} className="animate-spin shrink-0" />
        Checking git status…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-start gap-2 px-4 py-3 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300">
        <AlertTriangle size={15} className="shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <p>{error}</p>
          <button
            type="button"
            onClick={onRetry}
            className="mt-1 text-[11px] font-medium underline hover:no-underline"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  const binding = status?.binding
  if (!status?.connected || !binding) {
    return (
      <div className="flex items-start gap-2.5 px-4 py-3 rounded-xl bg-bg border border-border">
        <GitBranch size={16} className="text-muted/60 shrink-0 mt-0.5" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-fg">No repository connected</p>
          <p className="text-xs text-muted mt-0.5">
            Connect a remote in project settings to sync dashboards, queries and flows.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-2.5 px-4 py-3 rounded-xl bg-bg border border-border">
      <CheckCircle size={16} className="text-emerald-500 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-sm font-medium text-fg">
          <span className="capitalize">{binding.provider}</span>
          <span className="text-muted">·</span>
          <span className="inline-flex items-center gap-1 text-muted">
            <GitBranch size={13} /> {binding.branch}
          </span>
        </div>
        {binding.repo_url && (
          <p className="text-xs text-muted truncate mt-0.5" title={binding.repo_url}>
            {binding.repo_url}
          </p>
        )}
        {status?.last_sync && (
          <p className="text-xs text-muted mt-1 truncate">
            Last sync: <span className="font-mono">{shortSha(status.last_sync.sha)}</span>
            {status.last_sync.message ? ` — ${status.last_sync.message}` : ''}
          </p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// GitSyncPanel — main export
// ---------------------------------------------------------------------------

export default function GitSyncPanel({ projectId, open, onClose, envId }) {
  const { env, refreshEnvs } = useActiveEnv(envId)

  const [view, setView] = useState('sync') // 'sync' | 'files'
  const [graphOpen, setGraphOpen] = useState(false)

  // Status header state (self-contained, mirrors GitPanel's /git/status fetch)
  const [statusLoading, setStatusLoading] = useState(false)
  const [status, setStatus] = useState(null)
  const [statusError, setStatusError] = useState(null)

  // Sync action state
  const [busy, setBusy] = useState(null) // 'push' | 'pull' | null
  const [notice, setNotice] = useState(null) // { kind: 'ok'|'error', text }
  const [diverged, setDiverged] = useState(null) // 409 payload | null

  const loadStatus = useCallback(async () => {
    if (!projectId) {
      setStatus(null)
      return
    }
    setStatusLoading(true)
    setStatusError(null)
    try {
      const res = await api.get(`/git/status?project_id=${encodeURIComponent(projectId)}`)
      setStatus(res)
    } catch (cause) {
      setStatusError(cause?.message || 'Failed to load git status.')
    } finally {
      setStatusLoading(false)
    }
  }, [projectId])

  // (Re)load whenever the panel opens.
  useEffect(() => {
    if (!open) return
    setView('sync')
    setNotice(null)
    setDiverged(null)
    setBusy(null)
    loadStatus()
  }, [open, loadStatus])

  // ESC to close (never mid-flight).
  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape' && !busy) onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onClose])

  async function handlePush() {
    if (!env?.id) return
    setBusy('push')
    setNotice(null)
    try {
      const res = await pushEnvironment(env.id)
      setNotice({ kind: 'ok', text: formatPushNotice(res) })
      await Promise.all([loadStatus(), refreshEnvs()])
    } catch (cause) {
      setNotice({ kind: 'error', text: cause?.message || 'Push failed.' })
    } finally {
      setBusy(null)
    }
  }

  async function handlePull(strategy) {
    if (!env?.id) return
    setBusy('pull')
    setNotice(null)
    try {
      const res = await pullEnvironment(env.id, strategy ? { strategy } : {})
      setDiverged(null)
      setNotice({ kind: 'ok', text: formatPullNotice(res) })
      await Promise.all([loadStatus(), refreshEnvs()])
    } catch (cause) {
      const payload = divergedPayload(cause)
      if (payload) {
        setDiverged(payload)
      } else {
        setNotice({ kind: 'error', text: cause?.message || 'Pull failed.' })
      }
    } finally {
      setBusy(null)
    }
  }

  if (!open) return null

  const envKey = env?.key
  const canSync = !!env?.id && busy === null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[55] bg-black/40 backdrop-blur-[1px]"
        onClick={busy ? undefined : onClose}
        aria-hidden="true"
      />

      {/* Slide-in panel */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="git-sync-panel-title"
        className="fixed inset-y-0 right-0 z-[55] w-full max-w-md bg-surface border-l border-border shadow-2xl flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border shrink-0">
          <div className="shrink-0 w-9 h-9 rounded-xl flex items-center justify-center bg-primary/10">
            <GitBranch size={17} className="text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id="git-sync-panel-title" className="font-display font-semibold text-base text-fg">
              Git sync
            </h2>
            <p className="text-xs text-muted truncate">
              Push, pull and browse this project's synced repository.
            </p>
          </div>
          <button
            type="button"
            onClick={() => loadStatus()}
            disabled={statusLoading || busy !== null}
            aria-label="Refresh status"
            title="Refresh status"
            className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <RefreshCw size={15} className={statusLoading ? 'animate-spin' : ''} />
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={busy !== null}
            aria-label="Close git sync panel"
            className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <X size={16} />
          </button>
        </div>

        {/* Tabs */}
        <div
          role="tablist"
          aria-label="Git sync sections"
          className="flex items-center gap-1 px-5 pt-3 shrink-0"
        >
          <button
            type="button"
            role="tab"
            aria-selected={view === 'sync'}
            onClick={() => setView('sync')}
            className={[
              'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
              view === 'sync'
                ? 'bg-primary/10 text-primary'
                : 'text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            <GitCommitHorizontal size={14} /> Sync
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'files'}
            onClick={() => setView('files')}
            className={[
              'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
              view === 'files'
                ? 'bg-primary/10 text-primary'
                : 'text-muted hover:text-fg hover:bg-surface-2',
            ].join(' ')}
          >
            <Files size={14} /> Files
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          {view === 'sync' ? (
            <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4 space-y-4">
              {/* Status header */}
              <StatusHeader
                loading={statusLoading}
                error={statusError}
                status={status}
                onRetry={loadStatus}
              />

              {/* Active environment + Push/Pull */}
              <div className="rounded-xl border border-border bg-surface-2/40 px-4 py-3.5 space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Sync</span>
                  {envKey ? (
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-surface border border-border text-[11px] font-mono text-fg">
                      <span className={`w-1.5 h-1.5 rounded-full ${envDotClass(envKey)}`} />
                      {envKey}
                    </span>
                  ) : (
                    <span className="text-[11px] text-muted/70">none</span>
                  )}
                </div>

                {!env?.id ? (
                  <p className="text-xs text-muted">
                    No environment is selected, so push and pull are unavailable. Open the branch
                    graph to sync a specific environment branch.
                  </p>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={handlePush}
                      disabled={!canSync}
                      title={`Push pinned ${envKey ?? 'env'} resources to its branch`}
                      className="inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold bg-primary text-primary-fg hover:bg-primary/90 transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                    >
                      {busy === 'push' ? (
                        <Loader2 size={15} className="animate-spin" />
                      ) : (
                        <Upload size={15} />
                      )}
                      Push
                    </button>
                    <button
                      type="button"
                      onClick={() => handlePull()}
                      disabled={!canSync}
                      title={`Pull this environment's branch into ${envKey ?? 'env'}`}
                      className="inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl text-sm font-medium text-fg bg-surface border border-border hover:border-primary hover:bg-surface-2 transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                    >
                      {busy === 'pull' ? (
                        <Loader2 size={15} className="animate-spin" />
                      ) : (
                        <Download size={15} />
                      )}
                      Pull
                    </button>
                  </div>
                )}

                {/* Divergence resolver */}
                {diverged && (
                  <DivergedNotice
                    diverged={diverged}
                    busy={busy !== null}
                    onResolve={(strategy) => handlePull(strategy)}
                    onDismiss={() => setDiverged(null)}
                  />
                )}

                {/* Action feedback */}
                {notice && (
                  <div
                    className={[
                      'flex items-start gap-2 px-3 py-2.5 rounded-xl text-xs',
                      notice.kind === 'error'
                        ? 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300'
                        : 'bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300',
                    ].join(' ')}
                  >
                    {notice.kind === 'error' ? (
                      <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                    ) : (
                      <CheckCircle size={14} className="shrink-0 mt-0.5" />
                    )}
                    <span className="min-w-0">{notice.text}</span>
                  </div>
                )}
              </div>

              {/* Branch graph entry */}
              <button
                type="button"
                onClick={() => setGraphOpen(true)}
                className="w-full inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium text-fg bg-bg border border-border hover:border-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              >
                <GitBranch size={15} className="text-muted" />
                Branch graph
                <span className="ml-auto text-xs text-muted">
                  per-environment commits
                </span>
              </button>
            </div>
          ) : (
            /* Files tab — embed the existing read-only browser */
            <div className="flex-1 min-h-0 overflow-hidden px-5 py-4">
              {projectId ? (
                <GitFilesPanel projectId={projectId} />
              ) : (
                <div className="flex flex-col items-center justify-center h-full gap-2 text-sm text-muted">
                  <Files size={26} className="opacity-30" />
                  <span>No project selected.</span>
                </div>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* Per-environment commit graph */}
      <GitGraphDialog open={graphOpen} onClose={() => setGraphOpen(false)} />
    </>
  )
}
