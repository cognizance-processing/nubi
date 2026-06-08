/**
 * MapGroupNode.jsx — custom React Flow node for a map (fan-out) task.
 *
 * Renders as a collapsible group container with:
 *   - Task key (title) + "map" kind badge
 *   - Collapsed: shows item_expr snippet + max_concurrency hint
 *   - Expanded: shows body task list as compact pills
 *   - Item count badge when a task_run result is available
 *   - Status dot colored by task_run.state (includes waiting_children)
 *   - Source (bottom) and Target (top) handles
 *
 * State colors mirror TaskNode, plus:
 *   waiting_children → indigo (pulse) — map is running its fan-out
 */

import { memo, useState } from 'react'
import { Handle, Position } from 'reactflow'
import { ChevronDown, ChevronRight, GitBranch } from 'lucide-react'

// ---------------------------------------------------------------------------
// Color constants (align with TaskNode)
// ---------------------------------------------------------------------------

const KIND_BG    = 'bg-indigo-500/10'
const KIND_TEXT  = 'text-indigo-600 dark:text-indigo-400'
const KIND_BORDER = 'border-indigo-200 dark:border-indigo-800'
const ACCENT      = 'border-t-indigo-400'

const STATE_DOT = {
  pending:          'bg-slate-400',
  ready:            'bg-blue-500',
  running:          'bg-amber-400 animate-pulse',
  retrying:         'bg-orange-500 animate-pulse',
  waiting_children: 'bg-indigo-400 animate-pulse',
  success:          'bg-green-500',
  failed:           'bg-red-500',
  timed_out:        'bg-red-500',
  upstream_failed:  'bg-orange-400',
  cancelled:        'bg-gray-400',
}

const STATE_LABEL = {
  pending:          'pending',
  ready:            'ready',
  running:          'running…',
  retrying:         'retrying…',
  waiting_children: 'fan-out…',
  success:          'success',
  failed:           'failed',
  timed_out:        'timed out',
  upstream_failed:  'upstream failed',
  cancelled:        'cancelled',
}

const FAILURE_STATES  = new Set(['failed', 'timed_out'])
const WARNING_STATES  = new Set(['upstream_failed', 'retrying'])

// ---------------------------------------------------------------------------
// MapGroupNode
// ---------------------------------------------------------------------------

function MapGroupNode({ data, selected }) {
  const { task, taskRun } = data
  const config    = task?.config ?? {}
  const bodyTasks = config.body ?? []
  const itemExpr  = config.item_expr ?? ''
  const itemVar   = config.item_var ?? 'item'
  const maxConc   = config.max_concurrency ?? 0
  const state     = taskRun?.state ?? null

  const [expanded, setExpanded] = useState(false)

  // Item count from a completed run
  const itemCount = taskRun?.result?.item_count ?? null

  const dotCls   = state ? (STATE_DOT[state] ?? 'bg-slate-400') : null
  const isFailure = state && FAILURE_STATES.has(state)
  const isWarning = state && WARNING_STATES.has(state)

  // Duration label
  const durationLabel = taskRun?.duration_s != null
    ? taskRun.duration_s < 1
      ? `${Math.round(taskRun.duration_s * 1000)} ms`
      : `${taskRun.duration_s.toFixed(1)} s`
    : null

  // Truncate item_expr for display
  const exprSnippet = itemExpr.length > 40 ? itemExpr.slice(0, 38) + '…' : itemExpr

  return (
    <div
      className={[
        'relative rounded-xl border-2 bg-surface shadow-md transition-all duration-150',
        'min-w-[210px] max-w-[260px]',
        // top accent stripe
        'border-t-4',
        ACCENT,
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
        {/* Header row: key + expand toggle + status dot */}
        <div className="flex items-center justify-between gap-1.5 mb-1.5">
          {/* Expand toggle */}
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded(v => !v) }}
            className="shrink-0 text-muted hover:text-fg transition-colors"
            title={expanded ? 'Collapse body' : 'Expand body'}
            aria-label={expanded ? 'Collapse map body' : 'Expand map body'}
          >
            {expanded
              ? <ChevronDown size={13} />
              : <ChevronRight size={13} />
            }
          </button>

          <span className="text-sm font-semibold text-fg truncate flex-1 font-mono">
            {task?.key ?? '(untitled)'}
          </span>

          {/* Item count badge */}
          {itemCount != null && (
            <span className="shrink-0 px-1.5 py-0.5 rounded-md text-[9px] font-semibold bg-indigo-500/15 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-800 font-mono">
              ×{itemCount}
            </span>
          )}

          {dotCls && (
            <span
              className={['w-2.5 h-2.5 rounded-full shrink-0', dotCls].join(' ')}
              title={STATE_LABEL[state]}
            />
          )}
        </div>

        {/* Kind badge + icon + state label */}
        <div className="flex items-center gap-1.5 flex-wrap mb-1.5">
          <span
            className={[
              'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide border',
              KIND_BG, KIND_TEXT, KIND_BORDER,
            ].join(' ')}
          >
            <GitBranch size={9} />
            map
          </span>

          {state && state !== 'pending' && (
            <span className={[
              'text-[10px] font-medium',
              isFailure
                ? 'text-red-600 dark:text-red-400'
                : isWarning
                ? 'text-orange-600 dark:text-orange-400'
                : state === 'waiting_children'
                ? 'text-indigo-600 dark:text-indigo-400'
                : 'text-muted',
            ].join(' ')}>
              {STATE_LABEL[state]}
            </span>
          )}
        </div>

        {/* item_expr snippet (collapsed view) */}
        {!expanded && itemExpr && (
          <p className="text-[9px] text-muted/70 font-mono truncate mb-1" title={itemExpr}>
            {exprSnippet}
          </p>
        )}

        {/* item_var + max_concurrency hint */}
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-muted/60 font-mono">
            var: <span className="text-fg/70">{itemVar}</span>
          </span>
          {maxConc > 0 && (
            <span className="text-[9px] text-muted/60 font-mono">
              conc: <span className="text-fg/70">{maxConc}</span>
            </span>
          )}
        </div>

        {/* Expanded body: list of body task keys */}
        {expanded && bodyTasks.length > 0 && (
          <div className="mt-2 border-t border-border/60 pt-2 space-y-1">
            <p className="text-[9px] font-semibold text-muted/60 uppercase tracking-wider mb-1">
              Body ({bodyTasks.length} task{bodyTasks.length !== 1 ? 's' : ''})
            </p>
            {bodyTasks.map((bt) => (
              <div
                key={bt.key}
                className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-surface-2/50 border border-border/50"
              >
                <span className="text-[9px] font-mono text-fg/80 truncate flex-1">{bt.key}</span>
                <span className="text-[8px] uppercase tracking-wide text-muted/60 font-semibold shrink-0">{bt.kind}</span>
              </div>
            ))}
          </div>
        )}

        {expanded && bodyTasks.length === 0 && (
          <p className="mt-2 text-[9px] text-muted/50 italic text-center border-t border-border/60 pt-2">
            No body tasks defined
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

export default memo(MapGroupNode)
