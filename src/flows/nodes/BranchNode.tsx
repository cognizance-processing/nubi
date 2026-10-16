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
import Tag from '../../components/ui/Tag.jsx'
import {
  StateDot, STATE_LABEL, FAILURE_STATES, WARNING_STATES, HANDLE_CLASS, nodeClass, fmtDuration,
} from './nodeChrome.jsx'

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
        'fn-meta absolute whitespace-nowrap select-none pointer-events-none',
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

  const isFailure  = !!state && FAILURE_STATES.has(state)
  const isWarning  = !!state && WARNING_STATES.has(state)

  // Which branch was taken (from completed run result)
  const branchTaken = taskRun?.result?.branch_taken ?? null
  const durationLabel = fmtDuration(taskRun?.duration_s)

  return (
    <div className="relative" style={{ width: 160, height: 100 }}>
      {/* ── Diamond background: a rotated square keeps the handles on the cardinal points ── */}
      <div
        className={nodeClass({ selected, failure: isFailure, warning: isWarning })}
        style={{
          position: 'absolute', inset: 0, minWidth: 0, maxWidth: 'none',
          transform: 'rotate(45deg)', transformOrigin: 'center', borderRadius: 2,
        }}
      />

      {/* ── Content (not rotated, stays readable) ─────────────────────── */}
      <div className="absolute inset-0 flex select-none flex-col items-center justify-center px-3" style={{ zIndex: 1 }}>
        <div className="flex w-full items-center justify-center gap-1.5">
          <span className="fn-key max-w-[100px] text-center" style={{ fontSize: 12 }}>{task?.key ?? '(branch)'}</span>
          <StateDot state={state} className="shrink-0" />
        </div>

        <div className="mt-0.5 flex items-center gap-1">
          <Tag><GitMerge size={10} aria-hidden="true" /> branch</Tag>
          {conditions.length > 0 && (
            <span className="fn-meta">{conditions.length} cond{conditions.length !== 1 ? 's' : ''}</span>
          )}
        </div>

        {/* Branch taken (run result) */}
        {branchTaken && <p className="fn-meta m-0 mt-0.5 max-w-full truncate" style={{ color: 'var(--text)' }}>took: {branchTaken}</p>}

        {/* State label */}
        {state && state !== 'pending' && !branchTaken && (
          <span className="fn-meta mt-0.5" style={isFailure ? { color: 'var(--danger-fg)' } : isWarning ? { color: 'var(--warning-fg)' } : undefined}>
            {STATE_LABEL[state]}
          </span>
        )}

        {durationLabel && <p className="fn-meta m-0">{durationLabel}</p>}

        {isFailure && taskRun?.error && (
          <p className="fn-err m-0 max-w-full" title={taskRun.error}>
            {taskRun.error.slice(0, 30)}{taskRun.error.length > 30 ? '…' : ''}
          </p>
        )}
      </div>

      {/* ── Handles ────────────────────────────────────────────────────────── */}

      {/* Target (top) — incoming upstream dependency */}
      <Handle type="target" position={Position.Top} className={HANDLE_CLASS} style={{ zIndex: 2 }} />

      {/* Source: "then" — left side (condition_0) */}
      <div style={{ position: 'absolute', left: 0, top: '50%', transform: 'translateY(-50%)', zIndex: 2 }}>
        <Handle type="source" position={Position.Left} id="then" className={`${HANDLE_CLASS} fn-handle-branch`} />
        <HandleLabel label={conditions[0] ? condLabel(conditions[0].when, 0) : 'then'} side="left" />
      </div>

      {/* Source: "else" — right side (condition_1 or else) */}
      <div style={{ position: 'absolute', right: 0, top: '50%', transform: 'translateY(-50%)', zIndex: 2 }}>
        <Handle type="source" position={Position.Right} id="else" className={`${HANDLE_CLASS} fn-handle-branch`} />
        <HandleLabel label={conditions[1] ? condLabel(conditions[1].when, 1) : 'else'} side="right" />
      </div>

      {/* Source: default — bottom */}
      {hasDefault && (
        <div style={{ position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)', zIndex: 2 }}>
          <Handle type="source" position={Position.Bottom} id="default" className={HANDLE_CLASS} />
          <HandleLabel label="default" side="bottom" />
        </div>
      )}

      {/* Source: fallback single bottom handle (when no default configured) */}
      {!hasDefault && <Handle type="source" position={Position.Bottom} className={HANDLE_CLASS} style={{ zIndex: 2 }} />}
    </div>
  )
}

export default memo(BranchNode)
