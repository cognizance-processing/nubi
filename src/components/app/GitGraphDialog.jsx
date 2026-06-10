/**
 * GitGraphDialog — commit graph of the active project's env-bound branches.
 *
 * Renders GET /projects/{pid}/git/graph (lib/gitenv.js getGitGraph) as one
 * column per branch: a header chip pairing the branch name with its
 * environment, then the branch's commits as dots on a vertical rail (newest
 * first; merge commits are marked and show their second parent's short sha —
 * deliberately no graph library, just CSS rails).
 *
 * Per-branch actions (resolved to the environment via useEnv()):
 *   Push  — POST /environments/{id}/git/push  (env pins → branch → remote)
 *   Pull  — POST /environments/{id}/git/pull; a diverged 409 opens an inline
 *           panel listing the conflicting files with two resolutions:
 *           'Use branch' (strategy take_branch — import branch into the env)
 *           and 'Use environment' (take_env — overwrite the branch from the
 *           env's pinned state, force-with-lease semantics).
 *   New environment from branch — creates an env seeded from the branch
 *           (EnvContext addEnv with from_branch).
 *
 * Empty state (no workspace repo yet) explains that commits appear after the
 * first checkpoint and links out to the GitPanel in project settings to
 * connect a remote.
 *
 * Props:
 *   open    {boolean}
 *   onClose {() => void}
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Download,
  GitBranch,
  GitMerge,
  Loader2,
  Plus,
  RefreshCw,
  Upload,
  X,
} from 'lucide-react'

import { getGitGraph, pullEnvironment, pushEnvironment, divergedPayload } from '../../lib/gitenv.js'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { useEnv, envDotClass } from '../../contexts/EnvContext.jsx'

const COMMITS_SHOWN = 30

function shortSha(sha) {
  return (sha || '').slice(0, 7)
}

function shortDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// ---------------------------------------------------------------------------
// Inline divergence panel — shown under a branch column after a 409 pull
// ---------------------------------------------------------------------------

function DivergedPanel({ diverged, busy, onResolve, onDismiss }) {
  return (
    <div className="rounded-xl border border-amber-300/60 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-900/10 px-3 py-2.5 space-y-2">
      <p className="flex items-start gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
        <AlertTriangle size={13} className="shrink-0 mt-0.5" />
        <span>
          Branch diverged — <span className="font-mono">{shortSha(diverged.branch_sha)}</span> on
          the branch vs <span className="font-mono">{shortSha(diverged.env_sha) || 'none'}</span>{' '}
          last synced.
        </span>
      </p>
      {Array.isArray(diverged.files) && diverged.files.length > 0 && (
        <ul className="max-h-24 overflow-y-auto space-y-0.5">
          {diverged.files.map(file => (
            <li key={file} className="text-[11px] font-mono text-amber-700/80 dark:text-amber-400/80 truncate">
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
// One branch column
// ---------------------------------------------------------------------------

function BranchColumn({ branch, env, sharedShas, onPush, onPull, onNewEnv, busy, notice, diverged, onResolve, onDismissDiverged }) {
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState('')

  const commits = Array.isArray(branch.commits) ? branch.commits : []
  const shown = commits.slice(0, COMMITS_SHOWN)
  const hidden = commits.length - shown.length

  async function commitNewEnv() {
    const key = draft.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '')
    if (!key) return
    const ok = await onNewEnv(key, branch.branch)
    if (ok) {
      setCreating(false)
      setDraft('')
    }
  }

  return (
    <div className="w-64 shrink-0 flex flex-col gap-2.5">
      {/* Header: branch + env chip */}
      <div className="rounded-xl border border-border bg-surface-2/50 px-3 py-2.5">
        <div className="flex items-center gap-1.5 min-w-0">
          <GitBranch size={13} className="text-muted shrink-0" />
          <span className="font-mono text-xs font-semibold text-fg truncate">{branch.branch}</span>
          <span className="ml-auto font-mono text-[10px] text-muted/70 shrink-0">{shortSha(branch.head_sha)}</span>
        </div>
        <div className="flex items-center gap-1.5 mt-1.5">
          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-surface border border-border text-[10px] font-mono text-fg ${env ? '' : 'opacity-60'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${envDotClass(branch.env_key)}`} />
            {branch.env_key}
          </span>
          {/* Actions */}
          <span className="ml-auto flex items-center gap-1">
            <button
              type="button"
              disabled={!env || busy !== null}
              onClick={onPush}
              title={env ? `Push pinned ${branch.env_key} resources to ${branch.branch}` : 'Environment not loaded'}
              aria-label={`Push environment ${branch.env_key}`}
              className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40"
            >
              {busy === 'push' ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            </button>
            <button
              type="button"
              disabled={!env || busy !== null}
              onClick={onPull}
              title={env ? `Pull ${branch.branch} into ${branch.env_key}` : 'Environment not loaded'}
              aria-label={`Pull environment ${branch.env_key}`}
              className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40"
            >
              {busy === 'pull' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => setCreating(v => !v)}
              title={`New environment from ${branch.branch}`}
              aria-label={`New environment from branch ${branch.branch}`}
              className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40"
            >
              {busy === 'newenv' ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            </button>
          </span>
        </div>

        {/* Inline new-env-from-branch form */}
        {creating && (
          <div className="flex items-center gap-1 mt-2">
            <input
              type="text"
              autoFocus
              value={draft}
              placeholder="staging"
              aria-label={`New environment key (from branch ${branch.branch})`}
              className="h-7 flex-1 min-w-0 text-xs font-mono border border-border rounded-md px-2 bg-surface text-fg placeholder:text-muted/50 focus:outline-none focus:ring-2 focus:ring-ring/60"
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') commitNewEnv()
                if (e.key === 'Escape') { setCreating(false); setDraft('') }
              }}
            />
            <button
              type="button"
              onClick={commitNewEnv}
              disabled={busy !== null}
              className="h-7 px-2 rounded-md text-xs font-medium bg-primary text-primary-fg hover:opacity-90 transition-opacity shrink-0 disabled:opacity-50"
            >
              Create
            </button>
          </div>
        )}
      </div>

      {/* Per-branch feedback */}
      {notice && (
        <p className={`text-[11px] px-1 ${notice.kind === 'error' ? 'text-red-500' : 'text-muted'}`}>
          {notice.text}
        </p>
      )}
      {diverged && (
        <DivergedPanel
          diverged={diverged}
          busy={busy !== null}
          onResolve={onResolve}
          onDismiss={onDismissDiverged}
        />
      )}

      {/* Commit rail */}
      <div className="relative pl-1">
        {shown.length > 1 && (
          <span
            aria-hidden="true"
            className="absolute left-[9px] top-2 bottom-2 w-px bg-border"
          />
        )}
        {shown.length === 0 ? (
          <p className="text-xs text-muted px-1 py-2">No commits yet.</p>
        ) : (
          <ul className="space-y-0.5">
            {shown.map((commit, i) => {
              const isHead = i === 0
              const isMerge = Array.isArray(commit.parents) && commit.parents.length > 1
              const shared = sharedShas.has(commit.sha)
              return (
                <li key={commit.sha} className="relative flex items-start gap-2.5 py-1">
                  {/* Dot — head gets the env accent, shared commits are hollow */}
                  <span className="relative z-10 shrink-0 mt-1 w-[9px] flex justify-center">
                    <span
                      className={[
                        'w-[9px] h-[9px] rounded-full border-2',
                        isHead
                          ? `${envDotClass(branch.env_key)} border-transparent ring-2 ring-primary/20`
                          : shared
                            ? 'bg-surface border-muted/50'
                            : 'bg-muted/70 border-transparent',
                      ].join(' ')}
                    />
                  </span>
                  <span className="min-w-0 flex-1 leading-tight">
                    <span className="block text-xs text-fg truncate" title={commit.message}>
                      {commit.message || '(no message)'}
                    </span>
                    <span className="flex items-center gap-1.5 text-[10px] text-muted/70 font-mono">
                      <span>{shortSha(commit.sha)}</span>
                      {isMerge && (
                        <span className="inline-flex items-center gap-0.5 text-violet-500/90" title={`Merge — parents ${commit.parents.map(shortSha).join(', ')}`}>
                          <GitMerge size={10} />
                          {shortSha(commit.parents[1])}
                        </span>
                      )}
                      {commit.date && <span className="ml-auto shrink-0">{shortDate(commit.date)}</span>}
                    </span>
                  </span>
                </li>
              )
            })}
          </ul>
        )}
        {hidden > 0 && (
          <p className="text-[10px] text-muted/60 pl-6 pt-1">+{hidden} earlier commit{hidden === 1 ? '' : 's'}</p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Dialog
// ---------------------------------------------------------------------------

export default function GitGraphDialog({ open, onClose }) {
  const { activeProject } = useProject()
  const projectId = activeProject?.id ?? null
  const { environments, refresh: refreshEnvs, addEnv } = useEnv()

  const [graph, setGraph] = useState(null) // null = loading; {branches: []} once loaded
  const [busy, setBusy] = useState(null) // { branch, action } | null
  const [notices, setNotices] = useState({}) // branch -> { kind, text }
  const [diverged, setDiverged] = useState({}) // branch -> 409 payload

  const load = useCallback(async () => {
    if (!projectId) {
      setGraph({ branches: [] })
      return
    }
    const data = await getGitGraph(projectId)
    setGraph(data && Array.isArray(data.branches) ? data : { branches: [] })
  }, [projectId])

  // (Re)load whenever the dialog opens.
  useEffect(() => {
    if (!open) return
    setGraph(null)
    setNotices({})
    setDiverged({})
    setBusy(null)
    load()
  }, [open, load])

  // ESC to close
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onClose])

  const branches = useMemo(() => graph?.branches ?? [], [graph])

  // Shas appearing on more than one branch render as hollow "shared" dots.
  const sharedShas = useMemo(() => {
    const seen = new Map()
    for (const b of branches) {
      for (const c of b.commits ?? []) seen.set(c.sha, (seen.get(c.sha) ?? 0) + 1)
    }
    return new Set([...seen.entries()].filter(([, n]) => n > 1).map(([sha]) => sha))
  }, [branches])

  const envByKey = useMemo(() => {
    const map = new Map()
    for (const env of environments ?? []) map.set(env.key, env)
    return map
  }, [environments])

  if (!open) return null

  function setNotice(branch, kind, text) {
    setNotices(prev => ({ ...prev, [branch]: text ? { kind, text } : undefined }))
  }

  async function handlePush(branch, env) {
    setBusy({ branch: branch.branch, action: 'push' })
    setNotice(branch.branch, 'info', null)
    try {
      const res = await pushEnvironment(env.id)
      const warn = res?.warnings?.length ? ` (${res.warnings.join('; ')})` : ''
      setNotice(
        branch.branch,
        'info',
        res?.committed
          ? `Committed ${res.files} file${res.files === 1 ? '' : 's'} @ ${shortSha(res.sha)}${res.pushed ? ', pushed to remote' : ''}${warn}`
          : `Nothing to commit${warn}`,
      )
      await Promise.all([load(), refreshEnvs()])
    } catch (cause) {
      setNotice(branch.branch, 'error', cause?.message || 'Push failed.')
    } finally {
      setBusy(null)
    }
  }

  async function handlePull(branch, env, strategy) {
    setBusy({ branch: branch.branch, action: 'pull' })
    setNotice(branch.branch, 'info', null)
    try {
      const res = await pullEnvironment(env.id, strategy ? { strategy } : {})
      setDiverged(prev => ({ ...prev, [branch.branch]: undefined }))
      if (res?.up_to_date) {
        setNotice(branch.branch, 'info', 'Already up to date.')
      } else if (res?.strategy === 'take_env') {
        setNotice(branch.branch, 'info', `Branch overwritten from environment @ ${shortSha(res.sha)}`)
      } else if (res?.pulled) {
        const counts = Object.entries(res.updated ?? {})
          .map(([kind, n]) => `${n} ${kind}${n === 1 ? '' : 's'}`)
          .join(', ')
        setNotice(branch.branch, 'info', `Pulled ${counts || 'changes'} @ ${shortSha(res.sha)}`)
      } else {
        setNotice(branch.branch, 'info', res?.warning || 'Nothing to pull.')
      }
      await Promise.all([load(), refreshEnvs()])
    } catch (cause) {
      const payload = divergedPayload(cause)
      if (payload) {
        setDiverged(prev => ({ ...prev, [branch.branch]: payload }))
      } else {
        setNotice(branch.branch, 'error', cause?.message || 'Pull failed.')
      }
    } finally {
      setBusy(null)
    }
  }

  async function handleNewEnv(key, fromBranch) {
    if (envByKey.has(key)) {
      setNotice(fromBranch, 'error', `Environment "${key}" already exists.`)
      return false
    }
    setBusy({ branch: fromBranch, action: 'newenv' })
    setNotice(fromBranch, 'info', null)
    try {
      const created = await addEnv(key, { from_branch: fromBranch })
      const counts = Object.entries(created?.imported ?? {})
        .map(([kind, n]) => `${n} ${kind}${n === 1 ? '' : 's'}`)
        .join(', ')
      setNotice(
        fromBranch,
        'info',
        created?.warning
          ? `Environment "${key}" created — ${created.warning}`
          : `Environment "${key}" created from ${fromBranch}${counts ? ` (${counts})` : ''}.`,
      )
      await load()
      return true
    } catch (cause) {
      setNotice(fromBranch, 'error', cause?.message || 'Could not create environment.')
      return false
    } finally {
      setBusy(null)
    }
  }

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
        aria-labelledby="git-graph-dialog-title"
        className="fixed inset-0 z-[60] flex items-center justify-center p-4 pointer-events-none"
      >
        <div
          className="pointer-events-auto w-full max-w-3xl max-h-[85vh] bg-surface rounded-2xl border border-border shadow-2xl flex flex-col"
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start gap-3 px-6 pt-6 pb-4 border-b border-border">
            <div className="shrink-0 w-10 h-10 rounded-xl flex items-center justify-center bg-primary/10">
              <GitBranch size={18} className="text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 id="git-graph-dialog-title" className="font-display font-semibold text-base text-fg">
                Branch graph
              </h2>
              <p className="text-sm text-muted mt-0.5 truncate">
                Environment branches in {activeProject?.name ?? 'this project'} — push pins to git, pull git into pins.
              </p>
            </div>
            <button
              type="button"
              onClick={() => load()}
              disabled={graph === null || busy !== null}
              aria-label="Refresh graph"
              title="Refresh graph"
              className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={15} className={graph === null ? 'animate-spin' : ''} />
            </button>
            <button
              type="button"
              onClick={onClose}
              disabled={busy !== null}
              aria-label="Close"
              className="shrink-0 p-1.5 rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
            >
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 min-h-0 overflow-auto px-6 py-5">
            {graph === null ? (
              <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
                <Loader2 size={16} className="animate-spin" /> Loading graph…
              </div>
            ) : branches.length === 0 ? (
              /* ---- Empty state: no workspace repo yet ---- */
              <div className="flex flex-col items-center text-center gap-3 py-10 px-6">
                <div className="w-12 h-12 rounded-2xl bg-surface-2 border border-border flex items-center justify-center">
                  <GitBranch size={20} className="text-muted" />
                </div>
                <p className="text-sm font-medium text-fg">No git history yet</p>
                <p className="text-sm text-muted max-w-sm">
                  This project has no git workspace yet. Branches appear after your first
                  checkpoint — every environment commits to its own branch
                  (<span className="font-mono text-xs">prod → main</span>,{' '}
                  <span className="font-mono text-xs">dev → dev</span>).
                </p>
                <Link
                  to="/settings/project"
                  onClick={onClose}
                  className="text-sm font-medium text-primary hover:underline"
                >
                  Connect a remote repository in project settings →
                </Link>
              </div>
            ) : (
              /* ---- Branch columns ---- */
              <div className="flex gap-5 items-start min-w-0 overflow-x-auto pb-1">
                {branches.map(branch => (
                  <BranchColumn
                    key={branch.branch}
                    branch={branch}
                    env={envByKey.get(branch.env_key) ?? null}
                    sharedShas={sharedShas}
                    busy={busy?.branch === branch.branch ? busy.action : null}
                    notice={notices[branch.branch]}
                    diverged={diverged[branch.branch]}
                    onPush={() => handlePush(branch, envByKey.get(branch.env_key))}
                    onPull={() => handlePull(branch, envByKey.get(branch.env_key))}
                    onResolve={(strategy) => handlePull(branch, envByKey.get(branch.env_key), strategy)}
                    onDismissDiverged={() => setDiverged(prev => ({ ...prev, [branch.branch]: undefined }))}
                    onNewEnv={handleNewEnv}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
