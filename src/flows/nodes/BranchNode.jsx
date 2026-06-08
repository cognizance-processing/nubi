/**
 * BranchNode.jsx — custom React Flow node for a branch (conditional routing) task.
 *
 * Renders as a diamond-shaped node with:
 *   - Task key (title) + "branch" kind badge
 *   - Condition count badge (e.g. "2 conditions")
 *   - Labeled outgoing handles: one per condition (right side) + one default (bottom)
 *   - Target handle on the top (upstream dependency)
 *   - Status dot colored by task_run.state
 *   - branch_taken label when a run has completed
 *
 * Handle layout:
 *   - Top handle:    target (incoming from upstream)
 *   - Left side:     source labeled "then" (condition_0 / first condition)
 *   - Right side:    source labeled "else" / additional conditions
 *   - Bottom handle: source labeled "default"
 *
 * Per blueprint §4.2: edges from branch node to downstream tasks carry labels
 * derived from conditions[i].when (truncated) or "default". The handles below
 * match the spec: one source handle per condition + one for default.
 *
 * For simplicity in the React Flow handle model (which requires static handles
 * at render time), we expose four named handles:
 *   "then"    — condition_0 branch
 *   "else"    — condition_1 branch (second condition)
 *   "cond2"   — condition_2 (third condition, shown when present)
 *   "default" — default branch
 *
 * The specGraph.js agent (Agent F) is responsible for wiring these handles
 * to labeled edges. This file only renders the node shape and handles.
 */

import { memo } from 'react'
import { Handle, Position } from 'reactflow'
import { GitMerge } from 'lucide-react'

// ---------------------------------------------------------------------------
// Color constants
// ---------------------------------------------------------------------------

const KIND_BG     = 'bg-amber-500/10'
const KIND_TEXT   = 'text-amber-600 dark:text-amber-400'
const KIND_BORDER = 'border-amber-200 dark:border-amber-800'

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

const FAILURE_STATES = new Set(['failed', 'timed_out'])
const WARNING_STATES = new Set(['upstream_failed', 'retrying'])

// ---------------------------------------------------------------------------
// Truncate a 'when' expression for display as a handle label
// ---------------------------------------------------------------------------

function condLabel(when, index) {
  if (!when) return `cond ${index}`
  // Strip outer {{ }} if present
  const cleaned = when.replace(/^\s*\{\{/, '').replace(/\}\}\s*$/, '').trim()
  return cleaned.length > 18 ? cleaned.slice(0, 16) + '…' : cleaned
}

// ---------------------------------------------------------------------------
// HandleLabel — small floating label beside a source handle
// ---------------------------------------------------------------------------

function HandleLabel({ label, side = 'left' }) {
  return (
    <span
      className={[
        'absolute text-[8px] font-semibold text-muted/70 font-mono whitespace-nowrap select-none pointer-events-none',
        side === 'left'  ? '-translate-x-full -left-2 top-1/2 -translate-y-1/2 pr-1'  : '',
        side === 'right' ? 'left-2 top-1/2 -translate-y-1/2 pl-1' : '',
        side === 'bottom' ? 'top-2 left-1/2 -translate-x-1/2' : '',
      ].join(' ')}
    >
      {label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// BranchNode
// ---------------------------------------------------------------------------

function BranchNode({ data, selected }) {
  const { task, taskRun } = data
  const config     = task?.config ?? {}
  const conditions = config.conditions ?? []
  const hasDefault = (config.default ?? []).length > 0
  const state      = taskRun?.state ?? null

  const dotCls    = state ? (STATE_DOT[state] ?? 'bg-slate-400') : null
  const isFailure  = state && FAILURE_STATES.has(state)
  const isWarning  = state && WARNING_STATES.has(state)

  // Which branch was taken (from completed run result)
  const branchTaken = taskRun?.result?.branch_taken ?? null

  // Duration label
  const durationLabel = taskRun?.duration_s != null
    ? taskRun.duration_s < 1
      ? `${Math.round(taskRun.duration_s * 1000)} ms`
      : `${taskRun.duration_s.toFixed(1)} s`
    : null

  return (
    <div
      className={[
        // Diamond shape via clip-path rotation trick:
        // We use a rotated square outer container + inner counter-rotated content.
        // This keeps React Flow handles on the cardinal points of the diamond.
        'relative',
      ].join(' ')}
      style={{ width: 160, height: 100 }}
    >
      {/* ── Diamond background ────────────────────────────────────────────── */}
      <div
        className={[
          'absolute inset-0 rounded-lg border-2 bg-surface shadow-md transition-all duration-150',
          // top accent via border color
          isFailure
            ? 'border-red-400/70 shadow-lg shadow-red-500/10'
            : isWarning
            ? 'border-orange-400/60 shadow-lg shadow-orange-500/10'
            : selected
            ? 'border-primary/70 shadow-lg shadow-primary/10'
            : 'border-amber-300/70 dark:border-amber-700/70 hover:border-amber-400/80 hover:shadow-lg',
        ].join(' ')}
        style={{
          transform: 'rotate(45deg)',
          transformOrigin: 'center',
          borderRadius: '8px',
        }}
      />

      {/* ── Content (counter-rotated to stay readable) ─────────────────── */}
      <div
        className="absolute inset-0 flex flex-col items-center justify-center px-3 select-none"
        style={{ zIndex: 1 }}
      >
        {/* Header: key + status dot */}
        <div className="flex items-center justify-center gap-1.5 w-full">
          <span className="text-xs font-semibold text-fg truncate text-center font-mono max-w-[100px]">
            {task?.key ?? '(branch)'}
          </span>
          {dotCls && (
            <span
              className={['w-2 h-2 rounded-full shrink-0', dotCls].join(' ')}
              title={STATE_LABEL[state]}
            />
          )}
        </div>

        {/* Kind badge */}
        <div className="flex items-center gap-1 mt-0.5">
          <span
            className={[
              'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md text-[9px] font-semibold uppercase tracking-wide border',
              KIND_BG, KIND_TEXT, KIND_BORDER,
            ].join(' ')}
          >
            <GitMerge size={8} />
            branch
          </span>
          {conditions.length > 0 && (
            <span className="text-[8px] text-muted/60 font-mono">
              {conditions.length} cond{conditions.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Branch taken (run result) */}
        {branchTaken && (
          <p className="mt-0.5 text-[8px] font-mono text-amber-600 dark:text-amber-400 truncate max-w-full">
            took: {branchTaken}
          </p>
        )}

        {/* State label */}
        {state && state !== 'pending' && !branchTaken && (
          <span className={[
            'text-[9px] font-medium mt-0.5',
            isFailure ? 'text-red-600 dark:text-red-400' : isWarning ? 'text-orange-600 dark:text-orange-400' : 'text-muted',
          ].join(' ')}>
            {STATE_LABEL[state]}
          </span>
        )}

        {/* Duration */}
        {durationLabel && (
          <p className="text-[8px] text-muted/60 font-mono">
            {durationLabel}
          </p>
        )}

        {/* Error excerpt */}
        {isFailure && taskRun?.error && (
          <p className="text-[8px] text-red-600 dark:text-red-400 font-mono truncate max-w-full" title={taskRun.error}>
            {taskRun.error.slice(0, 30)}{taskRun.error.length > 30 ? '…' : ''}
          </p>
        )}
      </div>

      {/* ── Handles ────────────────────────────────────────────────────────── */}

      {/* Target (top) — incoming upstream dependency */}
      <Handle
        type="target"
        position={Position.Top}
        className="!w-3.5 !h-3.5 !bg-surface !border-2 !border-border hover:!border-primary transition-colors"
        style={{ zIndex: 2 }}
      />

      {/* Source: "then" — left side (condition_0) */}
      <div className="relative" style={{ position: 'absolute', left: 0, top: '50%', transform: 'translateY(-50%)', zIndex: 2 }}>
        <Handle
          type="source"
          position={Position.Left}
          id="then"
          className="!w-3.5 !h-3.5 !bg-surface !border-2 !border-amber-400 hover:!border-amber-500 transition-colors"
        />
        <HandleLabel
          label={conditions[0] ? condLabel(conditions[0].when, 0) : 'then'}
          side="left"
        />
      </div>

      {/* Source: "else" — right side (condition_1 or else) */}
      <div className="relative" style={{ position: 'absolute', right: 0, top: '50%', transform: 'translateY(-50%)', zIndex: 2 }}>
        <Handle
          type="source"
          position={Position.Right}
          id="else"
          className="!w-3.5 !h-3.5 !bg-surface !border-2 !border-amber-400 hover:!border-amber-500 transition-colors"
        />
        <HandleLabel
          label={conditions[1] ? condLabel(conditions[1].when, 1) : 'else'}
          side="right"
        />
      </div>

      {/* Source: default — bottom */}
      {hasDefault && (
        <div className="relative" style={{ position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)', zIndex: 2 }}>
          <Handle
            type="source"
            position={Position.Bottom}
            id="default"
            className="!w-3.5 !h-3.5 !bg-surface !border-2 !border-slate-400 hover:!border-slate-500 transition-colors"
          />
          <HandleLabel label="default" side="bottom" />
        </div>
      )}

      {/* Source: fallback single bottom handle (when no default configured) */}
      {!hasDefault && (
        <Handle
          type="source"
          position={Position.Bottom}
          className="!w-3.5 !h-3.5 !bg-surface !border-2 !border-border hover:!border-primary transition-colors"
          style={{ zIndex: 2 }}
        />
      )}
    </div>
  )
}

export default memo(BranchNode)
