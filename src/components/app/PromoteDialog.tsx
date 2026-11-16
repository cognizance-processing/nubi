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
 * Before promoting, a change summary says what moves: the version pinned in each
 * environment and, for boards, how the two specs differ (widgets added, edited,
 * moved or removed) so nobody promotes blind.
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

import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  GitMerge,
  Rocket,
  Upload,
} from 'lucide-react'

import { promote, listVersions, getVersion } from '../../lib/versions.js'
import { pullEnvironment } from '../../lib/gitenv.js'
import { get } from '../../lib/api.js'
import { useProject } from '../../contexts/ProjectContext.jsx'
import { summarizeSpecChanges, describeBreakdown } from '../../editor/boardMeta.js'
import Modal from '../ui/Modal.jsx'
import Button from '../ui/Button.jsx'
import Tag from '../ui/Tag.jsx'
import Skeleton from '../ui/Skeleton.jsx'
import '../../dashboards/widgets/chrome.css'

const FALLBACK_ENVS = [{ key: 'dev', name: 'Development' }, { key: 'prod', name: 'Production' }]

function envVariant(key) {
  if (key === 'prod') return 'success'
  if (key === 'dev') return 'neutral'
  return 'accent'
}

/**
 * Change summary for promoting `from` → `to`.
 *
 * Fetches the version list once for the resource, finds the version each
 * environment points at, and (boards only) loads both specs to diff them.
 * Returns { loading, from, to, diff } where from/to are `{ version } | null`.
 */
function usePromoteSummary({ open, kind, resourceId, fromEnv, toEnv, versionsKey }) {
  const [state, setState] = useState({ loading: false, from: null, to: null, diff: null, failed: false })

  useEffect(() => {
    if (!open || !resourceId || fromEnv === toEnv) {
      setState({ loading: false, from: null, to: null, diff: null, failed: false })
      return
    }
    let cancelled = false
    setState(s => ({ ...s, loading: true, failed: false }))
    ;(async () => {
      const data = await listVersions(kind, resourceId)
      if (cancelled) return
      if (!data) { setState({ loading: false, from: null, to: null, diff: null, failed: true }); return }
      const pin = (env) => {
        const p = (data.pointers ?? []).find(x => x.env_key === env)
        return p ? { version: p.version } : null
      }
      const from = pin(fromEnv)
      const to = pin(toEnv)
      let diff = null
      if (kind === 'board' && from && to && from.version !== to.version) {
        const [a, b] = await Promise.all([
          getVersion('board', resourceId, to.version),
          getVersion('board', resourceId, from.version),
        ])
        if (cancelled) return
        const specA = a?.config?.spec
        const specB = b?.config?.spec
        if (specA && specB) diff = summarizeSpecChanges(specA, specB)
      }
      setState({ loading: false, from, to, diff, failed: false })
    })()
    return () => { cancelled = true }
  }, [open, kind, resourceId, fromEnv, toEnv, versionsKey])

  return state
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

  const sameEnv = fromEnv === toEnv
  const summary = usePromoteSummary({
    open: open && promoted === null, kind, resourceId, fromEnv, toEnv, versionsKey: 0,
  })

  if (!open) return null

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
    <label className="min-w-0 flex-1">
      <span className="nbc-label mb-1 block">{label}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={busy || promoted !== null}
        className="nubi-input nubi-input-md nubi-select font-mono"
      >
        {envs.map(env => (
          <option key={env.key} value={env.key}>{env.key}</option>
        ))}
      </select>
    </label>
  )

  return (
    <Modal
      open={open}
      onClose={() => { if (!busy) onClose() }}
      size="md"
      title={`Promote ${kind}`}
      description="Copy the pinned version from one environment to another."
      persistent={busy}
    >
      <Modal.Body data-testid="promote-dialog" className="space-y-4">
        {promoted === null ? (
          <>
            <div className="flex items-end gap-2">
              {envSelect(fromEnv, setFromEnv, 'From')}
              <ArrowRight size={16} strokeWidth={1.5} className="mb-[11px] shrink-0 text-muted" aria-hidden="true" />
              {envSelect(toEnv, setToEnv, 'To')}
            </div>

            {/* What will move: the version pinned in each env, and (boards) how
                the two specs differ. */}
            <PromoteSummary
              kind={kind}
              fromEnv={fromEnv}
              toEnv={toEnv}
              sameEnv={sameEnv}
              {...summary}
            />

            {sameEnv && (
              <p role="alert" className="m-0 text-[13px] text-warning-fg">
                Source and target environments must differ.
              </p>
            )}
            {error && <p role="alert" className="m-0 text-[13px] text-danger-fg">{error}</p>}
          </>
        ) : (
          /* ---- Result: what was promoted + git outcome ---- */
          <div className="space-y-3">
            <div className="nubi-notice nubi-notice-success items-start" role="status">
              <div className="min-w-0 flex-1">
                <p className="m-0 text-[13px] font-semibold">
                  Promoted {promoted.length} item{promoted.length === 1 ? '' : 's'}
                </p>
                <ul className="m-0 mt-2 list-none space-y-1.5 p-0">
                  {promoted.map((item, i) => (
                    <li key={i} className="flex items-center gap-2 text-[12px]">
                      <span className="font-medium">{item.kind}</span>
                      <span className="nbc-meta truncate !text-current">{String(item.resource_id).slice(0, 8)}</span>
                      <span className="nbc-meta !text-current">v{item.version ?? '?'}</span>
                      <span className="nbc-meta ml-auto shrink-0 !text-current">
                        {item.from_env} → {item.to_env}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            {/* Git merge footnote (best-effort, branches followed the pointers) */}
            {git?.merge?.merged && (
              <p className="m-0 flex items-center gap-1.5 text-[12px] text-muted">
                <GitMerge size={14} strokeWidth={1.5} className="shrink-0" aria-hidden="true" />
                <span className="truncate">
                  Branch <span className="font-mono">{git.merge.from_branch}</span> merged into{' '}
                  <span className="font-mono">{git.merge.to_branch}</span>
                  {git.merge.sha ? <> @ <span className="font-mono">{String(git.merge.sha).slice(0, 7)}</span></> : null}
                  {git.merge.ff ? ' (fast-forward)' : ''}
                </span>
              </p>
            )}
            {git?.warning && !git?.conflict && (
              <p className="m-0 text-[12px] text-muted">{git.warning}</p>
            )}

            {/* Git merge conflict — pointers promoted, branches diverged */}
            {git?.conflict && (
              <div className="nubi-notice nubi-notice-warning items-start" role="alert">
                <AlertTriangle size={16} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden="true" />
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="m-0 text-[13px] font-semibold">
                    The promote succeeded, but the git branches could not be merged
                    (<span className="font-mono">{String(git.conflict.from_sha || '').slice(0, 7)}</span> vs{' '}
                    <span className="font-mono">{String(git.conflict.to_sha || '').slice(0, 7)}</span>).
                  </p>
                  {Array.isArray(git.conflict.files) && git.conflict.files.length > 0 && (
                    <ul className="m-0 max-h-24 list-none space-y-0.5 overflow-y-auto p-0">
                      {git.conflict.files.map(file => (
                        <li key={file} className="truncate font-mono text-[11px]">{file}</li>
                      ))}
                    </ul>
                  )}
                  <div className="flex flex-wrap items-center gap-2 pt-0.5">
                    <Button
                      size="xs"
                      variant="secondary"
                      onClick={handleUseEnvState}
                      loading={gitBusy}
                      disabled={!toEnvRow?.id}
                      title={`Overwrite the "${toEnv}" branch from its pinned environment state (force-with-lease)`}
                    >
                      {!gitBusy && <Upload size={14} strokeWidth={1.5} aria-hidden="true" />}
                      Use environment state
                    </Button>
                    {repoUrl && (
                      <a
                        href={repoUrl.replace(/\.git$/, '')}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-[12px] font-medium underline-offset-2 hover:underline"
                      >
                        Resolve on provider <ExternalLink size={12} strokeWidth={1.5} aria-hidden="true" />
                      </a>
                    )}
                  </div>
                </div>
              </div>
            )}
            {gitNotice && (
              <p className="m-0 text-[12px] text-muted" role="status">{gitNotice}</p>
            )}
          </div>
        )}
      </Modal.Body>

      <Modal.Footer>
        {promoted === null ? (
          <>
            <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button size="sm" onClick={handlePromote} loading={busy} disabled={sameEnv}>
              {!busy && <Rocket size={14} strokeWidth={1.5} aria-hidden="true" />}
              {busy ? 'Promoting…' : `Promote to ${toEnv}`}
            </Button>
          </>
        ) : (
          <Button size="sm" onClick={onClose}>Done</Button>
        )}
      </Modal.Footer>
    </Modal>
  )
}

/**
 * PromoteSummary — "what will move", as a ruled block.
 *
 *   WHAT MOVES
 *   dev  v5   →   prod  v2
 *   2 added · 1 edited · 1 moved
 *   added    Revenue by region
 *   edited   Total orders
 */
function PromoteSummary({ kind, fromEnv, toEnv, sameEnv, loading, from, to, diff, failed }) {
  const rows = useMemo(() => (diff?.changes ?? []).slice(0, 6), [diff])
  if (sameEnv) return null

  return (
    <section aria-label="What will move" aria-live="polite" className="border-t-2 border-[var(--ink)]">
      <h3 className="nbc-label m-0 pt-2">What moves</h3>

      {loading ? (
        <div className="pt-2" role="status" aria-busy="true" aria-label="Comparing versions">
          <Skeleton className="mb-2 h-4 w-48" />
          <Skeleton className="h-3 w-32" />
        </div>
      ) : failed ? (
        <p className="m-0 pt-2 text-[13px] text-muted">
          Could not compare the two environments. Promoting still copies the pinned version.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 py-2.5 text-[13px]">
            <Tag variant={envVariant(fromEnv)} caps={false}>{fromEnv}</Tag>
            <span className="font-mono text-[13px] font-semibold tabular-nums">{from ? `v${from.version}` : 'nothing pinned'}</span>
            <ArrowRight size={14} strokeWidth={1.5} className="text-muted" aria-hidden="true" />
            <Tag variant={envVariant(toEnv)} caps={false}>{toEnv}</Tag>
            <span className="font-mono text-[13px] tabular-nums text-muted">{to ? `v${to.version}` : 'nothing pinned'}</span>
          </div>

          {!from && (
            <p className="m-0 border-t border-border py-2 text-[13px] text-warning-fg">
              Nothing is pinned to {fromEnv}, so there is nothing to copy. Create a checkpoint first.
            </p>
          )}
          {from && !to && (
            <p className="m-0 border-t border-border py-2 text-[13px] text-muted">
              First promote: {toEnv} has no version yet, so everything in v{from.version} is new there.
            </p>
          )}
          {from && to && from.version === to.version && (
            <p className="m-0 border-t border-border py-2 text-[13px] text-muted">
              Both environments already point at v{from.version}. Nothing will change.
            </p>
          )}

          {diff && (
            <>
              <p className="nbc-meta m-0 border-t border-border py-2">
                {diff.total === 0 ? 'The specs are identical.' : describeBreakdown(diff)}
              </p>
              {rows.length > 0 && (
                <ul className="m-0 list-none p-0">
                  {rows.map(c => (
                    <li key={`${c.kind}:${c.id}`} className="flex items-center gap-3 border-t border-border py-1.5 text-[13px]">
                      <span className="nbc-label w-16 shrink-0">{c.kind === 'board' ? 'setting' : c.kind}</span>
                      <span className="min-w-0 flex-1 truncate text-fg">{c.label}</span>
                    </li>
                  ))}
                  {diff.total > rows.length && (
                    <li className="nbc-meta border-t border-border py-1.5">and {diff.total - rows.length} more</li>
                  )}
                </ul>
              )}
            </>
          )}

          {kind === 'flow' && (
            <p className="nbc-meta m-0 border-t border-border py-2 !whitespace-normal">Incremental watermarks of materialized tasks are copied best-effort.</p>
          )}
          {kind === 'board' && (
            <p className="nbc-meta m-0 border-t border-border py-2 !whitespace-normal">Queries referenced by the pinned board version are promoted too.</p>
          )}
        </>
      )}
    </section>
  )
}
