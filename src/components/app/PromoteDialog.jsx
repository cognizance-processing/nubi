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
 */

import { useEffect, useState } from 'react'
import { ArrowRight, CheckCircle2, Loader2, Rocket, X } from 'lucide-react'

import { promote } from '../../lib/versions.js'

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
  const envs = Array.isArray(environments) && environments.length > 0 ? environments : FALLBACK_ENVS
  const [fromEnv, setFromEnv] = useState(defaultFrom)
  const [toEnv, setToEnv] = useState(defaultTo)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [promoted, setPromoted] = useState(null) // result list after success

  // Reset whenever the dialog is (re)opened for a resource.
  useEffect(() => {
    if (!open) return
    setFromEnv(defaultFrom)
    setToEnv(defaultTo)
    setBusy(false)
    setError(null)
    setPromoted(null)
  }, [open, resourceId, defaultFrom, defaultTo])

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
    } catch (cause) {
      setError(cause?.message || 'Promote failed.')
    } finally {
      setBusy(false)
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
              /* ---- Result: what was promoted ---- */
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
