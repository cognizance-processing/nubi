/**
 * PromoteDialog — promote a resource's pinned version between environments.
 *
 * Copies the `from_env` environment pointer to `to_env` via
 * POST /environments/promote. For flows the backend also best-effort copies
 * incremental watermarks; for boards it promotes referenced queries too.
 *
 * Props:
 *   kind         {'flow'|'board'|'query'}
 *   resourceId   {string}
 *   open         {boolean}
 *   onClose      {() => void}
 *   environments {Array<{key:string,name?:string,protected?:boolean}>}  selectable envs
 *   defaultFrom  {string}  default 'dev'
 *   defaultTo    {string}  default 'prod'
 *
 * After a successful promote the dialog shows the returned `promoted` list
 * (everything that moved) and the user closes it explicitly.
 *
 * Git layer (best-effort, pointers are authoritative): the promote response
 * may carry `git_merge` (from-env branch merged into to-env branch — shown as
 * a footnote), `git_warning`, or `git_conflict: {files, from_sha, to_sha}`.
 * A conflict does NOT roll back the promoted pointers; the dialog lists the
 * conflicting files and offers:
 *   - 'Resolve on provider' — link to the bound remote repo (GET /git/status)
 *   - 'Use environment state' — overwrite the to-env branch from its pinned
 *     state via POST /environments/{id}/git/pull {strategy: 'take_env'}
 *     (push --force-with-lease semantics).
 */

import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  GitMerge,
  Loader2,
  Rocket,
  Upload,
  X,
} from 'lucide-react'

import { promote } from '../../lib/versions.js'
import { pullEnvironment } from '../../lib/gitenv.js'
import { get } from '../../lib/api.js'
import { useProject } from '../../contexts/ProjectContext.jsx'

const FALLBACK_ENVS = [{ key: 'dev', name: 'Development' }, { key: 'prod', name: 'Production' }]

function envDotClass(key) {
  if (key === 'prod') return 'bg-emerald-500'
  if (key === 'dev') return 'bg-sky-500'
  return 'bg-violet-500'
}

export default function PromoteDialog({
  kind,
  resourceId,
  open,
  onClose,
  environments,
  defaultFrom = 'dev',
  defaultTo = 'prod',
}) {
  const { activeProject } = useProject()
  const envs = Array.isArray(environments) && environments.length > 0 ? environments : FALLBACK_ENVS
  const [fromEnv, setFromEnv] = useState(defaultFrom)
  const [toEnv, setToEnv] = useState(defaultTo)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [promoted, setPromoted] = useState(null) // result list after success
  // Best-effort git outcome of the promote: { merge?, conflict?, warning? }.
  const [git, setGit] = useState(null)
  const [gitBusy, setGitBusy] = useState(false)
  const [gitNotice, setGitNotice] = useState(null) // after 'Use environment state'
  const [repoUrl, setRepoUrl] = useState(null) // bound remote, for the provider link

  // Reset whenever the dialog is (re)opened for a resource.
  useEffect(() => {
    if (!open) return
    setFromEnv(defaultFrom)
    setToEnv(defaultTo)
    setBusy(false)
    setError(null)
    setPromoted(null)
    setGit(null)
    setGitBusy(false)
    setGitNotice(null)
  }, [open, resourceId, defaultFrom, defaultTo])

  // On a merge conflict, look up the project's remote binding (if any) so we
  // can link out to the provider. Graceful: no binding → no link.
  useEffect(() => {
    if (!git?.conflict || !activeProject?.id) return
    let cancelled = false
    get(`/git/status?project_id=${encodeURIComponent(activeProject.id)}`)
      .then(res => { if (!cancelled) setRepoUrl(res?.binding?.repo_url || null) })
      .catch(() => { if (!cancelled) setRepoUrl(null) })
    return () => { cancelled = true }
  }, [git?.conflict, activeProject?.id])

  // ESC to close
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onClose])

  if (!open) return null

  const sameEnv = fromEnv === toEnv

  async function handlePromote() {
    setBusy(true)
    setError(null)
    try {
      const result = await promote({ kind, resource_id: resourceId, from_env: fromEnv, to_env: toEnv })
      setPromoted(Array.isArray(result?.promoted) ? result.promoted : [])
      setGit(
        result?.git_merge || result?.git_conflict || result?.git_warning
          ? { merge: result.git_merge, conflict: result.git_conflict, warning: result.git_warning }
          : null,
      )
    } catch (cause) {
      setError(cause?.message || 'Promote failed.')
    } finally {
      setBusy(false)
    }
  }

  // Conflict resolution: overwrite the to-env branch from the environment's
  // pinned state (pull with strategy take_env — force-with-lease semantics).
  const toEnvRow = envs.find(e => e.key === toEnv && e.id)

  async function handleUseEnvState() {
    if (!toEnvRow?.id) return
    setGitBusy(true)
    try {
      const res = await pullEnvironment(toEnvRow.id, { strategy: 'take_env' })
      setGit(g => ({ ...g, conflict: null }))
      setGitNotice(
        res?.sha
          ? `Branch overwritten from "${toEnv}" @ ${String(res.sha).slice(0, 7)}.`
          : res?.warning || 'Branch overwritten from the environment state.',
      )
    } catch (cause) {
      setGitNotice(cause?.message || 'Could not overwrite the branch.')
    } finally {
      setGitBusy(false)
    }
  }

  const envSelect = (value, onChange, label) => (
    <label className="flex-1 min-w-0">
      <span className="block text-[11px] font-medium text-muted mb-1">{label}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={busy || promoted !== null}
        className="w-full h-9 px-2 rounded-xl border border-border bg-bg text-sm font-mono text-fg focus:outline-none focus:ring-2 focus:ring-ring/60 disabled:opacity-50"
      >
        {envs.map(env => (
          <option key={env.key} value={env.key}>{env.key}</option>
        ))}
      </select>
    </label>
  )

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm"
        onClick={busy ? undefined : onClose}
        aria-hidden="true"
      />

      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="promote-dialog-title"
        className="fixed inset-0 z-[60] flex items-center justify-center p-4 pointer-events-none"
      >
        <div
          className="pointer-events-auto w-full max-w-md bg-surface rounded-2xl border border-border shadow-2xl flex flex-col"
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start gap-3 px-6 pt-6 pb-4 border-b border-border">
            <div className="shrink-0 w-10 h-10 rounded-xl flex items-center justify-center bg-primary/10">
              <Rocket size={18} className="text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 id="promote-dialog-title" className="font-display font-semibold text-base text-fg">
                Promote {kind}
              </h2>
              <p className="text-sm text-muted mt-0.5">
                Copy the pinned version from one environment to another.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              aria-label="Close"
              className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
            >
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <div className="px-6 py-5 space-y-4">
            {promoted === null ? (
              <>
                <div className="flex items-end gap-2">
                  {envSelect(fromEnv, setFromEnv, 'From environment')}
                  <ArrowRight size={16} className="text-muted shrink-0 mb-2.5" />
                  {envSelect(toEnv, setToEnv, 'To environment')}
                </div>

                {/* What will move */}
                <div className="rounded-xl border border-border bg-surface-2/40 px-4 py-3 text-sm text-muted space-y-1">
                  <p>
                    The version pinned to{' '}
                    <span className="font-mono text-fg">{fromEnv}</span> will also be pinned to{' '}
                    <span className="font-mono text-fg">{toEnv}</span>.
                  </p>
                  {kind === 'flow' && (
                    <p className="text-xs">Incremental watermarks of materialized tasks are copied best-effort.</p>
                  )}
                  {kind === 'board' && (
                    <p className="text-xs">Queries referenced by the pinned board version are promoted too.</p>
                  )}
                </div>

                {sameEnv && (
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    Source and target environments must differ.
                  </p>
                )}
                {error && <p className="text-xs text-red-500">{error}</p>}
              </>
            ) : (
              /* ---- Result: what was promoted + git outcome ---- */
              <div className="space-y-3">
                <div className="rounded-xl border border-emerald-300/50 dark:border-emerald-700/50 bg-emerald-50 dark:bg-emerald-900/10 px-4 py-3 space-y-2">
                  <p className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-400">
                    <CheckCircle2 size={15} className="shrink-0" />
                    Promoted {promoted.length} item{promoted.length === 1 ? '' : 's'}
                  </p>
                  <ul className="space-y-1">
                    {promoted.map((item, i) => (
                      <li key={i} className="flex items-center gap-2 text-xs text-emerald-700 dark:text-emerald-400">
                        <span className={['w-1.5 h-1.5 rounded-full shrink-0', envDotClass(item.to_env)].join(' ')} />
                        <span className="font-medium">{item.kind}</span>
                        <span className="font-mono truncate">{String(item.resource_id).slice(0, 8)}…</span>
                        <span className="font-mono">v{item.version ?? '?'}</span>
                        <span className="text-muted ml-auto font-mono shrink-0">
                          {item.from_env} → {item.to_env}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Git merge footnote (best-effort, branches followed the pointers) */}
                {git?.merge?.merged && (
                  <p className="flex items-center gap-1.5 text-xs text-muted px-1">
                    <GitMerge size={12} className="shrink-0 text-violet-500" />
                    <span className="truncate">
                      Branch <span className="font-mono">{git.merge.from_branch}</span> merged into{' '}
                      <span className="font-mono">{git.merge.to_branch}</span>
                      {git.merge.sha ? <> @ <span className="font-mono">{String(git.merge.sha).slice(0, 7)}</span></> : null}
                      {git.merge.ff ? ' (fast-forward)' : ''}
                    </span>
                  </p>
                )}
                {git?.warning && !git?.conflict && (
                  <p className="text-xs text-muted px-1">{git.warning}</p>
                )}

                {/* Git merge conflict — pointers promoted, branches diverged */}
                {git?.conflict && (
                  <div className="rounded-xl border border-amber-300/60 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-900/10 px-4 py-3 space-y-2">
                    <p className="flex items-start gap-2 text-xs font-medium text-amber-700 dark:text-amber-400">
                      <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                      <span>
                        The promote succeeded, but the git branches could not be merged
                        (<span className="font-mono">{String(git.conflict.from_sha || '').slice(0, 7)}</span> vs{' '}
                        <span className="font-mono">{String(git.conflict.to_sha || '').slice(0, 7)}</span>).
                      </span>
                    </p>
                    {Array.isArray(git.conflict.files) && git.conflict.files.length > 0 && (
                      <ul className="max-h-24 overflow-y-auto space-y-0.5">
                        {git.conflict.files.map(file => (
                          <li key={file} className="text-[11px] font-mono text-amber-700/80 dark:text-amber-400/80 truncate">
                            {file}
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="flex flex-wrap items-center gap-2 pt-0.5">
                      <button
                        type="button"
                        onClick={handleUseEnvState}
                        disabled={gitBusy || !toEnvRow?.id}
                        title={`Overwrite the "${toEnv}" branch from its pinned environment state (force-with-lease)`}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border border-amber-400/60 text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-900/30 transition-colors disabled:opacity-50"
                      >
                        {gitBusy ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
                        Use environment state
                      </button>
                      {repoUrl && (
                        <a
                          href={repoUrl.replace(/\.git$/, '')}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-700 dark:text-amber-400 hover:underline"
                        >
                          Resolve on provider <ExternalLink size={10} />
                        </a>
                      )}
                    </div>
                  </div>
                )}
                {gitNotice && (
                  <p className="text-xs text-muted px-1">{gitNotice}</p>
                )}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-3 px-6 pb-6">
            {promoted === null ? (
              <>
                <button
                  type="button"
                  onClick={onClose}
                  disabled={busy}
                  className="px-4 py-2 rounded-xl text-sm font-medium border border-border text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handlePromote}
                  disabled={busy || sameEnv}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-primary-fg hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {busy ? <Loader2 size={15} className="animate-spin" /> : <Rocket size={15} />}
                  {busy ? 'Promoting…' : 'Promote'}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-primary-fg hover:opacity-90 transition-colors"
              >
                Done
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
