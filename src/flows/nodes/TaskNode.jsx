/**
 * TaskNode.jsx — custom React Flow node for a flow task.
 *
 * Displays:
 *   - Task key (title)
 *   - Kind badge (query | python | agent | noop)
 *   - Status dot colored by task_run.state
 *   - Attempt count (when retrying or attempt > 0)
 *   - Duration (when finished)
 *   - Error excerpt (when failed/timed_out)
 *   - Source (bottom) and Target (top) handles
 *
 * State colors:
 *   pending         → slate
 *   ready           → blue
 *   running         → amber (pulse)
 *   retrying        → orange (pulse)
 *   success         → green
 *   failed          → red (ring)
 *   timed_out       → red (ring)
 *   upstream_failed → orange
 *   cancelled       → gray
 */

import { memo } from 'react'
import { Handle, Position } from 'reactflow'

// ---------------------------------------------------------------------------
// Color maps
// ---------------------------------------------------------------------------

const KIND_COLORS = {
  query:       { bg: 'bg-blue-500/10',    text: 'text-blue-600 dark:text-blue-400',       border: 'border-blue-200 dark:border-blue-800'       },
  python:      { bg: 'bg-violet-500/10',  text: 'text-violet-600 dark:text-violet-400',   border: 'border-violet-200 dark:border-violet-800'   },
  agent:       { bg: 'bg-emerald-500/10', text: 'text-emerald-600 dark:text-emerald-400', border: 'border-emerald-200 dark:border-emerald-800' },
  materialize: { bg: 'bg-amber-500/10',   text: 'text-amber-600 dark:text-amber-400',     border: 'border-amber-200 dark:border-amber-800'     },
  extract:     { bg: 'bg-sky-500/10',     text: 'text-sky-600 dark:text-sky-400',         border: 'border-sky-200 dark:border-sky-800'         },
  bucket_load: { bg: 'bg-orange-500/10',  text: 'text-orange-600 dark:text-orange-400',   border: 'border-orange-200 dark:border-orange-800'   },
  noop:        { bg: 'bg-slate-500/10',   text: 'text-slate-600 dark:text-slate-400',     border: 'border-slate-200 dark:border-slate-800'     },
}

const KIND_ACCENT = {
  query:       'border-t-blue-400',
  python:      'border-t-violet-400',
  agent:       'border-t-emerald-400',
  materialize: 'border-t-amber-400',
  extract:     'border-t-sky-400',
  bucket_load: 'border-t-orange-400',
  noop:        'border-t-slate-400',
}

const STATE_DOT = {
  pending:         'bg-slate-400',
  ready:           'bg-blue-500',
  running:         'bg-amber-400 animate-pulse',
  retrying:        'bg-orange-500 animate-pulse',
  success:         'bg-green-500',
  failed:          'bg-red-500',
  timed_out:       'bg-red-500',
  upstream_failed: 'bg-orange-400',
  cancelled:       'bg-gray-400',
}

const STATE_LABEL = {
  pending:         'pending',
  ready:           'ready',
  running:         'running…',
  retrying:        'retrying…',
  success:         'success',
  failed:          'failed',
  timed_out:       'timed out',
  upstream_failed: 'upstream failed',
  cancelled:       'cancelled',
}

// States that show a red error ring on the node.
const FAILURE_STATES = new Set(['failed', 'timed_out'])
// States that show an orange warning ring.
const WARNING_STATES = new Set(['upstream_failed', 'retrying'])

// ---------------------------------------------------------------------------
// TaskNode
// ---------------------------------------------------------------------------

function TaskNode({ data, selected }) {
  const { task, taskRun } = data
  const kind = task?.kind ?? 'noop'
  const state = taskRun?.state ?? null
  const kc = KIND_COLORS[kind] ?? KIND_COLORS.noop
  const accent = KIND_ACCENT[kind] ?? KIND_ACCENT.noop
  const dotCls = state ? (STATE_DOT[state] ?? 'bg-slate-400') : null

  const isFailure = state && FAILURE_STATES.has(state)
  const isWarning = state && WARNING_STATES.has(state)

  // Duration label
  const durationLabel = taskRun?.duration_s != null
    ? taskRun.duration_s < 1
      ? `${Math.round(taskRun.duration_s * 1000)} ms`
      : `${taskRun.duration_s.toFixed(1)} s`
    : null

  return (
    <div
      className={[
        'relative rounded-xl border-2 bg-surface shadow-md transition-all duration-150',
        'min-w-[180px] max-w-[220px]',
        // top accent stripe
        'border-t-4',
        accent,
        // state-specific ring / border override
        isFailure
          ? 'border-red-400/70 shadow-lg shadow-red-500/10'
          : isWarning
          ? 'border-orange-400/60 shadow-lg shadow-orange-500/10'
          : selected
          ? 'border-primary/70 shadow-lg shadow-primary/10'
          : 'border-border hover:border-border/80 hover:shadow-lg',
      ].join(' ')}
    >
      {/* Target handle (top) */}
      <Handle
        type="target"
        position={Position.Top}
        className="!w-4 !h-4 !bg-surface !border-2 !border-border hover:!border-primary transition-colors"
      />

      {/* Node body */}
      <div className="px-3 py-2.5 select-none">
        {/* Header row: key + status dot */}
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <span className="text-sm font-semibold text-fg truncate flex-1 font-mono">
            {task?.key ?? '(untitled)'}
          </span>
          {dotCls && (
            <span
              className={['w-2.5 h-2.5 rounded-full shrink-0', dotCls].join(' ')}
              title={STATE_LABEL[state]}
            />
          )}
        </div>

        {/* Kind badge + state label */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span
            className={[
              'inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide border',
              kc.bg, kc.text, kc.border,
            ].join(' ')}
          >
            {kind}
          </span>

          {/* State label (only when a run is active) */}
          {state && state !== 'pending' && (
            <span className={[
              'text-[10px] font-medium',
              isFailure ? 'text-red-600 dark:text-red-400' : isWarning ? 'text-orange-600 dark:text-orange-400' : 'text-muted',
            ].join(' ')}>
              {STATE_LABEL[state]}
            </span>
          )}
        </div>

        {/* Attempt count (retrying or failed with attempt > 0) */}
        {taskRun && (taskRun.attempt ?? 0) > 0 && (
          <p className="mt-1 text-[9px] text-orange-600 dark:text-orange-400 font-mono">
            attempt {(taskRun.attempt ?? 0) + 1}
          </p>
        )}

        {/* Duration */}
        {durationLabel && (
          <p className="mt-0.5 text-[9px] text-muted/60 font-mono">
            {durationLabel}
          </p>
        )}

        {/* Error excerpt */}
        {isFailure && taskRun?.error && (
          <p className="mt-1 text-[9px] text-red-600 dark:text-red-400 font-mono truncate max-w-full" title={taskRun.error}>
            {taskRun.error.slice(0, 60)}{taskRun.error.length > 60 ? '…' : ''}
          </p>
        )}

        {/* Retries hint (from spec) */}
        {!taskRun && (task?.retries ?? 0) > 0 && (
          <p className="mt-1 text-[9px] text-muted/60 font-mono">
            retries: {task.retries}
          </p>
        )}
      </div>

      {/* Source handle (bottom) */}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-4 !h-4 !bg-surface !border-2 !border-border hover:!border-primary transition-colors"
      />
    </div>
  )
}

export default memo(TaskNode)
