/**
 * VersionTimeline — vertical version rail for a versioned resource
 * (flow / board / query), newest first.
 *
 * Each row: a rail node (dot + connecting line — the parent chain is implied
 * by the order), `vN`, the checkpoint message, author/date, env-pointer
 * chips, plus per-row actions:
 *   - View (optional, when onView is given): load that version read-only.
 *   - Restore: write the version's config back into the draft.
 *   - Promote: open the promote flow seeded from the row's pinned env.
 *
 * Presentational only — data + mutations live in the host
 * (VersionHistoryDialog).
 *
 * Props:
 *   versions  {Array<{id, version, message, created_by, created_at}>}
 *   pointers  {Array<{environment_id, env_key, version_id}>}
 *   restoring {number|null}  version number with a restore in flight
 *   viewing   {number|null}  version number with a View fetch in flight
 *   onView    {(v) => void}?  optional — omits the View action when absent
 *   onRestore {(v) => void}
 *   onPromote {(envKey: string) => void}
 */

import { Eye, Loader2, Rocket, RotateCcw } from 'lucide-react'

/** Env-pointer chip accent (matches envDotClass semantics). */
function envChipClass(key) {
  if (key === 'prod') return 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
  if (key === 'dev') return 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30'
  return 'bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/30'
}

export default function VersionTimeline({
  versions,
  pointers,
  restoring = null,
  viewing = null,
  onView,
  onRestore,
  onPromote,
}) {
  const chipsFor = (versionId) =>
    pointers.filter(p => p.version_id === versionId)

  const busy = restoring !== null || viewing !== null

  return (
    <ol className="relative">
      {versions.map((v, idx) => {
        const chips = chipsFor(v.id)
        const isLast = idx === versions.length - 1
        return (
          <li key={v.id} className="relative flex gap-3 pb-1.5">
            {/* Rail: node dot + connector down to the (older) parent */}
            <div className="relative flex flex-col items-center w-4 shrink-0" aria-hidden="true">
              <span
                className={[
                  'mt-4 w-2.5 h-2.5 rounded-full border-2 shrink-0 z-10',
                  chips.length > 0
                    ? 'bg-primary border-primary'
                    : 'bg-surface border-border',
                ].join(' ')}
              />
              {!isLast && (
                <span className="absolute top-6 bottom-[-0.875rem] w-px bg-border" />
              )}
            </div>

            {/* Row card */}
            <div className="flex-1 min-w-0 flex items-center gap-3 px-3 py-2.5 rounded-xl border border-border bg-surface hover:bg-surface-2/60 transition-colors">
              <span className="shrink-0 w-10 text-xs font-mono font-semibold text-fg">
                v{v.version}
              </span>

              <div className="flex-1 min-w-0">
                <p className="text-xs text-fg truncate">
                  {v.message || <span className="text-muted italic">No message</span>}
                </p>
                <p className="text-[10px] text-muted mt-0.5 truncate">
                  {v.created_by ? `${v.created_by} · ` : ''}
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
                {onView && (
                  <button
                    type="button"
                    onClick={() => onView(v)}
                    disabled={busy}
                    title={`View v${v.version} read-only`}
                    className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40"
                  >
                    {viewing === v.version
                      ? <Loader2 size={13} className="animate-spin" />
                      : <Eye size={13} />}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onRestore(v)}
                  disabled={busy}
                  title={`Restore v${v.version} into the draft`}
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40"
                >
                  {restoring === v.version
                    ? <Loader2 size={13} className="animate-spin" />
                    : <RotateCcw size={13} />}
                </button>
                <button
                  type="button"
                  onClick={() => onPromote(chips[0]?.env_key ?? 'dev')}
                  disabled={busy}
                  title="Promote between environments"
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-40"
                >
                  <Rocket size={13} />
                </button>
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
