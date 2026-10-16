/**
 * VersionTimeline — version history for a versioned resource
 * (flow / board / query), newest first.
 *
 * Reads as a ruled ledger, not a stack of cards: a 2px ink rule opens the list,
 * each version is one hairline-separated row, and the rail is a square node per
 * version (filled when an environment points at it). Mono carries the data:
 * `v12`, the short content hash, the author and the time.
 *
 *   ■ v12  a1b2c3d  [prod]            Added the regional split      👁 ↺ 🚀
 *     priya · 2026-10-14 09:12
 *
 * Per-row actions (icon buttons, labelled, with tooltips):
 *   - View (optional, when onView is given): load that version read-only.
 *   - Restore: write the version's config back into the draft.
 *   - Promote: open the promote flow seeded from the row's pinned env.
 *
 * Presentational only — data + mutations live in the host
 * (VersionHistoryDialog).
 *
 * Props:
 *   versions  {Array<{id, version, config_hash?, message, created_by, created_at}>}
 *   pointers  {Array<{environment_id, env_key, version_id}>}
 *   restoring {number|null}  version number with a restore in flight
 *   viewing   {number|null}  version number with a View fetch in flight
 *   onView    {(v) => void}?  optional — omits the View action when absent
 *   onRestore {(v) => void}
 *   onPromote {(envKey: string) => void}
 */

import { Eye, Rocket, RotateCcw } from 'lucide-react'
import Tag from '../ui/Tag.jsx'
import Tooltip from '../ui/Tooltip.jsx'
import Spinner from '../ui/Spinner.jsx'
import '../../dashboards/widgets/chrome.css'

/** Env-pointer Tag variant: production reads as live, the rest stay quiet. */
function envVariant(key) {
  if (key === 'prod') return 'success'
  if (key === 'dev') return 'neutral'
  return 'accent'
}

/** A user id is not an author name: show its first segment, as mono data. */
function author(v) {
  const id = v?.created_by
  if (!id) return ''
  return /^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(String(id)) ? String(id).slice(0, 8) : String(id)
}

function stamp(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  // ISO-style minute stamp: sortable, locale-free, reads as data.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} `
    + `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function VersionTimeline({
  versions,
  pointers,
  restoring = null,
  viewing = null,
  onView = undefined,
  onRestore,
  onPromote,
}) {
  const chipsFor = (versionId) =>
    pointers.filter(p => p.version_id === versionId)

  const busy = restoring !== null || viewing !== null

  return (
    <ol className="m-0 list-none border-t-2 border-[var(--ink)] p-0" aria-label="Versions, newest first">
      {versions.map((v) => {
        const chips = chipsFor(v.id)
        const pinned = chips.length > 0
        return (
          <li key={v.id} className="nbc-row !items-start !gap-3 !py-3">
            {/* Rail node: a filled square when an environment points here. */}
            <span
              aria-hidden="true"
              className={[
                'mt-[5px] h-2 w-2 shrink-0 border border-[var(--ink)]',
                pinned ? 'bg-[var(--ink)]' : 'bg-surface',
              ].join(' ')}
            />

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <span className="font-mono text-[13px] font-semibold tabular-nums text-fg">v{v.version}</span>
                {v.config_hash && (
                  <span className="nbc-meta" title={`Content hash ${v.config_hash}`}>
                    {String(v.config_hash).slice(0, 7)}
                  </span>
                )}
                {chips.map(p => (
                  <Tag
                    key={p.environment_id}
                    variant={envVariant(p.env_key)}
                    dot={p.env_key === 'prod'}
                    caps={false}
                    title={`Pinned to ${p.env_key}`}
                  >
                    {p.env_key}
                  </Tag>
                ))}
              </div>
              <p className="m-0 mt-1 truncate text-[13px] text-fg">
                {v.message || <span className="text-muted">No message</span>}
              </p>
              <p className="nbc-meta m-0 mt-0.5 truncate">
                {author(v) ? `${author(v)} · ` : ''}{stamp(v.created_at)}
              </p>
            </div>

            {/* Row actions */}
            <div className="flex shrink-0 items-center gap-0.5">
              {onView && (
                <Tooltip content={`View v${v.version} read-only`} side="top">
                  <button
                    type="button"
                    onClick={() => onView(v)}
                    disabled={busy}
                    aria-label={`View version ${v.version} read-only`}
                    className="nbc-tool !px-0"
                  >
                    {viewing === v.version ? <Spinner size="xs" /> : <Eye size={16} strokeWidth={1.5} />}
                  </button>
                </Tooltip>
              )}
              <Tooltip content="Restore into the draft" side="top">
                <button
                  type="button"
                  onClick={() => onRestore(v)}
                  disabled={busy}
                  aria-label={`Restore version ${v.version} into the draft`}
                  className="nbc-tool !px-0"
                >
                  {restoring === v.version ? <Spinner size="xs" /> : <RotateCcw size={16} strokeWidth={1.5} />}
                </button>
              </Tooltip>
              <Tooltip content="Promote between environments" side="top">
                <button
                  type="button"
                  onClick={() => onPromote(chips[0]?.env_key ?? 'dev')}
                  disabled={busy}
                  aria-label={`Promote version ${v.version}`}
                  className="nbc-tool !px-0"
                >
                  <Rocket size={16} strokeWidth={1.5} />
                </button>
              </Tooltip>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
