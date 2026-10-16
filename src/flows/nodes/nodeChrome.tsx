/**
 * nodeChrome — the shared Swiss chrome for flow-canvas nodes.
 *
 * One node language: a hairline box under a 2px ink rule, a mono key, a mono
 * kind tag and a square status dot. Colour carries state only (the dot and,
 * for failure/warning, the border). Kind is told by the label, never by a
 * rainbow of tints. Selection is the one accent. Styles live in
 * components/app/appPatterns.css (`fn-*`).
 */

import Tag from '../../components/ui/Tag.jsx'
import '../../components/app/appPatterns.css'

/** task_run.state -> status-dot tone (see .nubi-dot-*). */
const STATE_TONE: Record<string, string> = {
  pending: 'muted',
  ready: 'live',
  running: 'warning',
  retrying: 'warning',
  waiting_children: 'live',
  success: 'success',
  failed: 'danger',
  timed_out: 'danger',
  upstream_failed: 'warning',
  cancelled: 'muted',
  skipped: 'muted',
}

export const STATE_LABEL: Record<string, string> = {
  pending: 'pending',
  ready: 'ready',
  running: 'running',
  retrying: 'retrying',
  waiting_children: 'fan-out',
  success: 'success',
  failed: 'failed',
  timed_out: 'timed out',
  upstream_failed: 'upstream failed',
  cancelled: 'cancelled',
  skipped: 'skipped',
}

export const FAILURE_STATES = new Set(['failed', 'timed_out'])
export const WARNING_STATES = new Set(['upstream_failed', 'retrying'])

const PULSING = new Set(['running', 'retrying', 'waiting_children'])

/** Square status dot for a task_run state. */
export function StateDot({ state, className = '' }: { state: string | null | undefined; className?: string }) {
  if (!state) return null
  const tone = STATE_TONE[state] ?? 'muted'
  return (
    <span
      role="img"
      aria-label={STATE_LABEL[state] ?? state}
      title={STATE_LABEL[state] ?? state}
      className={`nubi-dot nubi-dot-${tone} ${PULSING.has(state) ? 'animate-pulse' : ''} ${className}`}
    />
  )
}

/** Class list for the node box, from selection / run state. */
export function nodeClass(opts: { selected?: boolean; failure?: boolean; warning?: boolean; conditional?: boolean }) {
  return [
    'fn-node',
    opts.selected ? 'is-selected' : '',
    opts.failure ? 'is-failed' : '',
    opts.warning ? 'is-warning' : '',
    opts.conditional ? 'has-runwhen' : '',
  ].filter(Boolean).join(' ')
}

/** Kind label: a neutral mono tag (the kind name is data). */
export function KindTag({ children, icon }: { children: React.ReactNode; icon?: React.ReactNode }) {
  return <Tag>{icon}{children}</Tag>
}

export const HANDLE_CLASS = 'fn-handle'

/** "123 ms" / "1.2 s" or null. */
export function fmtDuration(seconds: number | null | undefined) {
  if (seconds == null) return null
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`
}
